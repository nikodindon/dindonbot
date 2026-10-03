# Repository Guidelines

## Project Structure

DindonBot is at the start of phase 0. `README.md` is the product and architecture overview. `docs/kernel.md` defines the runtime primitives and invariants, and `docs/security.md` describes the threat model and trust boundaries. Python package code lives under `dindon/`; `dindon/kernel/` contains typed IDs, task and step lifecycle types, event records, and the initial SQLite store. Most source areas, tests, Docker files, and runtime configuration shown in the README are still planned. Keep design material in `docs/` and update the README when decisions change.

## Development and Validation

The package metadata is in `pyproject.toml`; runtime dependencies, an installed CLI, and a test suite have not been added yet. The Docker Compose and `dindon` commands in README describe the intended future deployment and are not currently executable. For documentation contributions, inspect the rendered Markdown, verify links and heading anchors, and confirm examples agree across the README and specifications.

## Writing and Naming

The project documentation is primarily in French; retain that language for substantive edits unless a section already uses another language. Use Markdown headings, concise paragraphs, and tables or fenced code blocks where they clarify architecture. Preserve established terms and identifiers such as `Agent`, `Task`, `ToolCall`, `Approval`, `Artifact`, and typed ID prefixes (`agt_`, `tsk_`, `apr_`). State normative requirements consistently with the RFC 2119 terms already used in `docs/kernel.md`.

## Design and Security Requirements

Treat `docs/kernel.md` and `docs/security.md` as the current design references. Proposed behavior should preserve explicit authorization through the Gardien, least privilege, data provenance, secret isolation, and resumable persistent tasks. Record unresolved design choices as open questions instead of implying they are implemented. Keep examples consistent with the trust boundaries and invariants in those documents.

## Commits and Pull Requests

The repository currently has only its initial commit, so no established commit-message convention can be inferred. Use a short imperative subject that names the change (for example, `Clarify kernel approval invariant`). A pull request should explain the design decision, identify affected documents, link related discussion when available, and note any unresolved questions. Include screenshots only when a rendered visual changed.
