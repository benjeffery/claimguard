#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

CG_TASK = {'inputs': {'payload': 'artifacts/prepare_data/payload.json',
            'in_interface': 'artifacts/prepare_data/interface.json'},
 'outputs': {'interface': 'artifacts/feature_stats/interface.json',
             'summary': 'artifacts/feature_stats/summary.md'},
 'interface_output': 'interface',
 'claim_blocking': True,
 'gates': [{'name': 'status_ok', 'expr': "interface['status'] == 'ok'"}]}


def main() -> int:
    root = Path.cwd()
    payload = json.loads((root / "artifacts/prepare_data/payload.json").read_text(encoding="utf-8"))
    centered = [float(x) for x in payload["centered"]]
    abs_sorted = sorted(centered, key=lambda x: abs(x), reverse=True)
    top_vals = abs_sorted[:3]

    mean = sum(centered) / max(1, len(centered))
    std = (sum((x - mean) ** 2 for x in centered) / max(1, len(centered))) ** 0.5

    out = root / "artifacts/feature_stats"
    out.mkdir(parents=True, exist_ok=True)
    interface = {
        "status": "ok",
        "task": "feature_stats",
        "metrics": {"mean": mean, "std": std, "max_abs": max(abs(x) for x in centered)},
        "top_values": top_vals,
    }
    (out / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(
        "\n".join([
            "# feature_stats",
            "",
            f"- std: `{std:.6f}`",
            "",
        ]),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
