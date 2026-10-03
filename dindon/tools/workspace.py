"""Read-only workspace inspection tools."""

from __future__ import annotations

import json
from pathlib import Path
import re
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
MAX_READ_BYTES = 64 * 1024
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
    for pattern in _SECRET_PATTERNS:
        content = pattern.sub(_redact_secret, content)
    return json.dumps(
        {"path": relative, "untrusted": True, "content": content},
        ensure_ascii=False,
        separators=(",", ":"),
    )
