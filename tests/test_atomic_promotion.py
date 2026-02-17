from __future__ import annotations

import json
from pathlib import Path

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_gate_failure_does_not_promote_staged_outputs(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/mode.txt", "ok\n")

    contract = {
        "pipeline_name": "atomic_promotion",
        "task_roots": ["tasks"],
    }
    _write(ws / "claimguard.json", json.dumps(contract, indent=2) + "\n")

    cg_task = {'inputs': {'mode': 'inputs/mode.txt'},
 'outputs': {'out': 'artifacts/out.txt', 'interface': 'artifacts/interface.json'},
 'interface_output': 'interface',
 'gates': [{'name': 'status_ok', 'expr': "interface['status'] == 'ok'"}]}
    _write(
        ws / "tasks/task.py",
        "\n".join(
            [
                f"CG_TASK = {repr(cg_task)}",
                "from pathlib import Path",
                "import json",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    mode = (root / 'inputs/mode.txt').read_text(encoding='utf-8').strip()",
                "    status = 'ok' if mode == 'ok' else 'not_ok'",
                "    (root / 'artifacts').mkdir(parents=True, exist_ok=True)",
                "    (root / 'artifacts/out.txt').write_text(f'{mode}\\n', encoding='utf-8')",
                "    (root / 'artifacts/interface.json').write_text(json.dumps({'status': status}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        ),
    )

    runner = PipelineRunner(ws / "claimguard.json")
    report_ok = runner.run()
    row_ok = next(r for r in report_ok["task_rows"] if r["task"] == "task")
    assert row_ok["status"] == "ok"
    assert (ws / "artifacts/out.txt").read_text(encoding="utf-8").strip() == "ok"

    _write(ws / "inputs/mode.txt", "bad\n")
    report_bad = runner.run()
    row_bad = next(r for r in report_bad["task_rows"] if r["task"] == "task")
    assert row_bad["status"] == "blocked"
    assert row_bad["blocked_reason"] == "gate_failure"
    # Output must remain last promoted value, not failed staged value.
    assert (ws / "artifacts/out.txt").read_text(encoding="utf-8").strip() == "ok"
