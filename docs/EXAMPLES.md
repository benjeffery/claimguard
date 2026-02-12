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
- `task_start`
- `task_end`
- `run_end`

## 8) Quick Troubleshooting Commands

```bash
python3 -m claimguard.cli doctor --contract claimguard.json
python3 -m claimguard.cli report --contract claimguard.json
```

Per-task process evidence:

- `.claimguard/runs/<run_id>/<task>/process.json`

