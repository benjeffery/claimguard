#!/usr/bin/env python3
"""Publish final ranking artifact."""

from __future__ import annotations

import json
from pathlib import Path

CG_TASK = {'inputs': {'model': 'artifacts/fit_model/model.json',
            'in_interface': 'artifacts/fit_model/interface.json'},
 'outputs': {'ranking': 'artifacts/publish_rank/ranking.json',
             'interface': 'artifacts/publish_rank/interface.json',
             'summary': 'artifacts/publish_rank/summary.md'},
 'interface_output': 'interface',
 'claim_blocking': True,
 'gates': [{'name': 'status_ok', 'expr': "interface['status'] == 'ok'"},
           {'name': 'stability_gate',
            'expr': "interface['classification']['strict_stability_class'] == 'strict'"},
           {'name': 'primitive_allowlist',
            'expr': "interface['classification']['primitive_class'] in ['core', 'derived']"}]}


def main() -> int:
    root = Path.cwd()
    model = json.loads((root / "artifacts/fit_model/model.json").read_text(encoding="utf-8"))
    fit_iface = json.loads((root / "artifacts/fit_model/interface.json").read_text(encoding="utf-8"))

    ranking = {
        "score": float(model["score"]),
        "classification": fit_iface["classification"],
        "top_k_features": [
            {"rank": 1, "feature": "f1", "value": float(model["mean"])},
            {"rank": 2, "feature": "f2", "value": float(model["adjusted_std"])},
        ],
    }

    out_dir = root / "artifacts/publish_rank"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ranking.json").write_text(json.dumps(ranking, indent=2) + "\n", encoding="utf-8")

    interface = {
        "status": "ok",
        "task": "publish_rank",
        "classification": dict(fit_iface["classification"]),
        "metrics": {
            "score": float(model["score"]),
            "rank_count": len(ranking["top_k_features"]),
        },
    }
    (out_dir / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")

    summary = "\n".join(
        [
            "# publish_rank",
            "",
            f"- score: `{float(model['score']):.6f}`",
            f"- primitive_class: `{fit_iface['classification']['primitive_class']}`",
            f"- strict_stability_class: `{fit_iface['classification']['strict_stability_class']}`",
            "",
        ]
    )
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

