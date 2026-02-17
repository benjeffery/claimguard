#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

CG_TASK = {'inputs': {'interface_2': 'artifacts/feature_stats/interface.json',
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
    fit = json.loads((root / "artifacts/fit_model/interface.json").read_text(encoding="utf-8"))
    stats = json.loads((root / "artifacts/feature_stats/interface.json").read_text(encoding="utf-8"))

    ranking = {
        "score": float(fit["metrics"]["score"]),
        "classification": dict(fit["classification"]),
        "top_k_features": [
            {"rank": i + 1, "feature": f"f{i+1}", "value": float(v)}
            for i, v in enumerate(stats.get("top_values", []))
        ],
    }

    out = root / "artifacts/publish_rank"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ranking.json").write_text(json.dumps(ranking, indent=2) + "\n", encoding="utf-8")
    interface = {
        "status": "ok",
        "task": "publish_rank",
        "classification": dict(fit["classification"]),
        "metrics": {"score": float(ranking["score"]), "rank_count": len(ranking["top_k_features"])}
    }
    (out / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(
        "\n".join([
            "# publish_rank",
            "",
            f"- score: `{float(ranking['score']):.6f}`",
            f"- strict_stability_class: `{fit['classification']['strict_stability_class']}`",
            "",
        ]),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
