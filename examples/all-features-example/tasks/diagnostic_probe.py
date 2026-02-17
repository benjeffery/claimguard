#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

CG_TASK = {'inputs': {'in_interface': 'artifacts/bootstrap_model/interface.json'},
 'outputs': {'interface': 'artifacts/diagnostic_probe/interface.json',
             'summary': 'artifacts/diagnostic_probe/summary.md'},
 'interface_output': 'interface',
 'claim_blocking': False,
 'gates': [{'name': 'diagnostic_status', 'expr': "interface['status'] == 'diagnostic_only'"}]}


def main() -> int:
    root = Path.cwd()
    boot = json.loads((root / "artifacts/bootstrap_model/interface.json").read_text(encoding="utf-8"))
    bstd = float(boot["metrics"]["bootstrap_std"])

    out = root / "artifacts/diagnostic_probe"
    out.mkdir(parents=True, exist_ok=True)
    interface = {
        "status": "diagnostic_only",
        "task": "diagnostic_probe",
        "metrics": {"bootstrap_std": bstd, "probe_value": bstd * 1.1},
    }
    (out / "interface.json").write_text(json.dumps(interface, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text("# diagnostic_probe\n\nStatus: diagnostic_only\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
