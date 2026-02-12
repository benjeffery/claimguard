# all-features-example

Runtime-native all-features example for `claimguard`.

Demonstrates in one contract:
- strict I/O enforcement with read/write exemptions,
- staged execution and atomic output promotion,
- deterministic caching with code + dependency fingerprints,
- parallel DAG execution (`--jobs`),
- strict RNG policy with allowed and blocked branches,
- managed subprocess policy with allowed and blocked branches,
- data-driven map fanout (`CG_TASK["map"]`),
- map branch reduction (`reduce_defects`) consuming shard interfaces,
- diagnostic-only and blocked non-claim branches,
- claim certificate generation.

## Run

From repo root:

```bash
python3 -m claimguard.cli run --contract examples/all-features-example/claimguard.json --clean-state --jobs 4
```

Targeted run (target + dependencies only):

```bash
python3 -m claimguard.cli run --contract examples/all-features-example/claimguard.json --target publish_rank --jobs 4
```

LLM/machine stream:

```bash
python3 -m claimguard.cli run --contract examples/all-features-example/claimguard.json --clean-state --jobs 4 --llm-output
```

Report/certificate:

```bash
python3 -m claimguard.cli report --contract examples/all-features-example/claimguard.json
```

Claim-fail scenario (intentional publish gate failure):

```bash
python3 -m claimguard.cli run --contract examples/all-features-example/claimguard.claim-fail.json --clean-state --jobs 4
```

## Expected highlights

- `claim_class` is `contract-certified`.
- `blocked_probe` is blocked (undeclared read).
- `blocked_write_probe` is blocked (undeclared write).
- `rng_probe_blocked` is blocked (strict RNG, no `allow_rng`).
- `subprocess_probe` succeeds (explicit `allow_subprocess: true`).
- `subprocess_probe_blocked` is blocked (subprocess denied without opt-in).
- `solve_defect` fans out into shard outputs under `artifacts/defect_solve/`.
- `reduce_defects` consumes shard outputs and publishes aggregated metrics.

## Replay and staleness walkthrough

1. Baseline run:

```bash
python3 -m claimguard.cli run --contract examples/all-features-example/claimguard.json --clean-state --jobs 4
```

2. Replay run (expect widespread cache hits):

```bash
python3 -m claimguard.cli run --contract examples/all-features-example/claimguard.json --jobs 4
```

3. Mutate one declared input and rerun (expect stale propagation):

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("examples/all-features-example/inputs/raw_measurements.csv")
lines = p.read_text(encoding="utf-8").strip().splitlines()
header, rows = lines[0], lines[1:]
sample_id, value = rows[0].split(",")
rows[0] = f"{sample_id},{float(value) + 0.01:.2f}"
p.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
PY
python3 -m claimguard.cli run --contract examples/all-features-example/claimguard.json --jobs 4
python3 -m claimguard.cli report --contract examples/all-features-example/claimguard.json
```

In the report, inspect `cache_reason_counts` and per-task `cache_reason` to see replay vs rerun behavior.
