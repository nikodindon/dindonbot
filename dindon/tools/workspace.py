"""Read-only workspace inspection tools."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import io
import json
import os
from pathlib import Path
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit
import zipfile

LIST_DIR_TOOL = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "List visible file and directory names in the task workspace.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Workspace-relative directory; defaults to ."}},
            "required": [],
            "additionalProperties": False,
        },
    },
}
READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a text file from the task workspace after human approval.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Workspace-relative file path."}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}
SHELL_TOOL = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": "Run a command in the network-isolated sandbox after human approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "minLength": 1, "maxLength": 2048},
                "cwd": {"type": "string", "description": "Visible workspace-relative directory; defaults to ."},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}
MAX_READ_BYTES = 64 * 1024
MAX_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_FILES = 10_000
MAX_SNAPSHOT_TOTAL_BYTES = 128 * 1024 * 1024
_EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "data", "models"}
_SENSITIVE_NAMES = (
    "secret", "credential", "password", "token", "private-key", "id_rsa", "id_ed25519", "keystore", ".pem"
)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r'''(?i)(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)'''
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),
)


def _redact_secret(match: re.Match[str]) -> str:
    return "[REDACTED SECRET]"


def redact_known_secrets(content: str) -> str:
    for pattern in _SECRET_PATTERNS:
        content = pattern.sub(_redact_secret, content)
    return content


def list_directory(workspace: Path, args: dict[str, Any]) -> str:
    """Return at most 100 visible entry names; never read file contents."""
    relative = args.get("path", ".")
    target = (workspace / relative).resolve(strict=True)
    target.relative_to(workspace)
    if not target.is_dir():
        raise ValueError("path is not a directory")
    entries = sorted(
        (
            {"name": item.name, "type": "directory" if item.is_dir() else "file"}
            for item in target.iterdir()
            if not item.name.startswith(".") and item.name.isprintable()
        ),
        key=lambda item: item["name"].casefold(),
    )
    truncated = len(entries) > 100
    return json.dumps(
        {"entries": entries[:100], "truncated": truncated},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def read_text_file(workspace: Path, args: dict[str, Any]) -> str:
    """Read a bounded UTF-8 file and label its content as untrusted data."""
    relative = args.get("path")
    if not isinstance(relative, str) or not relative:
        raise ValueError("path must be a workspace-relative file path")
    target = (workspace / relative).resolve(strict=True)
    target.relative_to(workspace)
    if not target.is_file():
        raise ValueError("path is not a regular file")
    with target.open("rb") as source:
        raw_content = source.read(MAX_READ_BYTES + 1)
    if len(raw_content) > MAX_READ_BYTES:
        raise ValueError(f"file exceeds the {MAX_READ_BYTES}-byte reading limit")
    content = raw_content.decode("utf-8")
    content = redact_known_secrets(content)
    return json.dumps(
        {"path": relative, "untrusted": True, "content": content},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def create_workspace_snapshot(workspace: Path) -> tuple[bytes, str]:
    """Build a deterministic archive excluding hidden and sensitive-looking files."""
    buffer = io.BytesIO()
    digest = hashlib.sha256()
    total_bytes = 0
    count = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for directory, dirnames, filenames in os.walk(workspace, followlinks=False):
            parent = Path(directory)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not name.startswith(".")
                and name.casefold() not in _EXCLUDED_DIRS
                and not (parent / name).is_symlink()
            )
            for dirname in dirnames:
                relative_dir = (parent / dirname).relative_to(workspace).as_posix()
                encoded_dir = relative_dir.encode("utf-8")
                digest.update(b"D" + len(encoded_dir).to_bytes(4, "big") + encoded_dir)
                info = zipfile.ZipInfo(relative_dir + "/", date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o40700 << 16
                archive.writestr(info, b"")
                count += 1
                if count > MAX_SNAPSHOT_FILES:
                    raise ValueError("workspace snapshot exceeds its entry limit")
            for filename in sorted(filenames):
                path = parent / filename
                relative = path.relative_to(workspace)
                if (
                    path.is_symlink()
                    or any(part.startswith(".") or not part.isprintable() for part in relative.parts)
                ):
                    continue
                if any(word in part.casefold() for part in relative.parts for word in _SENSITIVE_NAMES):
                    continue
                try:
                    if not path.is_file():
                        continue
                    if path.stat().st_size > MAX_SNAPSHOT_FILE_BYTES:
                        continue
                    raw = path.read_bytes()
                    text = raw.decode("utf-8")
                except (OSError, UnicodeError):
                    continue
                content = redact_known_secrets(text).encode("utf-8")
                count += 1
                total_bytes += len(content)
                if count > MAX_SNAPSHOT_FILES or total_bytes > MAX_SNAPSHOT_TOTAL_BYTES:
                    raise ValueError("workspace snapshot exceeds its file or size limit")
                relative_name = relative.as_posix()
                encoded_name = relative_name.encode("utf-8")
                digest.update(b"F" + len(encoded_name).to_bytes(4, "big"))
                digest.update(encoded_name)
                digest.update(len(content).to_bytes(8, "big"))
                digest.update(content)
                info = zipfile.ZipInfo(relative_name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                archive.writestr(info, content)
    result = buffer.getvalue()
    if len(result) > MAX_SNAPSHOT_BYTES:
        raise ValueError("workspace snapshot exceeds the compressed size limit")
    return result, digest.hexdigest()


def run_in_sandbox(
    *,
    command: str,
    cwd: str,
    archive: bytes,
    base_url: str,
    timeout_s: float = 40.0,
) -> dict[str, Any]:
    """Send a read-only, filtered snapshot and one approved command to the sandbox."""
    parsed = urlsplit(base_url)
    host = parsed.hostname
    is_local = host in {"localhost", "sandbox"}
    if host:
        try:
            is_local = is_local or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
    if (
        parsed.scheme != "http"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not is_local
    ):
        raise ValueError("sandbox URL must point to localhost or the internal sandbox service")
    def encode_header(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")

    request = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", "run"),
        data=archive,
        headers={
            "Content-Type": "application/zip",
            "X-Dindon-Command": encode_header(command),
            "X-Dindon-Cwd": encode_header(cwd),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("error", "sandbox request failed")
        except (ValueError, AttributeError):
            detail = "sandbox request failed"
        raise RuntimeError(str(detail)) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConnectionError(f"could not reach the sandbox: {exc}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("exit_code"), int):
        raise ValueError("sandbox returned an invalid response")
    result["stdout"] = redact_known_secrets(str(result.get("stdout", "")))
    result["stderr"] = redact_known_secrets(str(result.get("stderr", "")))
    return result
