#!/usr/bin/env python3
"""Compute a tiny model and classification."""

from __future__ import annotations

import json
import os
from pathlib import Path

CG_TASK = {
    "inputs": [
        "artifacts/prepare_data/payload.json",
        "artifacts/prepare_data/interface.json",
        "inputs/fit_config.json",
    ],
    "outputs": [
        "artifacts/fit_model/model.json",
        "artifacts/fit_model/interface.json",
    ],
    "interface_output": "artifacts/fit_model/interface.json",
    "claim_blocking": True,
    "gates": [
        {"name": "status_ok", "expr": "interface['status'] == 'ok'"}
    ]
}


def _load_task_params() -> dict[str, float]:
    raw = os.environ.get("CG_TASK_PARAMS_JSON", "{}")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        return {}
    return {str(k): float(v) for k, v in obj.items()}


def main() -> int:
    root = Path.cwd()
    payload = json.loads((root / "artifacts/prepare_data/payload.json").read_text(encoding="utf-8"))
    cfg = json.loads((root / "inputs/fit_config.json").read_text(encoding="utf-8"))
    params = _load_task_params()

    values = [float(v) for v in payload["values"]]
    mean = float(payload["mean"])
    var = sum((x - mean) ** 2 for x in values) / max(1, len(values))
    std = var ** 0.5

    stability_threshold = float(cfg.get("stability_threshold", 0.35))
    stability_bias = float(params.get("stability_bias", 0.0))
    adjusted_std = max(0.0, std + stability_bias)
    score = abs(mean) / (adjusted_std + 1e-9)

    primitive_class = "core" if score >= 0.2 else "derived"
    strict_stability_class = "strict" if adjusted_std <= stability_threshold else "marginal"

    out_dir = root / "artifacts/fit_model"
    out_dir.mkdir(parents=True, exist_ok=True)
    model = {
        "score": score,
        "mean": mean,
        "std": std,
        "adjusted_std": adjusted_std,
    }
    (out_dir / "model.json").write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")

    interface = {
        "status": "ok",
        "task": "fit_model",
        "metrics": model,
        "classification": {
            "primitive_class": primitive_class,
            "strict_stability_class": strict_stability_class,
        },
    }
    (out_dir / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

