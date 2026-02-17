#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

CG_TASK = {'inputs': {'in_interface': 'artifacts/fit_model/interface.json'},
 'outputs': {'interface': 'artifacts/subprocess_probe/interface.json'},
 'interface_output': 'interface',
 'claim_blocking': False,
 'gates': [{'name': 'status_ok', 'expr': "interface['status'] == 'ok'"}],
 'allow_subprocess': True}


def main() -> int:
    root = Path.cwd()
    _ = json.loads((root / "artifacts/fit_model/interface.json").read_text(encoding="utf-8"))
    subprocess.run(["python3", "-c", "print('subprocess_probe_ok')"], check=True)
    out = root / "artifacts/subprocess_probe"
    out.mkdir(parents=True, exist_ok=True)
    (out / "interface.json").write_text(json.dumps({"status": "ok", "task": "subprocess_probe"}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
