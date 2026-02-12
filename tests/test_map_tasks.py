from __future__ import annotations

import hashlib
import json
from pathlib import Path

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _item_hash(item: object) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:8]


def test_map_task_fanout_and_replay(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    _write(ws / "inputs/items.json", json.dumps({"items": items}, indent=2) + "\n")

    contract = {
        "pipeline_name": "map_task",
        "task_roots": ["tasks"],
    }
    _write(ws / "claimguard.json", json.dumps(contract, indent=2) + "\n")

    cg_task = {
        "inputs": ["inputs/items.json"],
        "outputs": ["artifacts/map/{map_index}_{map_key}_{map_hash}/interface.json"],
        "interface_output": "artifacts/map/{map_index}_{map_key}_{map_hash}/interface.json",
        "gates": [{"name": "status_ok", "expr": "interface['status'] == 'ok'"}],
        "claim_blocking": True,
        "map": {
            "items_input": "inputs/items.json",
            "items_path": "items",
            "item_name_field": "id",
        },
    }
    _write(
        ws / "tasks/map_worker.py",
        "\n".join(
            [
                f"CG_TASK = {repr(cg_task)}",
                "from pathlib import Path",
                "import json",
                "import os",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    idx = os.environ['CG_MAP_INDEX']",
                "    key = os.environ['CG_MAP_KEY']",
                "    h = os.environ['CG_MAP_HASH']",
                "    item = json.loads(os.environ['CG_MAP_ITEM_JSON'])",
                "    out = root / f'artifacts/map/{idx}_{key}_{h}'",
                "    out.mkdir(parents=True, exist_ok=True)",
                "    (out / 'interface.json').write_text(json.dumps({'status':'ok','item':item}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
        + "\n",
    )

    runner = PipelineRunner(ws / "claimguard.json")
    report1 = runner.run(max_workers=2)
    row1 = next(r for r in report1["task_rows"] if r["task"] == "map_worker")
    assert row1["status"] == "ok"
    assert row1["cache_hit"] is False
    assert row1["cache_reason"] == "map_executed"

    for idx, item in enumerate(items):
        key = item["id"]
        h = _item_hash(item)
        p = ws / f"artifacts/map/{idx}_{key}_{h}/interface.json"
        assert p.exists()
        iface = json.loads(p.read_text(encoding="utf-8"))
        assert iface["status"] == "ok"
        assert iface["item"] == item

    report2 = runner.run(max_workers=2)
    row2 = next(r for r in report2["task_rows"] if r["task"] == "map_worker")
    assert row2["status"] == "replay_ok"
    assert row2["cache_hit"] is True
    assert row2["cache_reason"] == "map_all_cache_hit"
