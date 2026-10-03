"""Command-line entry point for DindonBot's first local workflows."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from datetime import UTC, datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unicodedata
from urllib.parse import quote
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
    apply = task_commands.add_parser("apply", help="appliquer un fichier après revue et confirmation")
    apply.add_argument("task_id")
    apply.add_argument("path", help="chemin relatif affiché par 'task diff'")
    apply.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
    cancel = task_commands.add_parser("cancel", help="annuler une tâche et révoquer ses approbations")
    cancel.add_argument("task_id")
    cancel.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
    cancel = task_commands.add_parser("cancel", help="annuler une tâche et révoquer ses approbations")
    cancel.add_argument("task_id")
    cancel.add_argument("--database", default=os.environ.get("DINDON_DB", "data/dindon.sqlite"))
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
                path = _safe_terminal_text(str(change.get("path", "?")))
                detail = _safe_terminal_text(str(change.get("detail", "")))
                print(f"  {change.get('status', 'changed')}: {path} {detail}")
        diff_text = proposal.get("diff")
        if isinstance(diff_text, str) and diff_text:
            safe_diff = _safe_terminal_text(diff_text)
            print(safe_diff, end="" if safe_diff.endswith("\n") else "\n")
        if proposal.get("diff_truncated") is True:
            print("[diff tronqué ; la liste des chemins modifiés peut aussi être partielle]")
    return 0


def _safe_terminal_text(value: str) -> str:
    rendered: list[str] = []
    for char in value:
        category = unicodedata.category(char)
        if char in {"\n", "\t"} or category not in {"Cc", "Cf", "Cs"}:
            rendered.append(char)
        elif ord(char) <= 0xFF:
            rendered.append(f"\\x{ord(char):02x}")
        else:
            rendered.append(f"\\u{ord(char):04x}")
    return "".join(rendered)


def _load_apply_proposal(
    database: str, task_id: str, relative_path: str
) -> tuple[str, dict[str, object], str, bool]:
    database_path = Path(database).resolve()
    uri = f"file:{quote(database_path.as_posix(), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.row_factory = sqlite3.Row
        task = connection.execute(
            "SELECT state, input_json FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if task["state"] != "succeeded":
            raise ValueError("task must be completed before applying a proposal")
        workspace = json.loads(task["input_json"]).get("workspace")
        if not isinstance(workspace, str):
            raise ValueError("task has no saved workspace")

        candidates: list[tuple[dict[str, object], str, bool]] = []
        rows = connection.execute(
            "SELECT result FROM steps WHERE task_id = ? AND kind = 'tool' AND state = 'done' ORDER BY idx",
            (task_id,),
        ).fetchall()
        for row in rows:
            result = json.loads(row["result"] or "{}")
            decision = result.get("decision")
            if not isinstance(decision, dict) or decision.get("rule_id") != "human-approval":
                continue
            message = result.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                continue
            try:
                output = json.loads(content)
            except json.JSONDecodeError:
                continue
            changes = output.get("changes") if isinstance(output, dict) else None
            if not isinstance(changes, list):
                continue
            for change in changes:
                if isinstance(change, dict) and change.get("path") == relative_path:
                    candidates.append((
                        change,
                        str(output.get("diff", "")),
                        output.get("diff_truncated") is True,
                    ))
        if len(candidates) != 1:
            raise ValueError("expected exactly one approved sandbox proposal for this path")
        return workspace, candidates[0][0], candidates[0][1], candidates[0][2]
    finally:
        connection.close()


def _append_apply_audit(workspace: Path, event: dict[str, object]) -> None:
    audit_dir = workspace / ".dindon"
    if audit_dir.is_symlink():
        raise ValueError("application audit path cannot be a symlink")
    audit_dir.mkdir(mode=0o700, exist_ok=True)
    audit_file = audit_dir / "applications.jsonl"
    if audit_file.is_symlink():
        raise ValueError("application audit file cannot be a symlink")
    document = json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    descriptor = os.open(
        audit_file,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "ab") as output:
        output.write(document.encode("ascii") + b"\n")
        output.flush()
        os.fsync(output.fileno())


def _task_apply(args: argparse.Namespace) -> int:
    try:
        workspace, change, diff_text, diff_truncated = _load_apply_proposal(
            args.database, args.task_id, args.path
        )
        if diff_truncated:
            raise ValueError("proposal diff is truncated; create a smaller sandbox change before applying")
        relative = Path(args.path)
        parts = relative.parts
        sensitive_names = (
            "secret", "credential", "password", "token", "private-key",
            "id_rsa", "id_ed25519", "keystore", ".pem",
        )
        if (
            not parts
            or relative.is_absolute()
            or "\\" in args.path
            or any(part in {".", ".."} or part.startswith(".") for part in parts)
            or any(word in part.casefold() for part in parts for word in sensitive_names)
        ):
            raise ValueError("proposal path is not allowed")
        if change.get("applyable") is not True or change.get("status") not in {"added", "modified"}:
            raise ValueError(f"proposal cannot be applied: {change.get('detail', 'unsupported change')}")
        new_content = change.get("new_content")
        new_hash = change.get("new_hash")
        if not isinstance(new_content, str) or not isinstance(new_hash, str):
            raise ValueError("proposal has no bounded text content")
        new_bytes = new_content.encode("utf-8")
        if len(new_bytes) > 64 * 1024 or hashlib.sha256(new_bytes).hexdigest() != new_hash:
            raise ValueError("proposal content hash is invalid")

        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("saved workspace is not a directory")
        target = root.joinpath(*parts)
        parent = target.parent
        parent_parts = parts[:-1]
        current = root
        missing_parents: list[Path] = []
        for part in parent_parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("proposal path crosses a symlink")
            if current.exists():
                if not current.is_dir():
                    raise ValueError("proposal parent is not a directory")
            else:
                missing_parents.append(current)
        if target.is_symlink():
            raise ValueError("proposal target is a symlink")

        status = change["status"]
        if status == "modified":
            base_hash = change.get("base_hash")
            if not target.is_file() or not isinstance(base_hash, str):
                raise ValueError("source file is missing or is not regular")
            current_bytes = target.read_bytes()
            if hashlib.sha256(current_bytes).hexdigest() == new_hash:
                print("Cette proposition est déjà appliquée.")
                return 0
            if hashlib.sha256(current_bytes).hexdigest() != base_hash:
                raise ValueError("source file changed since the snapshot; create a fresh task")
            file_mode = stat.S_IMODE(target.stat().st_mode)
        else:
            if target.exists():
                if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == new_hash:
                    print("Cette proposition est déjà appliquée.")
                    return 0
                raise ValueError("new-file target already exists")
            file_mode = 0o644

        print(f"Fichier proposé : {_safe_terminal_text(args.path)} ({status})")
        print("Seul ce fichier sera appliqué ; les autres changements restent des propositions.")
        if diff_text:
            print(_safe_terminal_text(diff_text), end="" if diff_text.endswith("\n") else "\n")
        if not sys.stdin.isatty():
            raise ValueError("application requires an interactive terminal for confirmation")
        answer = input("Appliquer ce fichier au workspace ? [y/N] ").strip().casefold()
        if answer not in {"y", "yes", "o", "oui"}:
            print("Aucun changement appliqué.")
            return 0

        if Path(workspace).resolve(strict=True) != root:
            raise ValueError("workspace root changed during confirmation")
        current = root
        missing_parents = []
        for part in parent_parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("proposal path now crosses a symlink")
            if current.exists():
                if not current.is_dir():
                    raise ValueError("proposal parent is no longer a directory")
            else:
                missing_parents.append(current)
        target = current / parts[-1]
        parent = target.parent
        if target.is_symlink():
            raise ValueError("proposal target became a symlink")
        if status == "modified":
            current_hash = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            if current_hash == new_hash:
                print("Cette proposition est déjà appliquée.")
                return 0
            if current_hash != change.get("base_hash"):
                raise ValueError("source file changed during confirmation")
        elif target.exists():
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == new_hash:
                print("Cette proposition est déjà appliquée.")
                return 0
            raise ValueError("new-file target appeared during confirmation")

        created_dirs: list[Path] = []
        temporary_name: str | None = None
        audit_record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "type": "apply.requested",
            "task_id": args.task_id,
            "path": args.path,
            "status": status,
            "base_hash": change.get("base_hash"),
            "new_hash": new_hash,
        }
        _append_apply_audit(root, audit_record)
        try:
            for directory in reversed(missing_parents):
                directory.mkdir()
                created_dirs.append(directory)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".dindon-apply-", dir=parent)
            with os.fdopen(descriptor, "wb") as output:
                output.write(new_bytes)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_name, file_mode)
            os.replace(temporary_name, target)
            temporary_name = None
            directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
            for directory in created_dirs:
                try:
                    directory.rmdir()
                except OSError:
                    pass
            _append_apply_audit(root, {**audit_record, "type": "apply.failed"})
            raise
        try:
            _append_apply_audit(root, {**audit_record, "type": "apply.applied"})
        except OSError as exc:
            print(
                f"dindon: fichier appliqué, mais journal incomplet dans .dindon/applications.jsonl: {exc}",
                file=sys.stderr,
            )
            return 1
    except Exception as exc:
        print(f"dindon: {exc}", file=sys.stderr)
        return 1
    print(f"Fichier appliqué : {_safe_terminal_text(args.path)}")
    return 0


def _task_cancel(args: argparse.Namespace) -> int:
    try:
        with TaskStore(Path(args.database)) as store:
            task = store.cancel_task(args.task_id, source="cli")
    except Exception as exc:
        print(f"dindon: {exc}", file=sys.stderr)
        return 1
    print(f"Tâche {task.id} — {task.state.value}")
    return 0


def _task_cancel(args: argparse.Namespace) -> int:
    try:
        with TaskStore(Path(args.database)) as store:
            task = store.cancel_task(args.task_id, source="cli")
    except Exception as exc:
        print(f"dindon: {exc}", file=sys.stderr)
        return 1
    print(f"Tâche {task.id} — {task.state.value}")
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
    elif args.command == "task" and args.task_command == "apply":
        result = _task_apply(args)
    elif args.command == "task" and args.task_command == "cancel":
        result = _task_cancel(args)
    elif args.command == "task" and args.task_command == "cancel":
        result = _task_cancel(args)
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
