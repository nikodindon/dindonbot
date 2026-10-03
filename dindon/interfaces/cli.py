"""Command-line entry point for DindonBot's first local workflows."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
import os
from pathlib import Path
import sys

from dindon.kernel import TaskStore
from dindon.llm import OpenAICompatibleClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dindon")
    commands = parser.add_subparsers(dest="command", required=True)

    chat = commands.add_parser("chat", help="parler à un backend LLM compatible OpenAI")
    chat.add_argument("agent", nargs="?", default="chief")
    chat.add_argument("prompt", nargs="*")
    chat.add_argument("--base-url", default=os.environ.get("DINDON_LLM_BASE_URL", "http://127.0.0.1:8080"))
    chat.add_argument("--model", default=os.environ.get("DINDON_MODEL"))
    chat.add_argument("--api-key", default=os.environ.get("DINDON_LLM_API_KEY"))

    task = commands.add_parser("task", help="inspecter et créer des tâches persistées")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    create = task_commands.add_parser("create", help="créer une tâche en attente")
    create.add_argument("agent")
    create.add_argument("goal")
    create.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
    listing = task_commands.add_parser("list", help="lister les tâches")
    listing.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
    return parser


def _choose_model(client: OpenAICompatibleClient, configured: str | None) -> str:
    if configured:
        return configured
    models = client.list_models()
    if not models:
        raise RuntimeError("the LLM endpoint did not report any models; pass --model")
    return models[0]


def _chat(args: argparse.Namespace) -> int:
    client = OpenAICompatibleClient(args.base_url, api_key=args.api_key)
    model = _choose_model(client, args.model)
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "You are DindonBot, a helpful personal assistant. Reply in the user's language.",
        }
    ]
    prompt = " ".join(args.prompt).strip()
    if prompt:
        return _send_message(client, model, messages, prompt)

    print(f"DindonBot ({args.agent}) — modèle {model}. Ctrl-D ou Ctrl-C pour quitter.")
    while True:
        try:
            user_text = input("toi> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user_text:
            continue
        _send_message(client, model, messages, user_text)


def _send_message(
    client: OpenAICompatibleClient,
    model: str,
    messages: list[dict[str, str]],
    user_text: str,
) -> int:
    messages.append({"role": "user", "content": user_text})
    try:
        stream = client.chat_completions(
            model=model,
            messages=messages,
            stream=True,
        )
        if not isinstance(stream, Iterator):
            raise RuntimeError("the LLM client did not return a stream")
        answer_parts: list[str] = []
        for chunk in stream:
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            text = delta.get("content")
            if isinstance(text, str):
                answer_parts.append(text)
                print(text, end="", flush=True)
        print()
        answer = "".join(answer_parts)
        messages.append({"role": "assistant", "content": answer})
        return 0
    except Exception as exc:
        messages.pop()
        print(f"dindon: {exc}", file=sys.stderr)
        return 1


def _task_create(args: argparse.Namespace) -> int:
    with TaskStore(Path(args.database)) as store:
        task = store.create_task(agent_id=args.agent, goal=args.goal, source="cli")
    print(task.id)
    return 0


def _task_list(args: argparse.Namespace) -> int:
    with TaskStore(Path(args.database)) as store:
        tasks = store.list_incomplete_tasks()
    if not tasks:
        print("Aucune tâche en cours.")
        return 0
    print(f"{'ID':<30} {'ÉTAT':<18} {'AGENT':<16} OBJECTIF")
    for task in tasks:
        goal = task.goal.replace("\n", " ")
        print(f"{task.id:<30} {task.state.value:<18} {task.agent_id:<16} {goal}")
    return 0


def main() -> None:
    args = _parser().parse_args()
    if args.command == "chat":
        result = _chat(args)
    elif args.command == "task" and args.task_command == "create":
        result = _task_create(args)
    elif args.command == "task" and args.task_command == "list":
        result = _task_list(args)
    else:
        raise AssertionError("unhandled command")
    raise SystemExit(result)


if __name__ == "__main__":
    main()
