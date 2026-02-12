# AGENTS.md (claimguard template)

This repository uses **claimguard** for contract-first, claim-safe workflow execution.

Primary references:

- `docs/LLM_GUIDE.md`
- `docs/EXAMPLES.md`

## Core rules for coding agents

- Prefer plain, readable Python task scripts. Do not wrap science logic in custom frameworks.
- Every executable task must define a top-level `CG_TASK` dict.
- Declare all task dependencies explicitly in `CG_TASK["inputs"]`.
- Declare all promoted task outputs explicitly in `CG_TASK["outputs"]`.
- Any artifact used by gates or downstream claim-relevant logic must be persisted to a declared `interface.json`.
- Do not rely on implicit file reads/writes. Undeclared I/O is blocked by design.
- If an undeclared read/write is required, add an explicit `read_exemptions` / `write_exemptions` entry and keep it minimal.
- Use subprocesses only when the task explicitly sets `allow_subprocess: true`.
- Under strict RNG policy, random calls require `allow_rng: true` in `CG_TASK`.

## Claim discipline

- claim-blocking is default-on. Set `claim_blocking: false` only for explicitly non-claim branches (diagnostic/probes/supporting outputs).
- Gate conditions in `CG_TASK["gates"]` are authoritative for pass/fail at task level.
- Gate conditions use strict expression DSL (`name` + `expr` only).
- Keep diagnostic and exploratory tasks as non-claim-blocking unless there is a clear reason otherwise.
- Do not treat `diagnostic_only` artifacts as certified claims.

## Task authoring checklist

1. Define `CG_TASK` with `inputs`, `outputs`, `interface_output`, `gates`, and `claim_blocking`.
2. Persist a machine-readable interface file (`interface.json`) with at least:
   - `status`
   - `task`
   - `metrics`
   - optional `classification`
3. Keep task outputs deterministic for fixed inputs and params.
4. Keep task-local temp/scratch data in runtime-managed temp paths when possible.

## Typical `CG_TASK` skeleton

```python
CG_TASK = {
    "inputs": [
        "artifacts/upstream/interface.json"
    ],
    "outputs": [
        "artifacts/my_task/interface.json",
        "artifacts/my_task/summary.md"
    ],
    "interface_output": "artifacts/my_task/interface.json",
    "claim_blocking": True,
    "gates": [
        {"name": "status_ok", "expr": "interface['status'] == 'ok'"}
    ]
}
```

## Runtime commands

From repo root:

```bash
python3 -m claimguard.cli run --contract claimguard.json --jobs 4
python3 -m claimguard.cli report --contract claimguard.json
python3 -m claimguard.cli doctor --contract claimguard.json
```

## Anti-patterns to avoid

- Hidden dependencies via `sys.path.insert(...)` hacks.
- Reading command strings/log text from prior tasks as implicit dependencies.
- Gate-relevant values existing only in memory (must be persisted in declared interface artifacts).
- Treating blocked or diagnostic outputs as certified evidence.
