from __future__ import annotations

import json
from pathlib import Path

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_task_script(workspace: Path, cg_task: dict, body_lines: list[str]) -> None:
    lines = [
        f"CG_TASK = {repr(cg_task)}",
        "from pathlib import Path",
        "import json",
        "",
        "def main() -> int:",
    ]
    lines.extend([f"    {line}" for line in body_lines])
    lines.extend(
        [
            "    return 0",
            "",
            "if __name__ == '__main__':",
            "    raise SystemExit(main())",
            "",
        ]
    )
    _write(workspace / "tasks/task.py", "\n".join(lines))


def _single_task_contract(workspace: Path) -> Path:
    contract = {
        "pipeline_name": "io_enforcement",
        "task_roots": ["tasks"],
    }
    path = workspace / "claimguard.json"
    _write(path, json.dumps(contract, indent=2) + "\n")
    return path


def test_io_enforcement_blocks_undeclared_read(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/in.txt", "in\n")
    _write(ws / "inputs/secret.txt", "secret\n")
    contract_path = _single_task_contract(ws)

    cg_task = {
        "inputs": ["inputs/in.txt"],
        "outputs": ["artifacts/out.txt", "artifacts/interface.json"],
        "interface_output": "artifacts/interface.json",
        "gates": [],
    }
    _write_task_script(
        ws,
        cg_task,
        [
            "root = Path.cwd()",
            "(root / 'inputs/in.txt').read_text(encoding='utf-8')",
            "(root / 'inputs/secret.txt').read_text(encoding='utf-8')",
            "(root / 'artifacts').mkdir(parents=True, exist_ok=True)",
            "(root / 'artifacts/out.txt').write_text('ok\\n', encoding='utf-8')",
            "(root / 'artifacts/interface.json').write_text(json.dumps({'status':'ok'}), encoding='utf-8')",
        ],
    )

    report = PipelineRunner(contract_path).run()
    row = next(r for r in report["task_rows"] if r["task"] == "task")
    assert row["status"] == "blocked"
    assert str(row["blocked_reason"]).startswith("nonzero_exit")
    assert report["claim"]["claim_class"] == "blocked"


def test_io_enforcement_blocks_undeclared_write(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/in.txt", "in\n")
    contract_path = _single_task_contract(ws)

    cg_task = {
        "inputs": ["inputs/in.txt"],
        "outputs": ["artifacts/out.txt", "artifacts/interface.json"],
        "interface_output": "artifacts/interface.json",
        "gates": [],
    }
    _write_task_script(
        ws,
        cg_task,
        [
            "root = Path.cwd()",
            "(root / 'inputs/in.txt').read_text(encoding='utf-8')",
            "(root / 'artifacts').mkdir(parents=True, exist_ok=True)",
            "(root / 'artifacts/rogue.txt').write_text('rogue\\n', encoding='utf-8')",
            "(root / 'artifacts/out.txt').write_text('ok\\n', encoding='utf-8')",
            "(root / 'artifacts/interface.json').write_text(json.dumps({'status':'ok'}), encoding='utf-8')",
        ],
    )

    report = PipelineRunner(contract_path).run()
    row = next(r for r in report["task_rows"] if r["task"] == "task")
    assert row["status"] == "blocked"
    assert str(row["blocked_reason"]).startswith("nonzero_exit")
    assert report["claim"]["claim_class"] == "blocked"


def test_io_enforcement_allows_declared_read_exemption(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/in.txt", "in\n")
    _write(ws / "inputs/secret.txt", "secret\n")
    contract_path = _single_task_contract(ws)

    cg_task = {
        "inputs": ["inputs/in.txt"],
        "read_exemptions": ["inputs/secret.txt"],
        "outputs": ["artifacts/out.txt", "artifacts/interface.json"],
        "interface_output": "artifacts/interface.json",
        "gates": [],
    }
    _write_task_script(
        ws,
        cg_task,
        [
            "root = Path.cwd()",
            "(root / 'inputs/in.txt').read_text(encoding='utf-8')",
            "(root / 'inputs/secret.txt').read_text(encoding='utf-8')",
            "(root / 'artifacts').mkdir(parents=True, exist_ok=True)",
            "(root / 'artifacts/out.txt').write_text('ok\\n', encoding='utf-8')",
            "(root / 'artifacts/interface.json').write_text(json.dumps({'status':'ok'}), encoding='utf-8')",
        ],
    )

    report = PipelineRunner(contract_path).run()
    row = next(r for r in report["task_rows"] if r["task"] == "task")
    assert row["status"] == "ok"
    assert row["blocked_reason"] == ""
