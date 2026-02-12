# claimguard LLM Guide

This guide is optimized for coding agents and fast human review.

## Purpose

Use `claimguard` when you want:

- strict contract-first execution,
- reproducible outputs and cache behavior,
- machine-checkable claim gating,
- hard failure on undeclared I/O.

## 60-Second Workflow

1. Create `claimguard.json` at repo root.
2. Set explicit `task_roots` (required).
3. Add Python task files with top-level `CG_TASK`.
4. Persist task interface to declared `interface_output`.
5. Add gates as strict expressions (`name` + `expr`).
6. Run:

```bash
python3 -m claimguard.cli run --contract claimguard.json --clean-state
```

7. Inspect:

- `.claimguard/reports/run_report_latest.json`
- `.claimguard/reports/claim_certificate_latest.json`

## Contract: Required and Allowed Keys

Top-level contract keys are strict. Unknown keys are rejected.

Allowed keys:

- `pipeline_name` (string, optional)
- `task_roots` (non-empty list of relative paths, required)
- `task_params` (object map, optional)
- `rng_policy` (`off|seeded|strict`, optional)
- `rng_seed` (integer, optional)

Minimal contract:

```json
{
  "pipeline_name": "my_pipeline",
  "task_roots": ["tasks"]
}
```

Path rules:

- Paths must be relative.
- Absolute paths are rejected.
- Parent traversal (`..`) is rejected.

## Task Spec (`CG_TASK`)

Every executable task must define top-level `CG_TASK`.

Required keys:

- `inputs`: list of relative paths
- `outputs`: list of relative paths
- `interface_output`: one of `outputs`

Optional keys:

- `gates`: list of `{ "name": str, "expr": str }`
- `claim_blocking`: bool (default `true`)
- `read_exemptions`: list of relative paths
- `write_exemptions`: list of relative paths
- `allow_subprocess`: bool (default `false`)
- `allow_rng`: bool (default `false`)
- `map`: object for fanout
- `params`: object

Minimal task skeleton:

```python
CG_TASK = {
    "inputs": ["inputs/raw.csv"],
    "outputs": ["artifacts/prepare/interface.json"],
    "interface_output": "artifacts/prepare/interface.json",
    "gates": [{"name": "status_ok", "expr": "interface['status'] == 'ok'"}],
}

from pathlib import Path
import json

def main() -> int:
    root = Path.cwd()
    (root / "inputs/raw.csv").read_text(encoding="utf-8")
    out = root / "artifacts/prepare"
    out.mkdir(parents=True, exist_ok=True)
    (out / "interface.json").write_text(
        json.dumps({"status": "ok", "task": "prepare"}),
        encoding="utf-8",
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

## Gate Expression Rules

Gates are expression-only. Legacy gate styles are rejected.

Valid gate shape:

```python
{"name": "stability_gate", "expr": "interface['classification']['strict_stability_class'] == 'strict'"}
```

Rules:

- expression must compile,
- expression result must be boolean,
- only `interface` may be referenced.

Common gate examples:

```python
{"name": "status_ok", "expr": "interface['status'] == 'ok'"}
{"name": "score_min", "expr": "interface['metrics']['score'] >= 0.9"}
{"name": "class_allowlist", "expr": "interface['classification']['kind'] in ['core', 'derived']"}
{"name": "nonempty", "expr": "len(interface['items']) > 0"}
```

## Best Practices

- Persist all claim-relevant values in `interface_output`.
- Keep task outputs deterministic for fixed inputs/params.
- Keep diagnostic/probe tasks `claim_blocking: false`.
- Use `CG_TMPDIR` for scratch files.
- Use declared artifacts as interfaces between tasks; avoid implicit shared state.
- Keep `gates` simple and explicit.
- Prefer one task per clear responsibility.

## Anti-Patterns

- Reading undeclared files.
- Writing undeclared files.
- Using subprocesses without `allow_subprocess: true`.
- Using RNG in strict mode without `allow_rng: true`.
- Encoding claim-relevant evidence only in stdout/stderr.
- Relying on hidden imports or path hacks (`sys.path.insert(...)`).

## Failure Modes and Fast Diagnosis

| Symptom | Likely cause | Fix |
|---|---|---|
| `contract must define non-empty string list task_roots` | Missing/invalid `task_roots` | Add valid non-empty list of relative directories |
| `unsupported contract key(s)` | Unknown top-level contract fields | Remove unsupported fields |
| `gate[...] must have exactly keys ['expr','name']` | Legacy gate DSL in task | Convert to expression gate |
| Task blocked with `gate_failure` | Gate expression false or non-bool | Fix task interface data or gate expression |
| Task blocked with `nonzero_exit:*` | Task runtime failure or policy violation | Check `.claimguard/runs/.../process.json` |
| Read/write denied in worker | Undeclared I/O | Add required path to inputs/outputs/exemptions |
| `unsupported_interface_status:*` | Interface status not `ok|diagnostic_only` | Emit one of supported statuses |

## Recommended Agent Loop

1. Edit task/contract.
2. Run `claimguard run`.
3. If blocked, inspect `run_report_latest.json` and per-task `process.json`.
4. Fix task contract/interface mismatch.
5. Re-run and confirm claim class in `claim_certificate_latest.json`.

