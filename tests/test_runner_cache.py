from __future__ import annotations

import json
import re
from pathlib import Path

from claimguard.runner import PipelineRunner, _make_run_id


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    contract = {
        "pipeline_name": "test",
        "task_roots": ["tasks"],
    }
    _write(workspace / "claimguard.json", json.dumps(contract, indent=2) + "\n")
    _write(workspace / "inputs/in.txt", "x\n")
    _write(
        workspace / "tasks/task.py",
        "\n".join(
            [
                "CG_TASK = {",
                '  "inputs": {"in": "inputs/in.txt"},',
                '  "outputs": {"out": "artifacts/out.txt", "interface": "artifacts/interface.json"},',
                '  "interface_output": "interface",',
                '  "gates": [],',
                "}",
                "import helper",
                "from pathlib import Path",
                "import json",
                "",
                "if __name__ == '__main__':",
                "    out = Path('artifacts')",
                "    out.mkdir(parents=True, exist_ok=True)",
                "    (out / 'out.txt').write_text(str(helper.VALUE) + '\\n', encoding='utf-8')",
                "    (out / 'interface.json').write_text(json.dumps({'status': 'ok', 'value': helper.VALUE}), encoding='utf-8')",
                "",
            ]
        ),
    )
    _write(workspace / "tasks/helper.py", "VALUE = 1\n")
    return workspace


def test_make_run_id_has_random_suffix() -> None:
    a = _make_run_id()
    b = _make_run_id()
    assert a != b
    assert re.match(r"^run_\d{8}T\d{6}Z_[0-9a-f]{8}$", a)
    assert re.match(r"^run_\d{8}T\d{6}Z_[0-9a-f]{8}$", b)


def test_cache_key_changes_when_direct_helper_changes(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    runner = PipelineRunner(workspace / "claimguard.json")
    spec = runner.task_specs["task"]
    input_hashes = runner._compute_input_hashes(spec)

    key_before = runner._cache_key(spec, input_hashes)
    _write(workspace / "tasks/helper.py", "VALUE = 2\n")
    key_after = runner._cache_key(spec, input_hashes)
    assert key_before != key_after


def test_cache_key_changes_when_transitive_helper_changes(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace / "tasks/helper.py", "import helper2\nVALUE = helper2.VALUE\n")
    _write(workspace / "tasks/helper2.py", "VALUE = 10\n")

    runner = PipelineRunner(workspace / "claimguard.json")
    spec = runner.task_specs["task"]
    input_hashes = runner._compute_input_hashes(spec)

    key_before = runner._cache_key(spec, input_hashes)
    _write(workspace / "tasks/helper2.py", "VALUE = 11\n")
    key_after = runner._cache_key(spec, input_hashes)
    assert key_before != key_after


def test_runner_reexecutes_task_after_helper_change(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    runner = PipelineRunner(workspace / "claimguard.json")
    spec = runner.task_specs["task"]

    first = runner._run_task(spec, run_id="run_a")
    assert first.status == "ok"
    assert first.cache_hit is False
    assert first.cache_reason == "no_cache_record"

    second = runner._run_task(spec, run_id="run_b")
    assert second.status == "replay_ok"
    assert second.cache_hit is True
    assert second.cache_reason == "cache_hit"

    _write(workspace / "tasks/helper.py", "VALUE = 9\n")

    third = runner._run_task(spec, run_id="run_c")
    assert third.status == "ok"
    assert third.cache_hit is False
    assert third.cache_reason == "cache_key_changed"


def test_cache_key_changes_when_dependency_file_changes(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace / "pyproject.toml", "[project]\nname = 'x'\nversion = '0.0.1'\n")
    runner = PipelineRunner(workspace / "claimguard.json")
    spec = runner.task_specs["task"]
    input_hashes = runner._compute_input_hashes(spec)

    key_before = runner._cache_key(spec, input_hashes)
    _write(workspace / "pyproject.toml", "[project]\nname = 'x'\nversion = '0.0.2'\n")
    key_after = runner._cache_key(spec, input_hashes)
    assert key_before != key_after
