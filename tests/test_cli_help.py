from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_python_module_help_flag() -> None:
    project_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(project_root)}
    proc = subprocess.run(
        [sys.executable, "-m", "claimguard", "--help"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "usage: claimguard" in proc.stdout
    assert "{run,report,doctor}" in proc.stdout

