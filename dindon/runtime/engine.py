"""Single-step LLM task execution with durable intent and result checkpoints."""

from __future__ import annotations

from typing import Any

from dindon.kernel import RecoveryAction, Step, StepState, Task, TaskState, TaskStore
from dindon.llm import OpenAICompatibleClient, ProtocolError


class TaskExecutionError(RuntimeError):
    """The task cannot be advanced safely in its current state."""


class TaskEngine:
    """Run an initial no-tools task against an OpenAI-compatible model endpoint.

    The persisted step contains the exact request, so an interrupted model call
    can be retried without repeating an external tool side effect.
    """

    def __init__(
        self,
        store: TaskStore,
        llm: OpenAICompatibleClient,
        *,
        model: str,
        source: str = "runtime",
    ) -> None:
        if not model:
            raise ValueError("model must not be empty")
        self.store = store
        self.llm = llm
        self.model = model
        self.source = source

    def run(self, *, agent_id: str, goal: str) -> tuple[Task, str]:
        task = self.store.create_task(
            agent_id=agent_id,
            goal=goal,
            source=self.source,
        )
        task = self.store.transition_task(
            task.id,
            TaskState.RUNNING,
            source=self.source,
            correlation_id=task.id,
        )
        answer = self._advance(task)
        completed = self.store.get_task(task.id)
        if completed is None:
            raise AssertionError("task disappeared after execution")
        return completed, answer

    def resume(self, task_id: str) -> tuple[Task, str]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if task.state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}:
            raise TaskExecutionError(f"task is already {task.state.value}")
        if task.state in {TaskState.WAITING_APPROVAL}:
            raise TaskExecutionError("task is waiting for an approval flow that is not implemented yet")
        if task.state == TaskState.WAITING_INPUT and self.store.recovery_action(task_id) == RecoveryAction.NEEDS_USER:
            raise TaskExecutionError("task needs a user decision about an interrupted non-idempotent action")
        if task.state != TaskState.RUNNING:
            task = self.store.transition_task(
                task.id,
                TaskState.RUNNING,
                source=self.source,
                correlation_id=task.id,
            )
        answer = self._advance(task)
        updated = self.store.get_task(task.id)
        if updated is None:
            raise AssertionError("task disappeared after execution")
        return updated, answer

    def _advance(self, task: Task) -> str:
        steps = self.store.list_steps(task.id)
        latest: Step | None = steps[-1] if steps else None
        active_model = self.model
        if latest is not None and latest.state == StepState.INTENT:
            action = self.store.recovery_action(task.id)
            if action != RecoveryAction.RETRY_MODEL:
                raise TaskExecutionError(
                    f"cannot resume step safely: recovery action is {action.value}"
                )
            messages = latest.payload.get("messages")
            if not isinstance(messages, list):
                raise ProtocolError("saved model-step intent has no message list")
            stored_model = latest.payload.get("model")
            if not isinstance(stored_model, str) or not stored_model:
                raise ProtocolError("saved model-step intent has no model ID")
            active_model = stored_model
            step = latest
        elif latest is not None and latest.state == StepState.DONE:
            content = (latest.result or {}).get("content")
            if not isinstance(content, str):
                raise ProtocolError("completed model step has no text result")
            if task.state == TaskState.RUNNING:
                task = self.store.transition_task(
                    task.id,
                    TaskState.VERIFYING,
                    source=self.source,
                    correlation_id=task.id,
                )
            if task.state == TaskState.VERIFYING:
                self.store.transition_task(
                    task.id,
                    TaskState.SUCCEEDED,
                    source=self.source,
                    correlation_id=task.id,
                )
            return content
        elif latest is not None and latest.state == StepState.FAILED:
            raise TaskExecutionError("last task step failed; automatic repair is not implemented yet")
        else:
            messages = [
                {
                    "role": "system",
                    "content": "Tu es DindonBot, un assistant personnel. Réponds dans la langue de l'utilisateur.",
                },
                {"role": "user", "content": task.goal},
            ]
            step = self.store.start_step(
                task.id,
                kind="model",
                payload={"model": self.model, "messages": messages},
                source=self.source,
                correlation_id=task.id,
            )

        response = self.llm.chat_completions(
            model=active_model,
            messages=messages,
            stream=False,
        )
        if not isinstance(response, dict):
            raise ProtocolError("non-streaming completion returned a stream")
        choices: Any = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProtocolError("chat completion response has no choice")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ProtocolError("chat completion did not return text content")
        if message.get("tool_calls"):
            raise TaskExecutionError("model requested tools, but tool execution is not implemented yet")
        answer = message["content"]
        self.store.finish_step(
            step.id,
            result={"content": answer, "model": response.get("model", active_model)},
            source=self.source,
            correlation_id=task.id,
        )
        task = self.store.get_task(task.id)
        if task is None:
            raise AssertionError("task disappeared after a committed step")
        if task.state == TaskState.RUNNING:
            task = self.store.transition_task(
                task.id,
                TaskState.VERIFYING,
                source=self.source,
                correlation_id=task.id,
            )
        if task.state == TaskState.VERIFYING:
            task = self.store.transition_task(
                task.id,
                TaskState.SUCCEEDED,
                source=self.source,
                correlation_id=task.id,
            )
        return answer
