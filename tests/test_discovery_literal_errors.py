from __future__ import annotations

import json
from pathlib import Path

import pytest

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_non_literal_cg_task_gets_helpful_error(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "literal_error", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(ws / "inputs/in.txt", "x\n")
    _write(
        ws / "tasks/task.py",
        "\n".join(
            [
                "INPUTS = {'a': 'inputs/in.txt'}",
                "CG_TASK = {",
                "  'inputs': list(INPUTS.values()),",
                "  'outputs': ['artifacts/interface.json'],",
                "  'interface_output': 'artifacts/interface.json',",
                "  'gates': [],",
                "}",
            ]
        ),
    )

    with pytest.raises(RuntimeError, match=r"must be a top-level literal dict"):
        PipelineRunner(ws / "claimguard.json")
