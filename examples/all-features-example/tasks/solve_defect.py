#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

CG_TASK = {'inputs': {'manifest': 'artifacts/defect_manifest/manifest.json',
            'in_interface': 'artifacts/fit_model/interface.json'},
 'outputs': {'interface': 'artifacts/defect_solve/{map_index}_{map_key}_{map_hash}/interface.json',
             'summary': 'artifacts/defect_solve/{map_index}_{map_key}_{map_hash}/summary.md'},
 'interface_output': 'interface',
 'claim_blocking': False,
 'gates': [{'name': 'status_ok', 'expr': "interface['status'] == 'ok'"}],
 'map': {'items_input': 'manifest', 'items_path': 'defects', 'item_name_field': 'defect_id'}}


def main() -> int:
    root = Path.cwd()
    fit = json.loads((root / "artifacts/fit_model/interface.json").read_text(encoding="utf-8"))
    item = json.loads(os.environ["CG_MAP_ITEM_JSON"])
    map_index = os.environ["CG_MAP_INDEX"]
    map_key = os.environ["CG_MAP_KEY"]
    map_hash = os.environ["CG_MAP_HASH"]

    score = float(fit["metrics"]["score"])
    n = int(item["n"])
    t1 = int(item["t1"])
    t2 = int(item["t2"])
    amp = float(item["amplitude"])

    torsion_weight = 1.0 + 0.15 * float(t1 + t2)
    sign_weight = 1.0 + (0.05 if n > 0 else 0.08)
    cost_proxy = float((0.5 + score) * amp * torsion_weight * sign_weight)

    out = root / f"artifacts/defect_solve/{map_index}_{map_key}_{map_hash}"
    out.mkdir(parents=True, exist_ok=True)
    interface = {
        "status": "ok",
        "task": "solve_defect",
        "metrics": {
            "map_index": int(map_index),
            "defect_id": str(item.get("defect_id", map_key)),
            "n": n,
            "t1": t1,
            "t2": t2,
            "amplitude": amp,
            "cost_proxy": cost_proxy,
        },
        "classification": {
            "primitive_class": "core" if (t1 + t2) <= 1 else "derived",
            "strict_stability_class": "strict" if cost_proxy < 2.5 else "marginal",
        },
    }
    (out / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(
        "\n".join([
            f"# solve_defect {item.get('defect_id', map_key)}",
            "",
            f"- cost_proxy: `{cost_proxy:.6f}`",
            "",
        ]),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
