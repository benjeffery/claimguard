from __future__ import annotations

import json
from pathlib import Path

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_task(workspace: Path, *, allow_subprocess: bool) -> None:
    cg_task = {
        "inputs": {"in": "inputs/in.txt"},
        "outputs": {"out": "artifacts/out.txt", "interface": "artifacts/interface.json"},
        "interface_output": "interface",
        "gates": [],
        "allow_subprocess": allow_subprocess,
    }
    lines = [
        f"CG_TASK = {repr(cg_task)}",
        "from pathlib import Path",
        "import json",
        "import subprocess",
        "",
        "def main() -> int:",
        "    root = Path.cwd()",
        "    (root / 'inputs/in.txt').read_text(encoding='utf-8')",
        "    subprocess.run(['python3', '-c', 'print(123)'], check=True)",
        "    (root / 'artifacts').mkdir(parents=True, exist_ok=True)",
        "    (root / 'artifacts/out.txt').write_text('ok\\n', encoding='utf-8')",
        "    (root / 'artifacts/interface.json').write_text(json.dumps({'status':'ok'}), encoding='utf-8')",
        "    return 0",
        "",
        "if __name__ == '__main__':",
        "    raise SystemExit(main())",
        "",
    ]
    _write(workspace / "tasks/task.py", "\n".join(lines))


def _contract(workspace: Path) -> Path:
    contract = {
        "pipeline_name": "subprocess_policy",
        "task_roots": ["tasks"],
    }
    path = workspace / "claimguard.json"
    _write(path, json.dumps(contract, indent=2) + "\n")
    return path


def test_subprocess_blocked_by_default(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/in.txt", "in\n")
    _write_task(ws, allow_subprocess=False)
    report = PipelineRunner(_contract(ws)).run()
    row = next(r for r in report["task_rows"] if r["task"] == "task")
    assert row["status"] == "blocked"
    assert str(row["blocked_reason"]).startswith("nonzero_exit")


def test_subprocess_allowed_when_opted_in(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/in.txt", "in\n")
    _write_task(ws, allow_subprocess=True)
    report = PipelineRunner(_contract(ws)).run()
    row = next(r for r in report["task_rows"] if r["task"] == "task")
    assert row["status"] == "ok"
    assert row["blocked_reason"] == ""
