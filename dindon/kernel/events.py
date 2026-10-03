"""Versioned event records emitted by kernel state transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ids import new_id, utc_now


@dataclass(frozen=True, slots=True)
class Event:
    """A versioned event envelope; persistence and append-only rules live in the store."""

    id: str
    ts: str
    type: str
    source: str
    correlation_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    subject: str | None = None
    causation_id: str | None = None
    schema_version: int = 1

    @classmethod
    def create(
        cls,
        *,
        type: str,
        source: str,
        correlation_id: str,
        payload: dict[str, Any] | None = None,
        subject: str | None = None,
        causation_id: str | None = None,
        timestamp: str | None = None,
    ) -> Event:
        """Create an event with a typed ID and an injectable timestamp."""
        return cls(
            id=new_id("evt"),
            ts=timestamp if timestamp is not None else utc_now(),
            type=type,
            source=source,
            subject=subject,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload={} if payload is None else payload,
        )
