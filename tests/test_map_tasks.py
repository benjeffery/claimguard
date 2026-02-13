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
    events1: list[dict[str, object]] = []
    report1 = runner.run(max_workers=2, event_emitter=events1.append)
    row1 = next(r for r in report1["task_rows"] if r["task"] == "map_worker")
    assert row1["status"] == "ok"
    assert row1["cache_hit"] is False
    assert row1["cache_reason"] == "map_executed"
    map_progress = [e for e in events1 if str(e.get("event", "")) == "map_progress"]
    assert map_progress
    assert map_progress[0]["task"] == "map_worker"
    assert int(map_progress[0]["shard_total"]) == len(items)
    assert int(map_progress[-1]["shard_done"]) == len(items)

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

    _write(ws / "inputs/items.json", json.dumps({"items": items[:2]}, indent=2) + "\n")
    report3 = runner.run(max_workers=2)
    row3 = next(r for r in report3["task_rows"] if r["task"] == "map_worker")
    assert row3["status"] == "ok"

    stale_item = items[2]
    stale_hash = _item_hash(stale_item)
    stale_output = ws / f"artifacts/map/2_{stale_item['id']}_{stale_hash}/interface.json"
    assert not stale_output.exists()
    assert stale_output.with_name(f"{stale_output.name}.stale").exists()


def test_map_task_allow_empty_marks_previous_outputs_stale(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    items = [{"id": "a"}, {"id": "b"}]
    _write(ws / "inputs/items.json", json.dumps({"items": items}, indent=2) + "\n")

    contract = {
        "pipeline_name": "map_task_allow_empty",
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
            "allow_empty": True,
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
                "    out = root / f'artifacts/map/{idx}_{key}_{h}'",
                "    out.mkdir(parents=True, exist_ok=True)",
                "    (out / 'interface.json').write_text(json.dumps({'status':'ok'}), encoding='utf-8')",
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

    first_output = ws / f"artifacts/map/0_a_{_item_hash(items[0])}/interface.json"
    second_output = ws / f"artifacts/map/1_b_{_item_hash(items[1])}/interface.json"
    assert first_output.exists()
    assert second_output.exists()

    _write(ws / "inputs/items.json", json.dumps({"items": []}, indent=2) + "\n")
    report2 = runner.run(max_workers=2)
    row2 = next(r for r in report2["task_rows"] if r["task"] == "map_worker")
    assert row2["status"] == "diagnostic_only"
    assert row2["cache_reason"] == "map_empty_allowed"

    assert not first_output.exists()
    assert not second_output.exists()
    assert first_output.with_name(f"{first_output.name}.stale").exists()
    assert second_output.with_name(f"{second_output.name}.stale").exists()
