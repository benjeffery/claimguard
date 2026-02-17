#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

CG_TASK = {'inputs': {'raw_measurements': 'inputs/raw_measurements.csv',
            'fit_config': 'inputs/fit_config.json'},
 'outputs': {'payload': 'artifacts/prepare_data/payload.json',
             'interface': 'artifacts/prepare_data/interface.json',
             'summary': 'artifacts/prepare_data/summary.md'},
 'read_exemptions': ['inputs/exempt_reference.txt'],
 'write_exemptions': ['logs/prepare_data.trace.log'],
 'interface_output': 'interface',
 'claim_blocking': True,
 'gates': [{'name': 'status_ok', 'expr': "interface['status'] == 'ok'"},
           {'name': 'row_count_positive', 'expr': "interface['metrics']['row_count'] > 0"}]}


def main() -> int:
    root = Path.cwd()
    cfg = json.loads((root / "inputs/fit_config.json").read_text(encoding="utf-8"))
    scale = float(cfg.get("scale", 1.0))
    tag = (root / "inputs/exempt_reference.txt").read_text(encoding="utf-8").strip()

    values: list[float] = []
    with (root / "inputs/raw_measurements.csv").open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            values.append(scale * float(row["value"]))

    mean = sum(values) / max(1, len(values))
    centered = [v - mean for v in values]

    out = root / "artifacts/prepare_data"
    out.mkdir(parents=True, exist_ok=True)
    payload = {"values": values, "centered": centered, "mean": mean}
    (out / "payload.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    interface = {
        "status": "ok",
        "task": "prepare_data",
        "metrics": {
            "row_count": len(values),
            "scale": scale,
            "mean": mean,
            "std": (sum((v - mean) ** 2 for v in values) / max(1, len(values))) ** 0.5,
        },
        "notes": {"reference_tag": tag},
    }
    (out / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(
        "\n".join([
            "# prepare_data",
            "",
            f"- row_count: `{len(values)}`",
            f"- mean: `{mean:.6f}`",
            "",
        ]),
        encoding="utf-8",
    )

    log_path = root / "logs/prepare_data.trace.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"prepare_data rows={len(values)} scale={scale}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
