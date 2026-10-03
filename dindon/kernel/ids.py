"""Typed, time-sortable identifiers and canonical UTC timestamps."""

from __future__ import annotations

from datetime import UTC, datetime
import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIXES = frozenset(
    {"agt", "tsk", "stp", "tcl", "mem", "mdl", "nod", "skl", "evt", "apr", "art"}
)


def new_id(prefix: str, *, timestamp_ms: int | None = None) -> str:
    """Return a typed ULID, for example ``tsk_01ARZ3NDEKTSV4RRFFQ69G5FAV``.

    ``timestamp_ms`` is injectable to control the time component in tests and
    replay. The random portion keeps identifiers unique within a millisecond.
    """
    if prefix not in _PREFIXES:
        raise ValueError(f"Unsupported identifier prefix: {prefix!r}")

    milliseconds = int(time.time() * 1_000) if timestamp_ms is None else timestamp_ms
    if not 0 <= milliseconds < 2**48:
        raise ValueError("ULID timestamp must fit in 48 bits")

    value = (milliseconds << 80) | secrets.randbits(80)
    encoded = "".join(
        _CROCKFORD[(value >> shift) & 0x1F] for shift in range(125, -1, -5)
    )
    return f"{prefix}_{encoded}"


def utc_now() -> str:
    """Return the current UTC time in ISO 8601 with millisecond precision."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
