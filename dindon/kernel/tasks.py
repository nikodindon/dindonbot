"""Task state and allowed lifecycle transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    PAUSED = "paused"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecoveryAction(StrEnum):
    CONTINUE = "continue"
    RETRY_MODEL = "retry_model"
    RETRY_IDEMPOTENT_TOOL = "retry_idempotent_tool"
    NEEDS_USER = "needs_user"
    HANDLE_FAILURE = "handle_failure"


class StepState(StrEnum):
    INTENT = "intent"
    DONE = "done"
    FAILED = "failed"


TERMINAL_STATES = frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED})

TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING_APPROVAL,
            TaskState.WAITING_INPUT,
            TaskState.PAUSED,
            TaskState.VERIFYING,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.WAITING_APPROVAL: frozenset(
        {TaskState.RUNNING, TaskState.PAUSED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.WAITING_INPUT: frozenset(
        {TaskState.RUNNING, TaskState.PAUSED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.PAUSED: frozenset(
        {TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.VERIFYING: frozenset(
        {TaskState.RUNNING, TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    agent_id: str
    goal: str
    state: TaskState
    created_at: str
    updated_at: str
    parent_id: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    verification: list[dict[str, Any]] = field(default_factory=list)
    schedule: dict[str, Any] | None = None
    priority: int = 0
    result_artifact: str | None = None
    error: dict[str, Any] | None = None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    task_id: str
    index: int
    kind: str
    state: StepState
    payload: dict[str, Any]
    result: dict[str, Any] | None
    started_at: str
    finished_at: str | None
    tool_idempotent: bool | None = None
    idempotency_key: str | None = None
