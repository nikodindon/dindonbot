"""Read-only workspace inspection tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
            if not item.name.startswith(".")
        ),
        key=lambda item: item["name"].casefold(),
    )
    truncated = len(entries) > 100
    return json.dumps(
        {"entries": entries[:100], "truncated": truncated},
        ensure_ascii=False,
        separators=(",", ":"),
    )
