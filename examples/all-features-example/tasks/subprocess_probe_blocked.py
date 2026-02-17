#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

CG_TASK = {'inputs': {'in_interface': 'artifacts/fit_model/interface.json'},
 'outputs': {'interface': 'artifacts/subprocess_probe_blocked/interface.json'},
 'interface_output': 'interface',
 'claim_blocking': False,
 'gates': [{'name': 'status_ok', 'expr': "interface['status'] == 'ok'"}]}


def main() -> int:
    root = Path.cwd()
    _ = json.loads((root / "artifacts/fit_model/interface.json").read_text(encoding="utf-8"))
    # Intentional unmanaged subprocess call: this task should be blocked by policy.
    subprocess.run(["python3", "-c", "print('should_be_blocked')"], check=True)

    out = root / "artifacts/subprocess_probe_blocked"
    out.mkdir(parents=True, exist_ok=True)
    (out / "interface.json").write_text(json.dumps({"status": "ok"}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
