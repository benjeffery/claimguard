#!/usr/bin/env python3
"""Optional non-claim-blocking diagnostic task."""

from __future__ import annotations

import json
from pathlib import Path

CG_TASK = {'inputs': {'in_interface': 'artifacts/fit_model/interface.json'},
 'outputs': {'interface': 'artifacts/diagnostic_probe/interface.json'},
 'interface_output': 'interface',
 'claim_blocking': False,
 'gates': [{'name': 'diagnostic_status', 'expr': "interface['status'] == 'diagnostic_only'"}]}


def main() -> int:
    root = Path.cwd()
    fit_iface = json.loads((root / "artifacts/fit_model/interface.json").read_text(encoding="utf-8"))
    out_dir = root / "artifacts/diagnostic_probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    interface = {
        "status": "diagnostic_only",
        "task": "diagnostic_probe",
        "metrics": {
            "score": float(fit_iface["metrics"]["score"]),
        },
    }
    (out_dir / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

