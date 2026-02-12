# claimguard

Contract-first runtime for reproducible, gated scientific workflows.
It's like snakemake and pytest had a baby who loves simple scripts and hates workflow files.


Design intent:
- strict contract-listed I/O enforcement with explicit logged exemptions,
- deterministic replay via contract hashes,
- cache invalidation on task script plus transitive local helper-import changes,
- dependency/environment fingerprinting in cache keys (lockfiles + Python runtime),
- managed task subprocess policy (blocked by default, explicit task opt-in),
- staged execution with output promotion only after successful gate/status checks,
- map-task fanout with data-driven shard multiplicity (`CG_TASK["map"]`),
- optional deterministic RNG policy (`off` / `seeded` / `strict`) with per-task opt-in,
- dependency-safe task-graph (DAG) execution,
- format-agnostic artifact tracking.

Repository layout:
- `claimguard/`: runtime package (`python -m claimguard.cli ...`)
- `docs/`: LLM-focused guides and copy-paste examples
- `examples/minimal-example/`: clean baseline example for MVP runtime
- `examples/all-features-example/`: canonical advanced example exercising all major runtime features
- `templates/AGENTS.md`: starter guidance for coding agents in claimguard-managed projects

Docs:

- `docs/LLM_GUIDE.md`: best practices, strict rules, gate patterns, troubleshooting.
- `docs/EXAMPLES.md`: copy-paste contracts/tasks for common patterns.

CLI output modes:
- default: human-friendly live status (TTY redraw, line-mode fallback for logs)
- `--llm-output`: NDJSON event stream (`run_start` / `task_start` / `task_end` / `run_end`)

CLI commands:
- `claimguard run [--jobs N]` -> execute pipeline
- `claimguard run --target <task>` -> execute only target task(s) and dependencies
- `claimguard report` -> summarize latest run report
- `claimguard doctor` -> validate contract/task graph and input availability

Run artifacts:
- `.claimguard/reports/run_report_latest.json`
- `.claimguard/reports/claim_certificate_latest.json`

Claim-class policy (current):
- `contract-certified`: claim scope tasks are all `ok|replay_ok`.
- `diagnostic`: no blockers in claim scope and at least one claim-scope task is `diagnostic_only`.
- `blocked`: any claim-scope task is `blocked|error|stale|missing` (or unsupported status).

Claim scope:
- all selected `claim_blocking` tasks,
- plus any CLI targets supplied via `--target`.

Gate DSL:
- gates are strict and expression-based (`name` + `expr` only), e.g.
  - `{\"name\": \"status_ok\", \"expr\": \"interface['status'] == 'ok'\"}`.
