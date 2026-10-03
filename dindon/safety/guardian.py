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
    """Allow directory listings only when they stay inside the task workspace."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def decide(self, tool: str, args: dict[str, Any]) -> GuardianDecision:
        if tool != "list_dir":
            return GuardianDecision("deny", "tool is not in the read-only allowlist", "default-deny")
        if set(args) - {"path"}:
            return GuardianDecision("deny", "arguments do not match the tool schema", "workspace-read")
        path = args.get("path", ".")
        if (
            not isinstance(path, str)
            or not path
            or len(path) > 4096
            or Path(path).is_absolute()
            or any(part.startswith(".") for part in PurePosixPath(path).parts)
        ):
            return GuardianDecision("deny", "path must be a non-empty workspace-relative path", "workspace-read")
        try:
            target = (self.workspace / path).resolve(strict=True)
            relative_target = target.relative_to(self.workspace)
        except (OSError, ValueError, RuntimeError):
            return GuardianDecision("deny", "path is missing or outside the task workspace", "workspace-read")
        if any(part.startswith(".") for part in relative_target.parts):
            return GuardianDecision("deny", "hidden paths are not available to tools", "workspace-read")
        if not target.is_dir():
            return GuardianDecision("deny", "path is not a directory", "workspace-read")
        return GuardianDecision("allow", "read-only directory listing inside workspace", "workspace-read")
