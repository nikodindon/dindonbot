from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from dindon.interfaces.cli import _task_apply
from dindon.kernel import StepState, TaskState, TaskStore
from dindon.runtime import TaskEngine
from dindon.safety import LocalGuardian
from dindon.sandbox.server import _extract_workspace, _workspace_changes
from dindon.tools.workspace import (
    create_workspace_snapshot,
    list_directory,
    read_text_file,
)


def test_guardian_scopes_directory_listing_and_requires_file_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    api_key_name = "API" + "_KEY"
    (workspace / ".env").write_text(f"{api_key_name}=not-a-real-key\n", encoding="utf-8")
    guardian = LocalGuardian(workspace)

    assert guardian.decide("list_dir", {"path": "."}).verdict == "allow"
    assert guardian.decide("list_dir", {"path": "../"}).verdict == "deny"
    assert guardian.decide("read_file", {"path": "src/main.py"}).verdict == "ask"
    assert guardian.decide("read_file", {"path": ".env"}).verdict == "deny"
    assert guardian.decide("shell", {"command": "python -V", "cwd": "."}).verdict == "ask"
    assert guardian.decide("shell", {"command": "python -V", "cwd": "../"}).verdict == "deny"

    listing = json.loads(list_directory(workspace, {"path": "."}))
    assert [entry["name"] for entry in listing["entries"]] == ["src"]


def test_read_file_redacts_known_secret_patterns(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake_token = "github_pat_" + "A" * 30
    (workspace / "notes.md").write_text(f"token={fake_token}\n", encoding="utf-8")

    result = json.loads(read_text_file(workspace, {"path": "notes.md"}))

    assert result["untrusted"] is True
    assert fake_token not in result["content"]
    assert "[REDACTED SECRET]" in result["content"]


def test_workspace_snapshot_filters_hidden_files_and_produces_bounded_diff(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("before\n", encoding="utf-8")
    api_key_name = "API" + "_KEY"
    (workspace / ".env").write_text(f"{api_key_name}=not-a-real-key\n", encoding="utf-8")

    snapshot, initial_hash = create_workspace_snapshot(workspace)
    with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
        assert archive.namelist() == ["README.md"]

    disposable = tmp_path / "disposable"
    disposable.mkdir()
    _extract_workspace(snapshot, disposable)
    unchanged = _workspace_changes(snapshot, disposable)
    assert unchanged["changes"] == []
    assert unchanged["diff"] == ""
    assert unchanged["diff_truncated"] is False

    (disposable / "README.md").write_text("after\n", encoding="utf-8")
    (disposable / "new.txt").write_text("new file\n", encoding="utf-8")

    changed = _workspace_changes(snapshot, disposable)
    by_path = {item["path"]: item for item in changed["changes"]}
    assert by_path["README.md"]["status"] == "modified"
    assert by_path["README.md"]["applyable"] is True
    assert by_path["README.md"]["base_hash"] == hashlib.sha256(b"before\n").hexdigest()
    assert by_path["README.md"]["new_content"] == "after\n"
    assert by_path["new.txt"]["status"] == "added"
    assert "before" in changed["diff"] and "after" in changed["diff"]
    assert initial_hash


def test_snapshot_preserves_python_annotations_and_rejects_redacted_source(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    annotated_source = "def connect(api_key: str | None = None):\n    return api_key\n"
    (workspace / "safe.py").write_text(annotated_source, encoding="utf-8")

    snapshot, _ = create_workspace_snapshot(workspace)
    with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
        assert archive.read("safe.py").decode("utf-8") == annotated_source

    api_key_name = "API" + "_KEY"
    suspicious_source = f'{api_key_name} = "not-a-real-key"\n'
    (workspace / "unsafe.py").write_text(suspicious_source, encoding="utf-8")
    with pytest.raises(ValueError, match="redaction would alter Python source"):
        create_workspace_snapshot(workspace)


class FakeLLM:
    def __init__(self, *messages: dict[str, object]) -> None:
        self.messages = list(messages)

    def chat_completions(self, **_kwargs: object) -> dict[str, object]:
        message = self.messages.pop(0)
        return {"model": "fake", "choices": [{"message": message}]}


def test_read_file_waits_for_exact_cli_approval_then_resumes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("local project notes\n", encoding="utf-8")
    llm = FakeLLM(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_read_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
            }],
        },
        {"role": "assistant", "content": "Le fichier contient des notes locales."},
    )

    with TaskStore(tmp_path / "tasks.sqlite") as store:
        engine = TaskEngine(store, llm, model="fake", workspace=workspace)
        task, answer = engine.run(agent_id="chief", goal="Résume README.md")
        assert task.state == TaskState.WAITING_APPROVAL
        assert answer.startswith("Approbation requise : apr_")
        approval = store.list_pending_approvals()[0]
        tool_step = store.list_steps(task.id)[-1]
        assert tool_step.state == StepState.INTENT
        assert "local project notes" not in json.dumps(tool_step.payload)

        decided, resumed_task = store.decide_approval(
            approval.id, approved=True, source="test", channel="cli"
        )
        assert decided.state == "granted"
        task, answer = engine.resume(resumed_task.id)

        assert task.state == TaskState.SUCCEEDED
        assert answer == "Le fichier contient des notes locales."
        read_step = next(step for step in store.list_steps(task.id) if step.kind == "tool")
        assert read_step.result["decision"]["rule_id"] == "human-approval"
        assert json.loads(read_step.result["message"]["content"])["content"] == "local project notes\n"


def test_cancel_task_revokes_pending_approval_atomically(tmp_path: Path) -> None:
    with TaskStore(tmp_path / "cancel.sqlite") as store:
        task = store.create_task(agent_id="chief", goal="read a file")
        store.transition_task(task.id, TaskState.RUNNING, source="test", correlation_id=task.id)
        step = store.start_step(
            task.id,
            kind="tool",
            payload={"tool": "read_file", "args": {"path": "README.md"}},
            source="test",
            correlation_id=task.id,
            tool_idempotent=True,
            idempotency_key="call-cancel-test",
        )
        approval = store.request_approval(
            task_id=task.id,
            step_id=step.id,
            action_summary="read README",
            action_hash="a" * 64,
            source="test",
        )

        cancelled = store.cancel_task(task.id, source="test")

        assert cancelled.state == TaskState.CANCELLED
        assert store.list_pending_approvals() == []
        assert store.get_approval(approval.id).state == "revoked"
        event_types = [event.type for _, event in store.list_events()]
        assert "approval.revoked" in event_types
        assert "task.cancelled" in event_types


def _completed_apply_task(database: Path, workspace: Path, old: str, new: str) -> str:
    task_id = ""
    with TaskStore(database) as store:
        task = store.create_task(
            agent_id="code",
            goal="propose a small edit",
            input={"workspace": str(workspace)},
        )
        task_id = task.id
        store.transition_task(task.id, TaskState.RUNNING, source="test", correlation_id=task.id)
        step = store.start_step(
            task.id,
            kind="tool",
            payload={"tool": "shell", "args": {"command": "edit"}},
            source="test",
            correlation_id=task.id,
            tool_idempotent=True,
            idempotency_key="call-apply-test",
        )
        old_bytes = old.encode("utf-8")
        new_bytes = new.encode("utf-8")
        output = {
            "untrusted": True,
            "changes": [{
                "path": "README.md",
                "status": "modified",
                "applyable": True,
                "base_hash": hashlib.sha256(old_bytes).hexdigest(),
                "new_hash": hashlib.sha256(new_bytes).hexdigest(),
                "new_content": new,
            }],
            "diff": "--- a/README.md\n+++ b/README.md\n-before\n+after\n",
            "diff_truncated": False,
        }
        store.finish_step(
            step.id,
            result={
                "message": {
                    "role": "tool",
                    "tool_call_id": "call-apply-test",
                    "content": json.dumps(output),
                },
                "decision": {"verdict": "allow", "rule_id": "human-approval"},
            },
            source="test",
            correlation_id=task.id,
        )
        store.transition_task(task.id, TaskState.VERIFYING, source="test", correlation_id=task.id)
        store.transition_task(task.id, TaskState.SUCCEEDED, source="test", correlation_id=task.id)
    return task_id


def test_apply_requires_confirmation_and_records_the_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "README.md"
    target.write_text("before\n", encoding="utf-8")
    database = tmp_path / "tasks.sqlite"
    task_id = _completed_apply_task(database, workspace, "before\n", "after\n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    code = _task_apply(argparse.Namespace(database=str(database), task_id=task_id, path="README.md"))

    assert code == 0
    assert target.read_text(encoding="utf-8") == "after\n"
    audit = (workspace / ".dindon" / "applications.jsonl").read_text(encoding="utf-8")
    assert '"type":"apply.applied"' in audit


def test_apply_rejects_a_source_changed_after_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "README.md"
    target.write_text("manual edit\n", encoding="utf-8")
    database = tmp_path / "tasks.sqlite"
    task_id = _completed_apply_task(database, workspace, "before\n", "after\n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    code = _task_apply(argparse.Namespace(database=str(database), task_id=task_id, path="README.md"))

    assert code == 1
    assert target.read_text(encoding="utf-8") == "manual edit\n"
