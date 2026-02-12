#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

CG_TASK = {
    "inputs": [
        "artifacts/feature_stats/interface.json",
        "artifacts/bootstrap_model/interface.json",
        "inputs/fit_config.json"
    ],
    "outputs": [
        "artifacts/fit_model/model.json",
        "artifacts/fit_model/interface.json",
        "artifacts/fit_model/summary.md"
    ],
    "interface_output": "artifacts/fit_model/interface.json",
    "claim_blocking": True,
    "gates": [
        {"name": "status_ok", "expr": "interface['status'] == 'ok'"}
    ]
}


def _task_params() -> dict[str, float]:
    raw = os.environ.get("CG_TASK_PARAMS_JSON", "{}")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        return {}
    return {str(k): float(v) for k, v in obj.items()}


def main() -> int:
    root = Path.cwd()
    stats = json.loads((root / "artifacts/feature_stats/interface.json").read_text(encoding="utf-8"))
    boot = json.loads((root / "artifacts/bootstrap_model/interface.json").read_text(encoding="utf-8"))
    cfg = json.loads((root / "inputs/fit_config.json").read_text(encoding="utf-8"))
    params = _task_params()

    mean = float(stats["metrics"]["mean"])
    std = float(stats["metrics"]["std"])
    bstd = float(boot["metrics"]["bootstrap_std"])
    threshold_default = float(cfg.get("stability_threshold", 0.2))
    threshold = float(params.get("stability_threshold_override", threshold_default))
    score_bias = float(cfg.get("score_bias", 0.0))

    score = abs(mean) / (std + bstd + 1e-9) + score_bias
    primitive_class = "core" if score >= 0.2 else "derived"
    strict_class = "strict" if bstd <= threshold else ("marginal" if bstd <= 1.5 * threshold else "unstable")

    out = root / "artifacts/fit_model"
    out.mkdir(parents=True, exist_ok=True)
    model = {"score": score, "mean": mean, "std": std, "bootstrap_std": bstd}
    (out / "model.json").write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")

    interface = {
        "status": "ok",
        "task": "fit_model",
        "metrics": {**model, "stability_threshold": threshold},
        "classification": {
            "primitive_class": primitive_class,
            "strict_stability_class": strict_class,
        },
    }
    (out / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(
        "\n".join([
            "# fit_model",
            "",
            f"- score: `{score:.6f}`",
            f"- strict_stability_class: `{strict_class}`",
            "",
        ]),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
