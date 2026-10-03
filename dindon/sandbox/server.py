"""Internal HTTP service for bounded commands in disposable workspaces."""

from __future__ import annotations

import base64
import difflib
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
import zipfile

_MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
_MAX_EXPANDED_BYTES = 128 * 1024 * 1024
_MAX_FILES = 10_000
_MAX_COMMAND_BYTES = 2_048
_MAX_OUTPUT_BYTES = 64 * 1024
_COMMAND_TIMEOUT_S = 30
_MAX_DIFF_BYTES = 64 * 1024
_MAX_APPLY_CONTENT_BYTES = 64 * 1024
_MAX_CHANGED_FILES = 1_000
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "data", "models"}
_SENSITIVE_NAMES = (
    "secret", "credential", "password", "token", "private-key", "id_rsa", "id_ed25519", "keystore", ".pem"
)


class RequestError(ValueError):
    pass


def _decode_header(value: str | None, *, max_bytes: int) -> str:
    if not value:
        raise RequestError("required request header is missing")
    try:
        result = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise RequestError("request header is not valid UTF-8 base64") from exc
    if not result or len(result.encode("utf-8")) > max_bytes or "\x00" in result:
        raise RequestError("request header value is invalid or too long")
    return result


def _extract_workspace(archive_bytes: bytes, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except (zipfile.BadZipFile, OSError) as exc:
        raise RequestError("workspace snapshot is not a valid zip archive") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > _MAX_FILES:
            raise RequestError("workspace snapshot contains too many entries")
        expanded_size = sum(info.file_size for info in infos)
        if expanded_size > _MAX_EXPANDED_BYTES:
            raise RequestError("workspace snapshot exceeds the extraction limit")
        for info in infos:
            relative = PurePosixPath(info.filename)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts)
            ):
                raise RequestError("workspace snapshot contains a disallowed path")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if info.is_dir():
                target.mkdir(exist_ok=True)
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise RequestError("workspace snapshot contains a symlink")
            if not info.file_size:
                target.touch()
                continue
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=64 * 1024)
            target.chmod(0o600)


def _bounded_process(command: str, cwd: Path, home: Path) -> dict[str, object]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    process = subprocess.Popen(
        ["/bin/sh", "-c", command],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    truncated = False
    timed_out = False
    deadline = time.monotonic() + _COMMAND_TIMEOUT_S
    while selector.get_map() or process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 1
        if not selector.get_map():
            time.sleep(0.05)
            continue
        for key, _ in selector.select(timeout=max(0.0, min(0.2, remaining))):
            chunk = os.read(key.fileobj.fileno(), 8 * 1024)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            output = outputs[key.data]
            remaining_bytes = _MAX_OUTPUT_BYTES - len(output)
            if remaining_bytes > 0:
                output.extend(chunk[:remaining_bytes])
            if len(chunk) > remaining_bytes:
                truncated = True
    selector.close()
    return_code = process.wait(timeout=2)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return {
        "exit_code": return_code,
        "stdout": bytes(outputs["stdout"]).decode("utf-8", errors="replace"),
        "stderr": bytes(outputs["stderr"]).decode("utf-8", errors="replace"),
        "timed_out": timed_out,
        "truncated": truncated,
    }


def _is_visible(relative: PurePosixPath) -> bool:
    return not any(
        part.startswith(".")
        or not part.isprintable()
        or any(word in part.casefold() for word in _SENSITIVE_NAMES)
        for part in relative.parts
    )


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_changes(archive_bytes: bytes, root: Path) -> dict[str, object]:
    """Create a bounded, text-only diff from the initial snapshot to command output."""
    from dindon.tools.workspace import redact_known_secrets

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        original = {
            PurePosixPath(info.filename).as_posix(): info
            for info in archive.infolist()
            if not info.is_dir() and _is_visible(PurePosixPath(info.filename))
        }
        current: dict[str, Path] = {}
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not name.startswith(".")
                and name.casefold() not in _SKIP_DIRS
                and not (parent / name).is_symlink()
            )
            for filename in filenames:
                path = parent / filename
                relative = PurePosixPath(path.relative_to(root).as_posix())
                if path.is_symlink() or not _is_visible(relative) or not path.is_file():
                    continue
                current[relative.as_posix()] = path

        changes: list[dict[str, object]] = []
        diff_parts: list[str] = []
        diff_bytes = 0
        apply_content_bytes = 0
        diff_truncated = False
        for name in sorted(set(original) | set(current)):
            before_info = original.get(name)
            after_path = current.get(name)
            before_size = 0 if before_info is None else before_info.file_size
            after_size = 0 if after_path is None else after_path.stat().st_size
            if before_info is not None and after_path is not None:
                with archive.open(before_info) as source:
                    before_digest = hashlib.sha256(source.read()).digest()
                if before_size == after_size:
                    try:
                        if before_digest.hex() == _file_hash(after_path):
                            continue
                    except OSError:
                        pass
                status = "modified"
            elif before_info is None:
                status = "added"
            else:
                status = "deleted"
            if len(changes) < _MAX_CHANGED_FILES:
                change: dict[str, object] = {"path": name, "status": status, "applyable": False}
                changes.append(change)
            else:
                diff_truncated = True
                continue

            if max(before_size, after_size) > 64 * 1024:
                change["detail"] = "file exceeds text diff limit"
                continue
            try:
                before_bytes = b"" if before_info is None else archive.read(before_info)
                after_bytes = b"" if after_path is None else after_path.read_bytes()
                before_text = before_bytes.decode("utf-8")
                after_text = after_bytes.decode("utf-8")
            except (OSError, UnicodeError):
                change["detail"] = "binary or unreadable file"
                continue
            after_text = redact_known_secrets(after_text)
            before_text = redact_known_secrets(before_text)
            after_bytes = after_text.encode("utf-8")
            change["base_hash"] = None if before_info is None else hashlib.sha256(before_bytes).hexdigest()
            change["new_hash"] = None if after_path is None else hashlib.sha256(after_bytes).hexdigest()
            if status != "deleted" and apply_content_bytes + len(after_bytes) <= _MAX_APPLY_CONTENT_BYTES:
                change["new_content"] = after_text
                change["applyable"] = True
                apply_content_bytes += len(after_bytes)
            elif status == "deleted":
                change["detail"] = "automatic deletion is disabled"
            else:
                change["detail"] = "proposal content exceeds the application limit"
            lines = difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{name}" if before_info is not None else "/dev/null",
                tofile=f"b/{name}" if after_path is not None else "/dev/null",
            )
            fragment = "".join(lines)
            encoded = fragment.encode("utf-8")
            remaining = _MAX_DIFF_BYTES - diff_bytes
            if remaining <= 0:
                diff_truncated = True
                continue
            if len(encoded) > remaining:
                diff_parts.append(encoded[:remaining].decode("utf-8", errors="ignore"))
                diff_bytes += remaining
                diff_truncated = True
            else:
                diff_parts.append(fragment)
                diff_bytes += len(encoded)

    return {
        "changes": changes,
        "diff": redact_known_secrets("".join(diff_parts)),
        "diff_truncated": diff_truncated,
    }


def _execute(archive_bytes: bytes, command: str, cwd_relative: str) -> dict[str, object]:
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        raise RequestError("workspace snapshot exceeds the request size limit")
    if len(command.encode("utf-8")) > _MAX_COMMAND_BYTES:
        raise RequestError("command exceeds the size limit")
    relative = PurePosixPath(cwd_relative)
    if relative.is_absolute() or any(part in {"..", "."} or part.startswith(".") for part in relative.parts):
        raise RequestError("working directory must be a visible workspace-relative path")
    with tempfile.TemporaryDirectory(prefix="dindon-sandbox-") as temp_dir:
        temp_root = Path(temp_dir)
        root = temp_root / "workspace"
        root.mkdir(mode=0o700)
        _extract_workspace(archive_bytes, root)
        cwd = root.joinpath(*relative.parts) if relative.parts else root
        resolved = cwd.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_dir():
            raise RequestError("working directory is not a directory")
        result = _bounded_process(command, resolved, temp_root / "home")
        result.update(_workspace_changes(archive_bytes, root))
        return result


class Handler(BaseHTTPRequestHandler):
    server_version = "DindonSandbox/0.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler protocol
        if self.path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._respond(HTTPStatus.OK, {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler protocol
        if self.path != "/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0 or length > _MAX_ARCHIVE_BYTES:
                raise RequestError("invalid workspace snapshot size")
            command = _decode_header(
                self.headers.get("X-Dindon-Command"), max_bytes=_MAX_COMMAND_BYTES
            )
            cwd = _decode_header(self.headers.get("X-Dindon-Cwd"), max_bytes=4096)
            result = _execute(self.rfile.read(length), command, cwd)
        except (RequestError, ValueError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._respond(HTTPStatus.OK, result)

    def _respond(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        document = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(document)))
        self.end_headers()
        self.wfile.write(document)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    host = os.environ.get("DINDON_SANDBOX_HOST", "127.0.0.1")
    server = HTTPServer((host, 8787), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
