# Repository Guidelines

## Project Structure

DindonBot is at the start of phase 0. `README.md` is the product and architecture overview. `docs/kernel.md` defines the runtime primitives and invariants, and `docs/security.md` describes the threat model and trust boundaries. Python package code lives under `dindon/`; `dindon/kernel/` contains typed IDs, task and step lifecycle types, event records, and the initial SQLite store. The Dockerfile and Compose configuration package the CLI with persistent SQLite storage; `dindon/sandbox/` implements bounded command execution in a separate network-isolated container. The llama.cpp service, daemon, and other runtime areas shown in README remain planned. Keep design material in `docs/` and update the README when decisions change.

## Development and Validation

The package metadata is in `pyproject.toml`; there are no runtime dependencies or test suite yet. The task runtime supports Guardian-gated `list_dir`, approval-gated `read_file`, and approval-gated `shell` in a disposable network-isolated container. `dindon task diff` previews proposals; the `applier` Compose profile applies one reviewed text file after interactive confirmation and verifies its base hash. File reads, command output, and diffs use non-exhaustive redaction for known secret formats. Native workspace-write and Git tools remain planned. Run the deterministic development endpoint with `python -m dindon.llm.fake_server`. `docker compose build` builds both images; `docker compose run --rm dindon chat chief` starts a session against the configured local OpenAI-compatible endpoint. CI checks package installation and Python syntax. The daemon and unimplemented `dindon` subcommands shown in README remain planned. For documentation contributions, inspect the rendered Markdown, verify links and heading anchors, and confirm examples agree across the README and specifications.

## Writing and Naming

The project documentation is primarily in French; retain that language for substantive edits unless a section already uses another language. Use Markdown headings, concise paragraphs, and tables or fenced code blocks where they clarify architecture. Preserve established terms and identifiers such as `Agent`, `Task`, `ToolCall`, `Approval`, `Artifact`, and typed ID prefixes (`agt_`, `tsk_`, `apr_`). State normative requirements consistently with the RFC 2119 terms already used in `docs/kernel.md`.

## Design and Security Requirements

Treat `docs/kernel.md` and `docs/security.md` as the current design references. Proposed behavior should preserve explicit authorization through the Gardien, least privilege, data provenance, secret isolation, and resumable persistent tasks. Record unresolved design choices as open questions instead of implying they are implemented. Keep examples consistent with the trust boundaries and invariants in those documents.

## Commits and Pull Requests

Recent commits use short imperative subjects. Keep that pattern (for example, `Clarify kernel approval invariant`). A pull request should explain the design decision, identify affected documents, link related discussion when available, and note any unresolved questions. Include screenshots only when a rendered visual changed.
