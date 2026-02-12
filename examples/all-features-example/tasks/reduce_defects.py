#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CG_TASK = {
    "inputs": [
        "artifacts/defect_manifest/manifest.json",
        "artifacts/defect_solve/{map_index}_{map_key}_{map_hash}/interface.json"
    ],
    "read_exemptions": [
        "artifacts/defect_solve"
    ],
    "outputs": [
        "artifacts/reduce_defects/interface.json",
        "artifacts/reduce_defects/report.json",
        "artifacts/reduce_defects/summary.md"
    ],
    "interface_output": "artifacts/reduce_defects/interface.json",
    "claim_blocking": False,
    "gates": [
        {"name": "status_ok", "expr": "interface['status'] == 'ok'"},
        {"name": "shard_count_positive", "expr": "interface['metrics']['shard_count'] > 0"}
    ]
}


def _safe_name(task_name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.-]+", "_", task_name)


def _short_item_hash(item: Any) -> str:
    raw = json.dumps(item, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _shard_dir(idx: int, item: dict[str, Any]) -> str:
    key = _safe_name(str(item.get("defect_id", idx)))
    item_hash = _short_item_hash(item)
    return f"{idx}_{key}_{item_hash}"


def main() -> int:
    root = Path.cwd()
    manifest = json.loads((root / "artifacts/defect_manifest/manifest.json").read_text(encoding="utf-8"))
    defects = list(manifest.get("defects", []))

    shard_rows: list[dict[str, Any]] = []
    for idx, item in enumerate(defects):
        rel = f"artifacts/defect_solve/{_shard_dir(idx, item)}/interface.json"
        obj = json.loads((root / rel).read_text(encoding="utf-8"))
        shard_rows.append(obj)

    costs = [float(row["metrics"]["cost_proxy"]) for row in shard_rows]
    mean_cost = sum(costs) / max(1, len(costs))
    min_cost = min(costs)
    max_cost = max(costs)

    report = {
        "status": "ok",
        "task": "reduce_defects",
        "shards": shard_rows,
        "metrics": {
            "shard_count": len(shard_rows),
            "mean_cost": mean_cost,
            "min_cost": min_cost,
            "max_cost": max_cost,
        },
    }

    out = root / "artifacts/reduce_defects"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    interface = {
        "status": "ok",
        "task": "reduce_defects",
        "metrics": {
            "shard_count": len(shard_rows),
            "mean_cost": mean_cost,
            "min_cost": min_cost,
            "max_cost": max_cost,
        },
    }
    (out / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(
        "\n".join(
            [
                "# reduce_defects",
                "",
                f"- shard_count: `{len(shard_rows)}`",
                f"- mean_cost: `{mean_cost:.6f}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
