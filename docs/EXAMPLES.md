# claimguard Examples Cookbook

Copy-paste patterns for common workflows.

## 1) Minimal Linear Pipeline

`claimguard.json`

```json
{
  "pipeline_name": "linear_demo",
  "task_roots": ["tasks"]
}
```

`tasks/prepare.py`

```python
CG_TASK = {
    "inputs": ["inputs/raw.txt"],
    "outputs": ["artifacts/prepare/interface.json"],
    "interface_output": "artifacts/prepare/interface.json",
    "gates": [{"name": "status_ok", "expr": "interface['status'] == 'ok'"}],
}
```

`tasks/publish.py`

```python
CG_TASK = {
    "inputs": ["artifacts/prepare/interface.json"],
    "outputs": ["artifacts/publish/interface.json"],
    "interface_output": "artifacts/publish/interface.json",
    "gates": [{"name": "status_ok", "expr": "interface['status'] == 'ok'"}],
}
```

Run:

```bash
python3 -m claimguard.cli run --contract claimguard.json
```

## 2) Diagnostic Task (Non-Claim Blocking)

```python
CG_TASK = {
    "inputs": ["artifacts/fit/interface.json"],
    "outputs": ["artifacts/diag/interface.json"],
    "interface_output": "artifacts/diag/interface.json",
    "claim_blocking": False,
    "gates": [{"name": "is_diag", "expr": "interface['status'] == 'diagnostic_only'"}],
}
```

Use this for probes/analysis that should not block certification.

## 3) Map Fanout Task

Map tasks let one template spawn shards from a manifest list.

```python
CG_TASK = {
    "inputs": ["inputs/items.json"],
    "outputs": ["artifacts/map/{map_index}_{map_key}_{map_hash}/interface.json"],
    "interface_output": "artifacts/map/{map_index}_{map_key}_{map_hash}/interface.json",
    "gates": [{"name": "status_ok", "expr": "interface['status'] == 'ok'"}],
    "map": {
        "items_input": "inputs/items.json",
        "items_path": "items",
        "item_name_field": "id"
    }
}
```

Shard env vars available in task runtime:

- `CG_MAP_INDEX`
- `CG_MAP_KEY`
- `CG_MAP_HASH`
- `CG_MAP_ITEM_JSON`
- `CG_MAP_PARENT_TASK`

## 4) Strict RNG Policy

Contract:

```json
{
  "pipeline_name": "rng_demo",
  "task_roots": ["tasks"],
  "rng_policy": "strict",
  "rng_seed": 1234
}
```

Task that needs RNG:

```python
CG_TASK = {
    "inputs": ["inputs/in.txt"],
    "outputs": ["artifacts/sample/interface.json"],
    "interface_output": "artifacts/sample/interface.json",
    "allow_rng": True,
    "gates": [{"name": "status_ok", "expr": "interface['status'] == 'ok'"}],
}
```

If `allow_rng` is omitted under strict policy, RNG calls are blocked.

## 5) Subprocess Policy

Subprocesses are blocked by default.

Task enabling subprocess:

```python
CG_TASK = {
    "inputs": ["inputs/in.txt"],
    "outputs": ["artifacts/sub/interface.json"],
    "interface_output": "artifacts/sub/interface.json",
    "allow_subprocess": True,
    "gates": [{"name": "status_ok", "expr": "interface['status'] == 'ok'"}],
}
```

## 6) Targeted Execution

Run only one task and its dependency closure:

```bash
python3 -m claimguard.cli run --contract claimguard.json --target publish_rank
```

Use this for faster iteration while preserving correctness.

## 7) LLM-Friendly Event Stream

Emit NDJSON events:

```bash
python3 -m claimguard.cli run --contract claimguard.json --llm-output
```

Events:

- `run_start`
- `task_summary` (every 60s + final summary at run end: `current_task`, `task_started`, `task_done`, `task_left`, `task_running`; includes `map_progress` when a map task is in progress)
- `task_summary` also includes `current_task_progress` when the running task emits progress
- `run_end`

## 8) Quick Troubleshooting Commands

```bash
python3 -m claimguard.cli doctor --contract claimguard.json
python3 -m claimguard.cli report --contract claimguard.json
```

Per-task process evidence:

- `.claimguard/runs/<run_id>/<task>/process.json`

## 9) Optional Task Progress Updates

Tasks can emit lightweight live progress with one helper import:

```python
from claimguard.progress import update

update(done=12, total=100, phase="fit", message="epoch 12")
```

Supported fields:

- `done` / `total` (integer counts)
- `fraction` (0..1 float, optional if counts are provided)
- `phase` and `message` (optional strings)
- `eta_s` (optional seconds)
- `meta` (optional JSON-serializable object)

## 10) Task Resource Contract (CPU Thread Budgeting)

Declare per-task resource preferences inside `CG_TASK`:

```python
CG_TASK = {
    "inputs": ["inputs/in.txt"],
    "outputs": ["artifacts/train/interface.json"],
    "interface_output": "artifacts/train/interface.json",
    "resources": {
        "cpu_threads_min": 1,
        "cpu_threads_pref": 4,
        "cpu_threads_max": 8,
        "memory_gb_max": 6.0
    }
}
```

Scheduler behavior:

- global CPU-thread budget is `--jobs` (or available cores by default)
- each runnable task gets at least `cpu_threads_min`
- extra threads are allocated toward `cpu_threads_pref`, then `cpu_threads_max`
- default (no `resources` block): `cpu_threads_min=cpu_threads_pref=cpu_threads_max=1`

Worker env exported per task:

- `CG_CPU_THREADS`
- `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, `NUMEXPR_NUM_THREADS`, `GOTO_NUM_THREADS`

