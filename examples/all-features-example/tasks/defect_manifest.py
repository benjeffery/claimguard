#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

CG_TASK = {
    "inputs": [
        "artifacts/fit_model/interface.json",
        "artifacts/prepare_data/interface.json",
        "inputs/fit_config.json"
    ],
    "outputs": [
        "artifacts/defect_manifest/manifest.json",
        "artifacts/defect_manifest/interface.json",
        "artifacts/defect_manifest/summary.md"
    ],
    "interface_output": "artifacts/defect_manifest/interface.json",
    "claim_blocking": False,
    "gates": [
        {"name": "status_ok", "expr": "interface['status'] == 'ok'"},
        {"name": "defect_count_positive", "expr": "interface['metrics']['defect_count'] > 0"}
    ]
}


def main() -> int:
    root = Path.cwd()
    fit = json.loads((root / "artifacts/fit_model/interface.json").read_text(encoding="utf-8"))
    prep = json.loads((root / "artifacts/prepare_data/interface.json").read_text(encoding="utf-8"))
    cfg = json.loads((root / "inputs/fit_config.json").read_text(encoding="utf-8"))

    count = int(cfg.get("manifest_count", 4))
    score = float(fit["metrics"]["score"])
    row_count = int(prep["metrics"]["row_count"])
    torsion_cycle = [(0, 0), (1, 0), (0, 1), (1, 1)]

    defects = []
    for i in range(count):
        t1, t2 = torsion_cycle[i % len(torsion_cycle)]
        defects.append(
            {
                "defect_id": f"d{i+1:03d}",
                "n": 1 if i % 2 == 0 else -1,
                "t1": int(t1),
                "t2": int(t2),
                "amplitude": float((i + 1) * (0.5 + score)),
            }
        )

    manifest = {
        "status": "ok",
        "task": "defect_manifest",
        "defects": defects,
        "metadata": {"score": score, "row_count": row_count},
    }

    out = root / "artifacts/defect_manifest"
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    interface = {
        "status": "ok",
        "task": "defect_manifest",
        "metrics": {"defect_count": len(defects), "score": score, "row_count": row_count},
    }
    (out / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(
        "\n".join([
            "# defect_manifest",
            "",
            f"- defect_count: `{len(defects)}`",
            "",
        ]),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
