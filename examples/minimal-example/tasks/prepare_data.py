#!/usr/bin/env python3
"""Prepare payload from raw inputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

CG_TASK = {
    "inputs": [
        "inputs/raw_measurements.csv",
        "inputs/fit_config.json",
    ],
    "outputs": [
        "artifacts/prepare_data/payload.json",
        "artifacts/prepare_data/interface.json",
    ],
    "interface_output": "artifacts/prepare_data/interface.json",
    "claim_blocking": True,
    "gates": [
        {"name": "status_ok", "expr": "interface['status'] == 'ok'"},
        {"name": "row_count_positive", "expr": "interface['metrics']['row_count'] > 0"}
    ]
}


def main() -> int:
    root = Path.cwd()
    cfg = json.loads((root / "inputs/fit_config.json").read_text(encoding="utf-8"))
    scale = float(cfg.get("scale", 1.0))

    values: list[float] = []
    with (root / "inputs/raw_measurements.csv").open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            values.append(scale * float(row["value"]))

    payload = {
        "values": values,
        "mean": sum(values) / max(1, len(values)),
    }
    out_dir = root / "artifacts/prepare_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "payload.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    interface = {
        "status": "ok",
        "task": "prepare_data",
        "metrics": {
            "row_count": len(values),
            "scale": scale,
            "mean": payload["mean"],
        },
    }
    (out_dir / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

