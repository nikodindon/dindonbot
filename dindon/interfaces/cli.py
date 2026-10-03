"""Command-line entry point for DindonBot's first local workflows."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
import ipaddress
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from dindon.kernel import TaskStore
from dindon.llm import OpenAICompatibleClient
from dindon.runtime import TaskEngine


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dindon")
    commands = parser.add_subparsers(dest="command", required=True)

    chat = commands.add_parser("chat", help="parler à un backend LLM compatible OpenAI")
    chat.add_argument("agent", nargs="?", default="chief")
    chat.add_argument("prompt", nargs="*")
    chat.add_argument("--base-url", default=os.environ.get("DINDON_LLM_BASE_URL", "http://127.0.0.1:8080"))
    chat.add_argument("--model", default=os.environ.get("DINDON_MODEL"))

    task = commands.add_parser("task", help="inspecter et créer des tâches persistées")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    create = task_commands.add_parser("create", help="créer une tâche en attente")
    create.add_argument("agent")
    create.add_argument("goal")
    create.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
    listing = task_commands.add_parser("list", help="lister les tâches")
    listing.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
    diff = task_commands.add_parser("diff", help="afficher les changements proposés par le sandbox")
    diff.add_argument("task_id")
    diff.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
    run = task_commands.add_parser("run", help="créer et exécuter une tâche LLM")
    run.add_argument("agent")
    run.add_argument("goal")
    _add_runtime_options(run)
    resume = task_commands.add_parser("resume", help="reprendre une tâche persistée")
    resume.add_argument("task_id")
    _add_runtime_options(resume)
    approvals = task_commands.add_parser("approvals", help="lister les approbations en attente")
    approvals.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
    approve = task_commands.add_parser("approve", help="approuver une action précise et reprendre sa tâche")
    approve.add_argument("approval_id")
    _add_runtime_options(approve)
    deny = task_commands.add_parser("deny", help="refuser une action précise et reprendre sa tâche")
    deny.add_argument("approval_id")
    _add_runtime_options(deny)
    return parser


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
    parser.add_argument(
        "--workspace",
        default=os.environ.get("DINDON_WORKSPACE", "."),
        help="racine autorisée pour les outils de workspace (lecture seule)",
    )
    parser.add_argument(
        "--sandbox-url",
        default=os.environ.get("DINDON_SANDBOX_URL", "http://127.0.0.1:8787"),
        help="URL interne du service sandbox",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DINDON_LLM_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument("--model", default=os.environ.get("DINDON_MODEL"))


def _choose_model(client: OpenAICompatibleClient, configured: str | None) -> str:
    if configured:
        return configured
    models = client.list_models()
    if not models:
        raise RuntimeError("the LLM endpoint did not report any models; pass --model")
    return models[0]


def _local_client(base_url: str) -> OpenAICompatibleClient:
    host = urlsplit(base_url).hostname
    # Compose maps this reserved hostname directly to the host gateway.
    is_local = host in {"localhost", "host.docker.internal"}
    if host:
        try:
            is_local = is_local or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
    if not is_local:
        raise ValueError("the CLI currently permits local LLM endpoints only")
    return OpenAICompatibleClient(base_url)


def _chat(args: argparse.Namespace) -> int:
    try:
        client = _local_client(args.base_url)
        model = _choose_model(client, args.model)
    except Exception as exc:
        print(f"dindon: {exc}", file=sys.stderr)
        return 1
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
        tasks = store.list_tasks()
    if not tasks:
        print("Aucune tâche.")
        return 0
    print(f"{'ID':<30} {'ÉTAT':<18} {'AGENT':<16} OBJECTIF")
    for task in tasks:
        goal = task.goal.replace("\n", " ")
        print(f"{task.id:<30} {task.state.value:<18} {task.agent_id:<16} {goal}")
    return 0


def _task_diff(args: argparse.Namespace) -> int:
    with TaskStore(Path(args.database)) as store:
        task = store.get_task(args.task_id)
        if task is None:
            print(f"dindon: Task not found: {args.task_id}", file=sys.stderr)
            return 1
        proposals: list[dict[str, object]] = []
        for step in store.list_steps(task.id):
            if step.kind != "tool" or not step.result:
                continue
            message = step.result.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                continue
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict) and isinstance(result.get("changes"), list):
                proposals.append(result)
    if not proposals:
        print("Aucun diff de sandbox pour cette tâche.")
        return 0
    for index, proposal in enumerate(proposals, start=1):
        changes = proposal.get("changes", [])
        if not isinstance(changes, list) or not changes:
            print(f"Commande sandbox {index} : aucun changement de fichier.")
            continue
        print(f"Changements proposés par la commande sandbox {index} :")
        for change in changes:
            if isinstance(change, dict):
                print(f"  {change.get('status', 'changed')}: {change.get('path', '?')} {change.get('detail', '')}")
        diff_text = proposal.get("diff")
        if isinstance(diff_text, str) and diff_text:
            print(diff_text, end="" if diff_text.endswith("\n") else "\n")
        if proposal.get("diff_truncated") is True:
            print("[diff tronqué ; la liste des chemins modifiés peut aussi être partielle]")
    return 0


def _task_run(args: argparse.Namespace) -> int:
    try:
        client = _local_client(args.base_url)
        model = _choose_model(client, args.model)
        with TaskStore(Path(args.database)) as store:
            task, answer = TaskEngine(
                store,
                client,
                model=model,
                source="cli",
                workspace=args.workspace,
                sandbox_url=args.sandbox_url,
            ).run(
                agent_id=args.agent,
                goal=args.goal,
            )
    except Exception as exc:
        print(f"dindon: {exc}", file=sys.stderr)
        return 1
    print(f"Tâche {task.id} — {task.state.value}")
    print(answer)
    return 0


def _task_resume(args: argparse.Namespace) -> int:
    try:
        client = _local_client(args.base_url)
        with TaskStore(Path(args.database)) as store:
            task = store.get_task(args.task_id)
            if task is None:
                raise KeyError(f"Task not found: {args.task_id}")
            steps = store.list_steps(task.id)
            saved_model = (
                steps[-1].payload.get("model")
                if steps and isinstance(steps[-1].payload.get("model"), str)
                else None
            )
            model = args.model or saved_model or _choose_model(client, None)
            task, answer = TaskEngine(
                store,
                client,
                model=model,
                source="cli",
                workspace=args.workspace,
                sandbox_url=args.sandbox_url,
            ).resume(task.id)
    except Exception as exc:
        print(f"dindon: {exc}", file=sys.stderr)
        return 1
    print(f"Tâche {task.id} — {task.state.value}")
    print(answer)
    return 0


def _task_approvals(args: argparse.Namespace) -> int:
    with TaskStore(Path(args.database)) as store:
        approvals = store.list_pending_approvals()
    if not approvals:
        print("Aucune approbation en attente.")
        return 0
    for approval in approvals:
        print(f"{approval.id}  tâche {approval.task_id}  expire {approval.expires_at}")
        print(f"  {approval.action_summary}")
    return 0


def _task_decide_approval(args: argparse.Namespace, *, approved: bool) -> int:
    try:
        client = _local_client(args.base_url)
        with TaskStore(Path(args.database)) as store:
            current = store.get_approval(args.approval_id)
            if current is None:
                raise KeyError(f"Approval not found: {args.approval_id}")
            task = store.get_task(current.task_id)
            if task is None:
                raise KeyError(f"Task not found: {current.task_id}")
            steps = store.list_steps(task.id)
            saved_model = next(
                (
                    step.payload.get("model")
                    for step in reversed(steps)
                    if step.kind == "model" and isinstance(step.payload.get("model"), str)
                ),
                None,
            )
            model = args.model or saved_model or _choose_model(client, None)
            approval, task = store.decide_approval(
                args.approval_id,
                approved=approved,
                source="cli",
            )
            engine = TaskEngine(
                store,
                client,
                model=model,
                source="cli",
                workspace=task.input.get("workspace", args.workspace),
                sandbox_url=args.sandbox_url,
            )
            task, answer = engine.resume(task.id)
    except Exception as exc:
        print(f"dindon: {exc}", file=sys.stderr)
        return 1
    print(f"Approbation {approval.id} — {approval.state}")
    print(f"Tâche {task.id} — {task.state.value}")
    print(answer)
    return 0


def main() -> None:
    args = _parser().parse_args()
    if args.command == "chat":
        result = _chat(args)
    elif args.command == "task" and args.task_command == "create":
        result = _task_create(args)
    elif args.command == "task" and args.task_command == "list":
        result = _task_list(args)
    elif args.command == "task" and args.task_command == "diff":
        result = _task_diff(args)
    elif args.command == "task" and args.task_command == "run":
        result = _task_run(args)
    elif args.command == "task" and args.task_command == "resume":
        result = _task_resume(args)
    elif args.command == "task" and args.task_command == "approvals":
        result = _task_approvals(args)
    elif args.command == "task" and args.task_command == "approve":
        result = _task_decide_approval(args, approved=True)
    elif args.command == "task" and args.task_command == "deny":
        result = _task_decide_approval(args, approved=False)
    else:
        raise AssertionError("unhandled command")
    raise SystemExit(result)


if __name__ == "__main__":
    main()
