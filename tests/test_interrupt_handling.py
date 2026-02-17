from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_ctrl_c_stops_run_quickly(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    contract_path = ws / "claimguard.json"
    _write(
        contract_path,
        json.dumps(
            {
                "pipeline_name": "interrupt_demo",
                "task_roots": ["tasks"],
            },
            indent=2,
        )
        + "\n",
    )
    _write(
        ws / "tasks/sleepy.py",
        "\n".join(
            [
                "CG_TASK = {",
                "    'inputs': {},",
                "    'outputs': {'interface': 'artifacts/sleepy/interface.json'},",
                "    'interface_output': 'interface',",
                "}",
                "from pathlib import Path",
                "import json",
                "import time",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    time.sleep(20)",
                "    out = root / 'artifacts/sleepy'",
                "    out.mkdir(parents=True, exist_ok=True)",
                "    (out / 'interface.json').write_text(json.dumps({'status':'ok'}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
        + "\n",
    )

    project_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(project_root)}
    cmd = [
        sys.executable,
        "-m",
        "claimguard.cli",
        "run",
        "--contract",
        str(contract_path),
        "--jobs",
        "1",
    ]

    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(1.0)
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    elapsed = time.monotonic() - started
    assert proc.returncode == 130
    assert elapsed < 10
    assert not (ws / "artifacts/sleepy/interface.json").exists()
