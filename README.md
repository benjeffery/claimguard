# claimguard

Contract-first runtime for reproducible, gated scientific workflows.
It's like snakemake and pytest had a baby who loves simple scripts and hates workflow files.

Tiny example (2 tasks + gate + contract):

```bash
mkdir -p demo/tasks demo/inputs
printf "1,2,3\n" > demo/inputs/raw.txt
```

`demo/claimguard.json`

```json
{
  "pipeline_name": "tiny_demo",
  "task_roots": ["tasks"]
}
```

`demo/tasks/prepare.py`

```python
CG_TASK = {
    "inputs": ["inputs/raw.txt"],
    "outputs": ["artifacts/prepare/interface.json"],
    "interface_output": "artifacts/prepare/interface.json",
}

from pathlib import Path
import json

root = Path.cwd()
nums = [int(x) for x in (root / "inputs/raw.txt").read_text(encoding="utf-8").split(",")]
out = root / "artifacts/prepare"
out.mkdir(parents=True, exist_ok=True)
(out / "interface.json").write_text(
    json.dumps({"status": "ok", "task": "prepare", "metrics": {"sum": sum(nums)}}),
    encoding="utf-8",
)
```

`demo/tasks/publish.py`

```python
CG_TASK = {
    "inputs": ["artifacts/prepare/interface.json"],
    "outputs": ["artifacts/publish/interface.json"],
    "interface_output": "artifacts/publish/interface.json",
    "gates": [{"name": "sum_positive", "expr": "interface['metrics']['sum'] > 0"}],
}

from pathlib import Path
import json

root = Path.cwd()
prep = json.loads((root / "artifacts/prepare/interface.json").read_text(encoding="utf-8"))
out = root / "artifacts/publish"
out.mkdir(parents=True, exist_ok=True)
(out / "interface.json").write_text(
    json.dumps({"status": "ok", "task": "publish", "metrics": {"sum": prep["metrics"]["sum"]}}),
    encoding="utf-8",
)
```

Run it:

```bash
python3 -m claimguard.cli run --contract demo/claimguard.json --clean-state
```

Why this is awesome:
- **Tasks are just python scripts**: Minimal boilerplate and ceremony
- **Contract-scoped task discovery**: tasks under `task_roots` are discovered, quickly add a script without editing a workflow
- **Declared dependency order**: `publish` could not run before `prepare`, because it declared `artifacts/prepare/interface.json` as an input.
- **Strict I/O policy**: each task could only read/write declared artifacts (`inputs`/`outputs`) unless explicitly exempted, this blocks silent dependency drift and undeclared side effects.
- **Gate-backed promotion**: `publish` output is only promoted if its gate expression evaluates `True` on `interface_output`. If a change upstream broke an assumption/invariant then you know about it and all downstream results are invalidated.
- **Claim classification**: run status and claim certificate are written to `.claimguard/reports/...`. This gives an auditable trail of what passed, what was blocked, and why.

This fixes these kind of things that happen too often:

| Failure class | Typical symptom | claimguard prevention |
|---|---|---|
| Implicit input drift | Same script, different result after unrelated file updates | Every task input is hashed into cache keys and provenance. |
| Silent undeclared dependencies | Script reads helper files/env state not documented anywhere | Strict I/O enforcement blocks undeclared reads unless explicit exemption exists. |
| Race conditions in parallel runs | Partial artifacts, nondeterministic failures, stale mixed outputs | Atomic writes + lock discipline + dependency-safe scheduling. |
| Accidental RNG nondeterminism | Same task/inputs, different outputs from implicit global RNG usage | Deterministic seed policy + optional RNG guard + recorded seed context. |
| Subprocess provenance gaps | Child processes run outside declared contract/audit boundary | Managed subprocess-only execution with inherited context and enforcement. |
| Stale downstream outputs | Upstream changed but downstream reused old artifacts | Freshness engine propagates stale status and blocks invalid promotion. |
| Over-claiming from diagnostics | Exploratory output presented as certified result | Canonical statuses/claim classes separate diagnostic from certifiable runs. |
| Exemption creep | “Temporary” exceptions become permanent hidden policy | Exemptions require metadata and are logged per run for review/audit. |


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
