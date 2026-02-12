# minimal-example

Minimal `claimguard` example with plain Python tasks.

## What it shows

- task auto-discovery from explicit `task_roots` in `claimguard.json`
- task-local `CG_TASK` specs
- dependency inference from `inputs`/`outputs`
- simple gate evaluation from `interface.json`
- claim promotion via `claim_blocking`
- deterministic cache replay

## Run

From example root:

```bash
cd examples/minimal-example
python3 -m claimguard.cli run --clean-state
```

Default output is human-friendly live status.

For machine/LLM consumption, use NDJSON events:

```bash
python3 -m claimguard.cli run --clean-state --llm-output
```

Run again to see replay behavior:

```bash
python3 -m claimguard.cli run
```

From repository root (explicit path):

```bash
python3 -m claimguard.cli run --contract examples/minimal-example/claimguard.json --clean-state
```

## Key output

- `.claimguard/reports/run_report_latest.json` under `examples/minimal-example/`
