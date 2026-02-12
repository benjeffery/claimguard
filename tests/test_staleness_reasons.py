from __future__ import annotations

import json
from pathlib import Path

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_blocked_upstream_marks_skip_reason(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/in.txt", "in\n")
    _write(ws / "inputs/secret.txt", "secret\n")

    contract = {
        "pipeline_name": "stale_reason",
        "task_roots": ["tasks"],
    }
    _write(ws / "claimguard.json", json.dumps(contract, indent=2) + "\n")

    first_spec = {
        "inputs": ["inputs/in.txt"],
        "outputs": ["artifacts/first/out.txt", "artifacts/first/interface.json"],
        "interface_output": "artifacts/first/interface.json",
        "gates": [],
    }
    _write(
        ws / "tasks/first.py",
        "\n".join(
            [
                f"CG_TASK = {repr(first_spec)}",
                "from pathlib import Path",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    (root / 'inputs/secret.txt').read_text(encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        ),
    )

    second_spec = {
        "inputs": ["artifacts/first/interface.json"],
        "outputs": ["artifacts/second/interface.json"],
        "interface_output": "artifacts/second/interface.json",
        "gates": [],
    }
    _write(
        ws / "tasks/second.py",
        "\n".join(
            [
                f"CG_TASK = {repr(second_spec)}",
                "from pathlib import Path",
                "import json",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    (root / 'artifacts/second').mkdir(parents=True, exist_ok=True)",
                "    (root / 'artifacts/second/interface.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        ),
    )

    report = PipelineRunner(ws / "claimguard.json").run()
    first = next(r for r in report["task_rows"] if r["task"] == "first")
    second = next(r for r in report["task_rows"] if r["task"] == "second")

    assert first["status"] == "blocked"
    assert str(first["blocked_reason"]).startswith("nonzero_exit")
    assert second["status"] == "blocked"
    assert second["blocked_reason"] == "blocked_upstream"
    assert second["cache_reason"] == "skipped_blocked_upstream"
