"""Small fail-closed Guardian for the first read-only workspace tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


@dataclass(frozen=True, slots=True)
class GuardianDecision:
    verdict: str
    reason: str
    rule_id: str | None = None


class LocalGuardian:
    """Apply the initial workspace listing, file-read and shell policies."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def decide(self, tool: str, args: dict[str, Any]) -> GuardianDecision:
        if tool not in {"list_dir", "read_file", "shell"}:
            return GuardianDecision("deny", "tool is not in the read-only allowlist", "default-deny")
        if tool == "list_dir":
            if set(args) - {"path"}:
                return GuardianDecision("deny", "arguments do not match the tool schema", "workspace-read")
        elif tool == "read_file":
            if set(args) != {"path"}:
                return GuardianDecision("deny", "arguments do not match the tool schema", "workspace-read")
        elif set(args) - {"command", "cwd"} or "command" not in args:
            return GuardianDecision("deny", "arguments do not match the tool schema", "workspace-exec")

        if tool == "shell":
            command = args.get("command")
            if not isinstance(command, str) or not command.strip() or len(command.encode("utf-8")) > 2048 or "\x00" in command:
                return GuardianDecision("deny", "command must be non-empty and within the size limit", "workspace-exec")
        path = args.get("cwd", ".") if tool == "shell" else args.get("path", ".")
        if (
            not isinstance(path, str)
            or not path
            or len(path) > 4096
            or Path(path).is_absolute()
            or any(not part.isprintable() for part in PurePosixPath(path).parts)
            or any(part.startswith(".") for part in PurePosixPath(path).parts)
        ):
            return GuardianDecision("deny", "path must be a non-empty workspace-relative path", "workspace-read")
        if tool == "shell":
            candidate = self.workspace
            for part in PurePosixPath(path).parts:
                candidate = candidate / part
                if candidate.is_symlink():
                    return GuardianDecision("deny", "sandbox working paths cannot contain symlinks", "workspace-exec")
        try:
            target = (self.workspace / path).resolve(strict=True)
            relative_target = target.relative_to(self.workspace)
        except (OSError, ValueError, RuntimeError):
            return GuardianDecision("deny", "path is missing or outside the task workspace", "workspace-read")
        if any(part.startswith(".") for part in relative_target.parts):
            return GuardianDecision("deny", "hidden paths are not available to tools", "workspace-read")
        if tool == "shell":
            unavailable_dirs = {"data", "models", "node_modules", "__pycache__", "venv"}
            if any(part in unavailable_dirs for part in relative_target.parts):
                return GuardianDecision("deny", "working directory is excluded from the sandbox snapshot", "workspace-exec")
            sensitive_names = ("secret", "credential", "password", "token", "private-key", "id_rsa", "id_ed25519", "keystore", ".pem")
            if any(word in part.casefold() for part in relative_target.parts for word in sensitive_names):
                return GuardianDecision("deny", "working directory is excluded from the sandbox snapshot", "workspace-exec")
            if not target.is_dir():
                return GuardianDecision("deny", "working path is not a directory", "workspace-exec")
            return GuardianDecision("ask", "command runs in the isolated sandbox", "sandbox-command-approval")
        if tool == "list_dir":
            if not target.is_dir():
                return GuardianDecision("deny", "path is not a directory", "workspace-read")
            return GuardianDecision("allow", "read-only directory listing inside workspace", "workspace-read")
        if not target.is_file():
            return GuardianDecision("deny", "path is not a regular file", "workspace-read")
        sensitive_names = (
            "secret", "credential", "password", "token", "private-key",
            "id_rsa", "id_ed25519", "keystore", ".pem",
        )
        if any(word in part.casefold() for part in relative_target.parts for word in sensitive_names):
            return GuardianDecision("deny", "sensitive-looking filenames are not available to tools", "workspace-read")
        return GuardianDecision(
            "ask", "file contents will be sent to the local model", "file-content-approval"
        )
