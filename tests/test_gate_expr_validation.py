from __future__ import annotations

import json
from pathlib import Path

import pytest

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_task(workspace: Path, cg_task: dict, body_lines: list[str]) -> None:
    cg_task = dict(cg_task)
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


def test_contract_requires_explicit_task_roots(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "claimguard.json", json.dumps({"pipeline_name": "missing_roots"}, indent=2) + "\n")

    with pytest.raises(RuntimeError, match="contract must define non-empty string list `task_roots`"):
        PipelineRunner(ws / "claimguard.json")


def test_no_tasks_discovered_is_rejected(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "tasks").mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "no_tasks", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    with pytest.raises(RuntimeError, match="no tasks discovered"):
        PipelineRunner(ws / "claimguard.json")


def test_old_gate_dsl_is_rejected(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "old_gate", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(ws / "inputs/in.txt", "x\n")

    _write_task(
        ws,
        {'inputs': {'in': 'inputs/in.txt'},
 'outputs': {'interface': 'artifacts/interface.json'},
 'interface_output': 'interface',
 'gates': [{'name': 'status_ok', 'path': 'status', 'equals': 'ok'}]},
        [
            "root = Path.cwd()",
            "(root / 'artifacts').mkdir(parents=True, exist_ok=True)",
            "(root / 'artifacts/interface.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')",
        ],
    )

    with pytest.raises(RuntimeError, match="must have exactly keys \\['expr', 'name'\\]"):
        PipelineRunner(ws / "claimguard.json")


def test_gate_expr_must_return_bool(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "non_bool_expr", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(ws / "inputs/in.txt", "x\n")

    _write_task(
        ws,
        {'inputs': {'in': 'inputs/in.txt'},
 'outputs': {'interface': 'artifacts/interface.json'},
 'interface_output': 'interface',
 'gates': [{'name': 'status_truthy', 'expr': "interface['status']"}]},
        [
            "root = Path.cwd()",
            "(root / 'artifacts').mkdir(parents=True, exist_ok=True)",
            "(root / 'artifacts/interface.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')",
        ],
    )

    report = PipelineRunner(ws / "claimguard.json").run()
    row = next(r for r in report["task_rows"] if r["task"] == "task")
    assert row["status"] == "blocked"
    assert row["blocked_reason"] == "gate_failure"
    assert row["gate_rows"][0]["reason"] == "non_bool_result"


def test_contract_unknown_key_is_rejected(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps(
            {
                "pipeline_name": "unknown_key",
                "task_roots": ["tasks"],
                "claim_target": {"task": "task"},
            },
            indent=2,
        )
        + "\n",
    )
    with pytest.raises(RuntimeError, match="unsupported contract key\\(s\\)"):
        PipelineRunner(ws / "claimguard.json")


def test_contract_task_roots_disallow_parent_traversal(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "bad_roots", "task_roots": ["../tasks"]}, indent=2) + "\n",
    )
    with pytest.raises(RuntimeError, match="task_roots.*cannot contain parent traversal"):
        PipelineRunner(ws / "claimguard.json")


def test_task_paths_disallow_parent_traversal_and_absolute(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "bad_paths", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(ws / "inputs/in.txt", "x\n")

    _write_task(
        ws,
        {'inputs': {'in': 'inputs/in.txt'},
 'outputs': {'interface': '../artifacts/interface.json'},
 'interface_output': 'interface',
 'gates': []},
        [
            "root = Path.cwd()",
            "(root / 'artifacts').mkdir(parents=True, exist_ok=True)",
            "(root / 'artifacts/interface.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')",
        ],
    )
    with pytest.raises(RuntimeError, match="cannot contain parent traversal"):
        PipelineRunner(ws / "claimguard.json")

    _write_task(
        ws,
        {'inputs': {'in': 'inputs/in.txt'},
 'outputs': {'interface': '/tmp/claimguard_bad/interface.json'},
 'interface_output': 'interface',
 'gates': []},
        [
            "root = Path.cwd()",
            "(root / 'artifacts').mkdir(parents=True, exist_ok=True)",
            "(root / 'artifacts/interface.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')",
        ],
    )
    with pytest.raises(RuntimeError, match="must use relative paths only"):
        PipelineRunner(ws / "claimguard.json")


def test_contract_task_params_must_be_object_map(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)

    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "bad_params", "task_roots": ["tasks"], "task_params": []}, indent=2) + "\n",
    )
    with pytest.raises(RuntimeError, match="`task_params` must be an object"):
        PipelineRunner(ws / "claimguard.json")


def test_disabled_flag_defaults_to_off(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "disabled_default_off", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(ws / "inputs/in.txt", "x\n")
    _write_task(
        ws,
        {
            "inputs": {"in": "inputs/in.txt"},
            "outputs": {"interface": "artifacts/interface.json"},
            "interface_output": "interface",
            "gates": [],
        },
        [
            "root = Path.cwd()",
            "(root / 'artifacts').mkdir(parents=True, exist_ok=True)",
            "(root / 'artifacts/interface.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')",
        ],
    )

    report = PipelineRunner(ws / "claimguard.json").run()
    row = next(r for r in report["task_rows"] if r["task"] == "task")
    assert row["status"] == "ok"


def test_disabled_true_is_skipped_from_discovery_and_run(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "disabled_skip", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(ws / "inputs/in.txt", "x\n")

    _write(
        ws / "tasks/a_disabled.py",
        "\n".join(
            [
                "CG_TASK = {",
                "  'inputs': {'in': 'inputs/in.txt'},",
                "  'outputs': {'interface': 'artifacts/a/interface.json'},",
                "  'interface_output': 'interface',",
                "  'gates': [],",
                "  'disabled': True,",
                "}",
                "from pathlib import Path",
                "import json",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    (root / 'artifacts/a').mkdir(parents=True, exist_ok=True)",
                "    (root / 'artifacts/a/interface.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
        + "\n",
    )
    _write(
        ws / "tasks/b_enabled.py",
        "\n".join(
            [
                "CG_TASK = {",
                "  'inputs': {'in': 'inputs/in.txt'},",
                "  'outputs': {'interface': 'artifacts/b/interface.json'},",
                "  'interface_output': 'interface',",
                "  'gates': [],",
                "}",
                "from pathlib import Path",
                "import json",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    (root / 'artifacts/b').mkdir(parents=True, exist_ok=True)",
                "    (root / 'artifacts/b/interface.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
        + "\n",
    )

    runner = PipelineRunner(ws / "claimguard.json")
    assert sorted(runner.task_specs.keys()) == ["b_enabled"]
    report = runner.run()
    assert sorted(str(row["task"]) for row in report["task_rows"]) == ["b_enabled"]
