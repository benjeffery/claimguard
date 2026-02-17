from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_terminate_process_kills_process_group(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "cancel_test", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(
        ws / "tasks/a.py",
        "\n".join(
            [
                "CG_TASK = {",
                "    'inputs': {},",
                "    'outputs': {'interface': 'artifacts/a/interface.json'},",
                "    'interface_output': 'interface',",
                "}",
            ]
        )
        + "\n",
    )
    runner = PipelineRunner(ws / "claimguard.json")
    pid_file = ws / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "with open(sys.argv[1], 'w', encoding='utf-8') as f:\n"
        "    f.write(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(pid_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    child_pid = 0
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if pid_file.exists():
                raw = pid_file.read_text(encoding="utf-8").strip()
                if raw:
                    child_pid = int(raw)
                    break
            time.sleep(0.05)
        assert child_pid > 0

        runner._terminate_process(proc, grace_s=0.1)
        proc.wait(timeout=2.0)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("child process survived group termination")
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        if child_pid > 0:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            except Exception:
                pass
