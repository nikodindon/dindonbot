"""SQLite persistence for tasks and their append-only event journal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
import sqlite3
from threading import RLock
from typing import Any

from .events import Event
from .ids import new_id, utc_now
from .tasks import (
    Approval,
    TASK_TRANSITIONS,
    RecoveryAction,
    Step,
    StepState,
    Task,
    TaskState,
    TERMINAL_STATES,
)

_SCHEMA_VERSION = 3
_EVENT_NAMES = {
    TaskState.WAITING_APPROVAL: "task.waiting_approval",
    TaskState.WAITING_INPUT: "task.waiting_input",
    TaskState.PAUSED: "task.paused",
    TaskState.VERIFYING: "task.verifying",
    TaskState.SUCCEEDED: "task.succeeded",
    TaskState.FAILED: "task.failed",
    TaskState.CANCELLED: "task.cancelled",
}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class TaskStore:
    """Persist tasks and task-state events atomically in a SQLite database.

    Task input must contain data only. Store secret names/references, never
    credential values, in accordance with kernel invariant K4.
    """

    def __init__(self, path: str | Path) -> None:
        db_path = Path(path)
        if str(path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = RLock()
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> TaskStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _initialize(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > _SCHEMA_VERSION:
            raise RuntimeError(f"Database schema {version} is newer than supported {_SCHEMA_VERSION}")

        if version < 1:
            with self._transaction():
                statements = (
                    """CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    parent_id TEXT REFERENCES tasks(id),
                    goal TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    budget_json TEXT NOT NULL,
                    verification_json TEXT NOT NULL,
                    schedule_json TEXT,
                    result_artifact TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )""",
                    "CREATE INDEX tasks_state_created ON tasks(state, created_at)",
                    """CREATE TABLE events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    ts TEXT NOT NULL,
                    type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    subject TEXT,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT,
                    payload TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )""",
                    "CREATE INDEX events_subject_seq ON events(subject, seq)",
                    "CREATE INDEX events_type_seq ON events(type, seq)",
                    """CREATE TRIGGER events_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
                    """CREATE TRIGGER events_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
                )
                for statement in statements:
                    self._db.execute(statement)
                self._db.execute("PRAGMA user_version = 1")

        if version < 2:
            with self._transaction():
                self._db.execute(
                    """CREATE TABLE steps (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES tasks(id),
                        idx INTEGER NOT NULL,
                        kind TEXT NOT NULL CHECK (kind IN ('model', 'tool')),
                        state TEXT NOT NULL CHECK (state IN ('intent', 'done', 'failed')),
                        payload TEXT NOT NULL,
                        result TEXT,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        tool_idempotent INTEGER CHECK (tool_idempotent IN (0, 1)),
                        idempotency_key TEXT,
                        UNIQUE (task_id, idx),
                        CHECK (
                            (kind = 'model' AND tool_idempotent IS NULL AND idempotency_key IS NULL)
                            OR
                            (kind = 'tool' AND tool_idempotent IS NOT NULL AND idempotency_key IS NOT NULL)
                        )
                    )"""
                )
                self._db.execute("CREATE INDEX steps_task_idx ON steps(task_id, idx)")
                self._db.execute("PRAGMA user_version = 2")

        if version < 3:
            with self._transaction():
                self._db.execute(
                    """CREATE TABLE approvals (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES tasks(id),
                        step_id TEXT NOT NULL UNIQUE REFERENCES steps(id),
                        action_summary TEXT NOT NULL,
                        action_hash TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (state IN ('pending', 'granted', 'denied', 'expired', 'revoked')),
                        requested_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        decided_at TEXT,
                        decided_by TEXT,
                        channel TEXT
                    )"""
                )
                self._db.execute("CREATE INDEX approvals_task_state ON approvals(task_id, state)")
                self._db.execute("PRAGMA user_version = 3")

    class _Transaction:
        def __init__(self, store: TaskStore) -> None:
            self.store = store

        def __enter__(self) -> sqlite3.Connection:
            self.store._lock.acquire()
            try:
                self.store._db.execute("BEGIN IMMEDIATE")
            except BaseException:
                self.store._lock.release()
                raise
            return self.store._db

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            try:
                self.store._db.execute("ROLLBACK" if exc_type else "COMMIT")
            finally:
                self.store._lock.release()
            return False

    def _transaction(self) -> TaskStore._Transaction:
        return self._Transaction(self)

    def create_task(
        self,
        *,
        agent_id: str,
        goal: str,
        input: dict[str, Any] | None = None,
        parent_id: str | None = None,
        budget: dict[str, Any] | None = None,
        verification: list[dict[str, Any]] | None = None,
        schedule: dict[str, Any] | None = None,
        priority: int = 0,
        source: str = "runtime",
        correlation_id: str | None = None,
    ) -> Task:
        """Create a pending task and its ``task.created`` event atomically."""
        if not agent_id.strip() or not goal.strip():
            raise ValueError("agent_id and goal must not be empty")
        task_id = new_id("tsk")
        now = utc_now()
        task = Task(
            id=task_id,
            agent_id=agent_id,
            parent_id=parent_id,
            goal=goal,
            input={} if input is None else input,
            state=TaskState.PENDING,
            budget={} if budget is None else budget,
            verification=[] if verification is None else verification,
            schedule=schedule,
            priority=priority,
            created_at=now,
            updated_at=now,
        )
        event = Event.create(
            type="task.created",
            source=source,
            correlation_id=correlation_id or task_id,
            subject=task_id,
            payload={"agent_id": agent_id, "state": TaskState.PENDING.value},
            timestamp=now,
        )
        with self._transaction():
            self._db.execute(
                """INSERT INTO tasks
                   (id, agent_id, parent_id, goal, input_json, state, priority,
                    budget_json, verification_json, schedule_json, result_artifact,
                    error_json, created_at, updated_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.id,
                    task.agent_id,
                    task.parent_id,
                    task.goal,
                    _json_dump(task.input),
                    task.state.value,
                    task.priority,
                    _json_dump(task.budget),
                    _json_dump(task.verification),
                    None if task.schedule is None else _json_dump(task.schedule),
                    None,
                    None,
                    task.created_at,
                    task.updated_at,
                    task.schema_version,
                ),
            )
            self._append_event(event)
        return task

    def transition_task(
        self,
        task_id: str,
        state: TaskState | str,
        *,
        source: str,
        correlation_id: str,
        causation_id: str | None = None,
        result_artifact: str | None = None,
    ) -> Task:
        """Change task state and append its matching event in one transaction."""
        target = TaskState(state)
        with self._transaction():
            row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Task not found: {task_id}")
            current = TaskState(row["state"])
            if target not in TASK_TRANSITIONS[current]:
                raise ValueError(f"Invalid task transition: {current.value} -> {target.value}")
            now = utc_now()
            self._db.execute(
                "UPDATE tasks SET state = ?, updated_at = ?, result_artifact = COALESCE(?, result_artifact) WHERE id = ?",
                (target.value, now, result_artifact, task_id),
            )
            event = Event.create(
                type=self._event_name(current, target),
                source=source,
                correlation_id=correlation_id,
                causation_id=causation_id,
                subject=task_id,
                payload={"from": current.value, "to": target.value},
                timestamp=now,
            )
            self._append_event(event)
            updated = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_from_row(updated)

    def request_approval(
        self,
        *,
        task_id: str,
        step_id: str,
        action_summary: str,
        action_hash: str,
        source: str,
        expires_in_s: int = 600,
    ) -> Approval:
        """Persist a human approval and pause its task atomically."""
        if not action_summary.strip() or not action_hash or expires_in_s <= 0:
            raise ValueError("approval summary, hash, and positive expiry are required")
        now_dt = datetime.now(UTC)
        requested_at = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expires_at = (now_dt + timedelta(seconds=expires_in_s)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        approval = Approval(
            id=new_id("apr"),
            task_id=task_id,
            step_id=step_id,
            action_summary=action_summary,
            action_hash=action_hash,
            state="pending",
            requested_at=requested_at,
            expires_at=expires_at,
        )
        with self._transaction():
            task = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            step = self._db.execute(
                "SELECT task_id, kind, state FROM steps WHERE id = ?", (step_id,)
            ).fetchone()
            if task is None or step is None or step["task_id"] != task_id:
                raise KeyError("task or approval step not found")
            if TaskState(task["state"]) != TaskState.RUNNING:
                raise ValueError("approvals can only be requested for a running task")
            if step["kind"] != "tool" or step["state"] != StepState.INTENT.value:
                raise ValueError("approval must refer to a pending tool intent")
            self._db.execute(
                """INSERT INTO approvals
                   (id, task_id, step_id, action_summary, action_hash, state,
                    requested_at, expires_at, decided_at, decided_by, channel)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, NULL)""",
                (
                    approval.id,
                    task_id,
                    step_id,
                    action_summary,
                    action_hash,
                    requested_at,
                    expires_at,
                ),
            )
            self._db.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
                (TaskState.WAITING_APPROVAL.value, requested_at, task_id),
            )
            self._append_event(Event.create(
                type="approval.requested",
                source=source,
                correlation_id=task_id,
                subject=approval.id,
                payload={
                    "task_id": task_id,
                    "step_id": step_id,
                    "action_hash": action_hash,
                    "action_summary": action_summary,
                    "expires_at": expires_at,
                },
                timestamp=requested_at,
            ))
            self._append_event(Event.create(
                type="task.waiting_approval",
                source=source,
                correlation_id=task_id,
                subject=task_id,
                payload={"from": TaskState.RUNNING.value, "to": TaskState.WAITING_APPROVAL.value},
                timestamp=requested_at,
            ))
        return approval

    def get_approval_for_step(self, step_id: str) -> Approval | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM approvals WHERE step_id = ?", (step_id,)
            ).fetchone()
        return None if row is None else self._approval_from_row(row)

    def get_approval(self, approval_id: str) -> Approval | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return None if row is None else self._approval_from_row(row)

    def list_pending_approvals(self) -> list[Approval]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM approvals WHERE state = 'pending' ORDER BY requested_at, id"
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def decide_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        source: str,
        channel: str = "cli",
    ) -> tuple[Approval, Task]:
        """Record a human decision and resume the task in one transaction."""
        with self._transaction():
            row = self._db.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Approval not found: {approval_id}")
            if row["state"] != "pending":
                raise ValueError(f"approval is already {row['state']}")
            task = self._db.execute(
                "SELECT * FROM tasks WHERE id = ?", (row["task_id"],)
            ).fetchone()
            if task is None:
                raise KeyError(f"Task not found: {row['task_id']}")
            if TaskState(task["state"]) != TaskState.WAITING_APPROVAL:
                raise ValueError("approval task is not waiting for a decision")

            now_dt = datetime.now(UTC)
            decided_at = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            expires_dt = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            state = "expired" if now_dt >= expires_dt else ("granted" if approved else "denied")
            self._db.execute(
                """UPDATE approvals SET state = ?, decided_at = ?, decided_by = 'user', channel = ?
                   WHERE id = ?""",
                (state, decided_at, channel, approval_id),
            )
            self._db.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
                (TaskState.RUNNING.value, decided_at, row["task_id"]),
            )
            self._append_event(Event.create(
                type=f"approval.{state}",
                source=source,
                correlation_id=row["task_id"],
                subject=approval_id,
                payload={"task_id": row["task_id"], "step_id": row["step_id"], "channel": channel},
                timestamp=decided_at,
            ))
            self._append_event(Event.create(
                type="task.resumed",
                source=source,
                correlation_id=row["task_id"],
                subject=row["task_id"],
                payload={
                    "from": TaskState.WAITING_APPROVAL.value,
                    "to": TaskState.RUNNING.value,
                    "approval_id": approval_id,
                },
                timestamp=decided_at,
            ))
            updated_approval = self._db.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            updated_task = self._db.execute(
                "SELECT * FROM tasks WHERE id = ?", (row["task_id"],)
            ).fetchone()
        return self._approval_from_row(updated_approval), self._task_from_row(updated_task)

    def start_step(
        self,
        task_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
        source: str,
        correlation_id: str | None = None,
        tool_idempotent: bool | None = None,
        idempotency_key: str | None = None,
    ) -> Step:
        """Persist an intent checkpoint before making a model or tool call."""
        if kind not in {"model", "tool"}:
            raise ValueError("step kind must be 'model' or 'tool'")
        if kind == "tool":
            if not isinstance(tool_idempotent, bool) or not idempotency_key:
                raise ValueError("tool steps require idempotency metadata before execution")
        elif tool_idempotent is not None or idempotency_key is not None:
            raise ValueError("model steps must not have tool idempotency metadata")

        now = utc_now()
        step_id = new_id("stp")
        with self._transaction():
            task = self._db.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"Task not found: {task_id}")
            if TaskState(task["state"]) != TaskState.RUNNING:
                raise ValueError("steps can only be started for a running task")
            previous = self._db.execute(
                "SELECT idx, state FROM steps WHERE task_id = ? ORDER BY idx DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if previous is not None and previous["state"] == StepState.INTENT.value:
                raise ValueError("the previous step intent must be resolved before starting another")
            index = 0 if previous is None else previous["idx"] + 1
            self._db.execute(
                """INSERT INTO steps
                   (id, task_id, idx, kind, state, payload, result, started_at,
                    finished_at, tool_idempotent, idempotency_key)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)""",
                (
                    step_id, task_id, index, kind, StepState.INTENT.value,
                    _json_dump(payload), now,
                    None if tool_idempotent is None else int(tool_idempotent),
                    idempotency_key,
                ),
            )
            self._append_event(Event.create(
                type="step.started",
                source=source,
                correlation_id=correlation_id or task_id,
                subject=step_id,
                payload={"task_id": task_id, "index": index, "kind": kind},
                timestamp=now,
            ))
            self._db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
        return Step(
            id=step_id, task_id=task_id, index=index, kind=kind,
            state=StepState.INTENT, payload=payload, result=None,
            started_at=now, finished_at=None,
            tool_idempotent=tool_idempotent, idempotency_key=idempotency_key,
        )

    def finish_step(
        self,
        step_id: str,
        *,
        result: dict[str, Any] | None,
        source: str,
        correlation_id: str,
        failed: bool = False,
    ) -> Step:
        """Save a step result and its event as one durable checkpoint."""
        state = StepState.FAILED if failed else StepState.DONE
        now = utc_now()
        with self._transaction():
            row = self._db.execute("SELECT * FROM steps WHERE id = ?", (step_id,)).fetchone()
            if row is None:
                raise KeyError(f"Step not found: {step_id}")
            if row["state"] != StepState.INTENT.value:
                raise ValueError("only an unresolved step intent can be completed")
            self._db.execute(
                "UPDATE steps SET state = ?, result = ?, finished_at = ? WHERE id = ?",
                (state.value, None if result is None else _json_dump(result), now, step_id),
            )
            self._append_event(Event.create(
                type="step.failed" if failed else "step.done",
                source=source,
                correlation_id=correlation_id,
                subject=step_id,
                payload={"task_id": row["task_id"], "index": row["idx"]},
                timestamp=now,
            ))
            self._db.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?", (now, row["task_id"])
            )
            updated = self._db.execute("SELECT * FROM steps WHERE id = ?", (step_id,)).fetchone()
        return self._step_from_row(updated)

    def list_steps(self, task_id: str) -> list[Step]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM steps WHERE task_id = ? ORDER BY idx", (task_id,)
            ).fetchall()
        return [self._step_from_row(row) for row in rows]

    def recovery_action(self, task_id: str) -> RecoveryAction:
        """Describe how the most recent checkpoint can safely be resumed."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM steps WHERE task_id = ? ORDER BY idx DESC LIMIT 1", (task_id,)
            ).fetchone()
        if row is None or row["state"] == StepState.DONE.value:
            return RecoveryAction.CONTINUE
        if row["state"] == StepState.FAILED.value:
            return RecoveryAction.HANDLE_FAILURE
        if row["kind"] == "model":
            return RecoveryAction.RETRY_MODEL
        if row["tool_idempotent"]:
            return RecoveryAction.RETRY_IDEMPOTENT_TOOL
        return RecoveryAction.NEEDS_USER

    def recover_interrupted_tasks(self, *, source: str = "runtime") -> list[Task]:
        """Move interrupted non-idempotent tool calls to waiting_input.

        Without a tool probe implementation, this deliberately requires the
        user's input rather than risking a repeated external side effect.
        """
        now = utc_now()
        waiting: list[str] = []
        with self._transaction():
            rows = self._db.execute(
                """SELECT t.id, s.id AS step_id FROM tasks AS t
                   JOIN steps AS s ON s.task_id = t.id
                   WHERE t.state = ? AND s.state = ? AND s.kind = 'tool'
                     AND s.tool_idempotent = 0
                     AND s.idx = (SELECT MAX(last.idx) FROM steps AS last WHERE last.task_id = t.id)""",
                (TaskState.RUNNING.value, StepState.INTENT.value),
            ).fetchall()
            for row in rows:
                self._db.execute(
                    "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
                    (TaskState.WAITING_INPUT.value, now, row["id"]),
                )
                self._append_event(Event.create(
                    type="task.waiting_input",
                    source=source,
                    correlation_id=row["id"],
                    subject=row["id"],
                    payload={
                        "reason": "interrupted_non_idempotent_tool",
                        "step_id": row["step_id"],
                    },
                    timestamp=now,
                ))
                waiting.append(row["id"])
            tasks = [
                self._task_from_row(
                    self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                )
                for task_id in waiting
            ]
        return tasks

    @staticmethod
    def _event_name(current: TaskState, target: TaskState) -> str:
        if target == TaskState.RUNNING:
            if current == TaskState.VERIFYING:
                return "task.repairing"
            return "task.started" if current == TaskState.PENDING else "task.resumed"
        return _EVENT_NAMES[target]

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return None if row is None else self._task_from_row(row)

    def list_tasks(self) -> list[Task]:
        """Return all tasks in creation order for CLI inspection."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks ORDER BY created_at, id"
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_incomplete_tasks(self) -> list[Task]:
        """Return persisted tasks that are not terminal, for runtime recovery."""
        terminal = tuple(state.value for state in TERMINAL_STATES)
        placeholders = ",".join("?" for _ in terminal)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM tasks WHERE state NOT IN ({placeholders}) ORDER BY created_at, id",
                terminal,
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_events(self, *, after_seq: int = 0, limit: int = 100) -> list[tuple[int, Event]]:
        if after_seq < 0 or limit < 1:
            raise ValueError("after_seq must be non-negative and limit must be positive")
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (after_seq, limit)
            ).fetchall()
        return [(row["seq"], self._event_from_row(row)) for row in rows]

    def _append_event(self, event: Event) -> None:
        self._db.execute(
            """INSERT INTO events
               (id, ts, type, source, subject, correlation_id, causation_id, payload, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.ts,
                event.type,
                event.source,
                event.subject,
                event.correlation_id,
                event.causation_id,
                _json_dump(event.payload),
                event.schema_version,
            ),
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            agent_id=row["agent_id"],
            parent_id=row["parent_id"],
            goal=row["goal"],
            input=json.loads(row["input_json"]),
            state=TaskState(row["state"]),
            priority=row["priority"],
            budget=json.loads(row["budget_json"]),
            verification=json.loads(row["verification_json"]),
            schedule=None if row["schedule_json"] is None else json.loads(row["schedule_json"]),
            result_artifact=row["result_artifact"],
            error=None if row["error_json"] is None else json.loads(row["error_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            schema_version=row["schema_version"],
        )

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> Step:
        return Step(
            id=row["id"],
            task_id=row["task_id"],
            index=row["idx"],
            kind=row["kind"],
            state=StepState(row["state"]),
            payload=json.loads(row["payload"]),
            result=None if row["result"] is None else json.loads(row["result"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            tool_idempotent=(
                None if row["tool_idempotent"] is None else bool(row["tool_idempotent"])
            ),
            idempotency_key=row["idempotency_key"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            ts=row["ts"],
            type=row["type"],
            source=row["source"],
            subject=row["subject"],
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
            payload=json.loads(row["payload"]),
            schema_version=row["schema_version"],
        )

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> Approval:
        return Approval(
            id=row["id"],
            task_id=row["task_id"],
            step_id=row["step_id"],
            action_summary=row["action_summary"],
            action_hash=row["action_hash"],
            state=row["state"],
            requested_at=row["requested_at"],
            expires_at=row["expires_at"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            channel=row["channel"],
        )
