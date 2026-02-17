#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

CG_TASK = {'inputs': {'in_interface': 'artifacts/prepare_data/interface.json'},
 'outputs': {'interface': 'artifacts/blocked_write_probe/interface.json'},
 'interface_output': 'interface',
 'claim_blocking': False,
 'gates': [{'name': 'status_ok', 'expr': "interface['status'] == 'ok'"}]}


def main() -> int:
    root = Path.cwd()
    _ = json.loads((root / "artifacts/prepare_data/interface.json").read_text(encoding="utf-8"))
    # Intentional undeclared write: this task should be blocked by policy.
    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "rogue_write.txt").write_text("UNDECLARED_WRITE\n", encoding="utf-8")

    out = root / "artifacts/blocked_write_probe"
    out.mkdir(parents=True, exist_ok=True)
    (out / "interface.json").write_text(json.dumps({"status": "ok"}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
