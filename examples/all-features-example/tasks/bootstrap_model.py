#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import random
from pathlib import Path

CG_TASK = {'inputs': {'fit_config': 'inputs/fit_config.json',
            'in_payload': 'artifacts/prepare_data/payload.json'},
 'outputs': {'payload': 'artifacts/bootstrap_model/payload.json',
             'interface': 'artifacts/bootstrap_model/interface.json',
             'summary': 'artifacts/bootstrap_model/summary.md'},
 'interface_output': 'interface',
 'claim_blocking': True,
 'gates': [{'name': 'status_ok', 'expr': "interface['status'] == 'ok'"}],
 'allow_rng': True}


def _task_params() -> dict[str, float]:
    raw = os.environ.get("CG_TASK_PARAMS_JSON", "{}")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        return {}
    return {str(k): float(v) for k, v in obj.items()}


def main() -> int:
    root = Path.cwd()
    payload = json.loads((root / "artifacts/prepare_data/payload.json").read_text(encoding="utf-8"))
    params = _task_params()

    centered = [float(x) for x in payload["centered"]]
    n = len(centered)
    num_bootstrap = int(params.get("num_bootstrap", 64))

    means = []
    for _ in range(num_bootstrap):
        sample = [centered[int(random.random() * n) % n] for _ in range(n)]
        means.append(sum(sample) / max(1, len(sample)))

    bmean = sum(means) / max(1, len(means))
    bstd = (sum((m - bmean) ** 2 for m in means) / max(1, len(means))) ** 0.5

    out = root / "artifacts/bootstrap_model"
    out.mkdir(parents=True, exist_ok=True)
    (out / "payload.json").write_text(json.dumps({"bootstrap_means": means}, indent=2) + "\n", encoding="utf-8")
    interface = {
        "status": "ok",
        "task": "bootstrap_model",
        "metrics": {
            "num_bootstrap": num_bootstrap,
            "bootstrap_mean": bmean,
            "bootstrap_std": bstd,
            "seed_used": int(os.environ.get("CG_SEED", "0")),
        },
    }
    (out / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(
        "\n".join([
            "# bootstrap_model",
            "",
            f"- num_bootstrap: `{num_bootstrap}`",
            f"- bootstrap_std: `{bstd:.6f}`",
            "",
        ]),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
