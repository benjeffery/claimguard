from __future__ import annotations

import json
from pathlib import Path

import pytest

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_task(
    *,
    name: str,
    output_rel: str,
    inputs: list[str] | None = None,
    resources: dict[str, object] | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    spec: dict[str, object] = {
        "inputs": list(inputs or []),
        "outputs": [output_rel],
        "interface_output": output_rel,
        "gates": [],
    }
    if resources is not None:
        spec["resources"] = dict(resources)
    lines = [
        f"CG_TASK = {repr(spec)}",
        "from pathlib import Path",
        "import json",
        "import os",
        "def main() -> int:",
        "    root = Path.cwd()",
    ]
    for rel in inputs or []:
        lines.append(f"    (root / {rel!r}).read_text(encoding='utf-8')")
    lines.extend(
        [
            f"    out = root / {output_rel.rsplit('/', 1)[0]!r}",
            "    out.mkdir(parents=True, exist_ok=True)",
        ]
    )
    if extra_lines:
        lines.extend(extra_lines)
    else:
        lines.extend(
            [
                "    payload = {",
                "        'status': 'ok',",
                "        'task': os.environ.get('CG_TASK_NAME', ''),",
                "        'cpu_threads': int(os.environ.get('CG_CPU_THREADS', '0')),",
                "        'omp': os.environ.get('OMP_NUM_THREADS', ''),",
                "        'openblas': os.environ.get('OPENBLAS_NUM_THREADS', ''),",
                "        'mkl': os.environ.get('MKL_NUM_THREADS', ''),",
                "        'numexpr': os.environ.get('NUMEXPR_NUM_THREADS', ''),",
                "        'goto': os.environ.get('GOTO_NUM_THREADS', ''),",
                "        'affinity_env': os.environ.get('CG_CPU_AFFINITY', ''),",
                "    }",
                f"    (out / {output_rel.rsplit('/', 1)[1]!r}).write_text(json.dumps(payload), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
    return "\n".join(lines) + "\n"


def test_default_resources_are_single_thread(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "resources_default", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(ws / "tasks/a.py", _make_task(name="a", output_rel="artifacts/a/interface.json"))
    runner = PipelineRunner(ws / "claimguard.json")
    spec = runner.task_specs["a"]
    assert spec.resources["cpu_threads_min"] == 1
    assert spec.resources["cpu_threads_pref"] == 1
    assert spec.resources["cpu_threads_max"] == 1


def test_resources_invalid_cpu_pref_is_rejected(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "resources_invalid", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(
        ws / "tasks/a.py",
        _make_task(
            name="a",
            output_rel="artifacts/a/interface.json",
            resources={"cpu_threads_min": 2, "cpu_threads_pref": 1},
        ),
    )
    with pytest.raises(RuntimeError, match=r"cpu_threads_pref"):
        PipelineRunner(ws / "claimguard.json")


def test_resources_bool_thread_values_are_rejected(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "resources_bool_invalid", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(
        ws / "tasks/a.py",
        _make_task(
            name="a",
            output_rel="artifacts/a/interface.json",
            resources={"cpu_threads_min": True},
        ),
    )
    with pytest.raises(RuntimeError, match=r"cpu_threads_min"):
        PipelineRunner(ws / "claimguard.json")


def test_scheduler_exports_thread_budget_env_vars(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "resources_env", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    shared_resources = {"cpu_threads_min": 1, "cpu_threads_pref": 2, "cpu_threads_max": 2}
    _write(
        ws / "tasks/a.py",
        _make_task(name="a", output_rel="artifacts/a/interface.json", resources=shared_resources),
    )
    _write(
        ws / "tasks/b.py",
        _make_task(name="b", output_rel="artifacts/b/interface.json", resources=shared_resources),
    )

    runner = PipelineRunner(ws / "claimguard.json")
    report = runner.run(max_workers=2)
    rows = {str(row["task"]): row for row in report["task_rows"]}
    assert int(rows["a"]["cpu_threads_alloc"]) == 1
    assert int(rows["b"]["cpu_threads_alloc"]) == 1

    for task in ("a", "b"):
        payload = json.loads((ws / f"artifacts/{task}/interface.json").read_text(encoding="utf-8"))
        assert int(payload["cpu_threads"]) == 1
        assert payload["omp"] == "1"
        assert payload["openblas"] == "1"
        assert payload["mkl"] == "1"
        assert payload["numexpr"] == "1"
        assert payload["goto"] == "1"
        assert len([x for x in str(payload["affinity_env"]).split(",") if x.strip()]) == 1


def test_scheduler_uses_critical_path_priority(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "resources_critical_path", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(ws / "tasks/a.py", _make_task(name="a", output_rel="artifacts/a/interface.json"))
    _write(ws / "tasks/b.py", _make_task(name="b", output_rel="artifacts/b/interface.json"))
    _write(
        ws / "tasks/c.py",
        _make_task(
            name="c",
            output_rel="artifacts/c/interface.json",
            inputs=["artifacts/a/interface.json"],
        ),
    )
    _write(
        ws / "tasks/d.py",
        _make_task(
            name="d",
            output_rel="artifacts/d/interface.json",
            inputs=["artifacts/c/interface.json"],
        ),
    )
    _write(
        ws / "tasks/f.py",
        _make_task(
            name="f",
            output_rel="artifacts/f/interface.json",
            inputs=["artifacts/d/interface.json"],
        ),
    )
    _write(
        ws / "tasks/g.py",
        _make_task(
            name="g",
            output_rel="artifacts/g/interface.json",
            inputs=["artifacts/f/interface.json"],
        ),
    )

    runner = PipelineRunner(ws / "claimguard.json")
    events: list[dict[str, object]] = []
    runner.run(max_workers=1, event_emitter=events.append)
    starts = [str(e.get("task", "")) for e in events if str(e.get("event", "")) == "task_start"]
    assert starts[:4] == ["a", "c", "d", "f"]


def test_scheduler_rejects_task_min_threads_above_budget(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps({"pipeline_name": "resources_over_budget", "task_roots": ["tasks"]}, indent=2) + "\n",
    )
    _write(
        ws / "tasks/a.py",
        _make_task(
            name="a",
            output_rel="artifacts/a/interface.json",
            resources={"cpu_threads_min": 3, "cpu_threads_pref": 3, "cpu_threads_max": 3},
        ),
    )
    runner = PipelineRunner(ws / "claimguard.json")
    with pytest.raises(RuntimeError, match=r"require cpu_threads_min above global budget"):
        runner.run(max_workers=2)
