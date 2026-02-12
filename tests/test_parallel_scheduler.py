from __future__ import annotations

import json
import time
from pathlib import Path

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_parallel_scheduler_overlaps_independent_tasks(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/in.txt", "in\n")
    _write(
        ws / "claimguard.json",
        json.dumps(
            {
                "pipeline_name": "parallel_overlap",
                "task_roots": ["tasks"],
            },
            indent=2,
        )
        + "\n",
    )

    def task_script(task_name: str, output_dir: str, sleep_s: float, extra_inputs: list[str] | None = None) -> str:
        ins = ["inputs/in.txt"] + (extra_inputs or [])
        outs = [f"artifacts/{output_dir}/interface.json"]
        spec = {
            "inputs": ins,
            "outputs": outs,
            "interface_output": outs[0],
            "gates": [],
            "claim_blocking": task_name == "merge",
        }
        lines = [
            f"CG_TASK = {repr(spec)}",
            "from pathlib import Path",
            "import json",
            "import time",
            "def main() -> int:",
            "    root = Path.cwd()",
            "    (root / 'inputs/in.txt').read_text(encoding='utf-8')",
        ]
        if extra_inputs:
            for rel in extra_inputs:
                lines.append(f"    (root / {rel!r}).read_text(encoding='utf-8')")
        lines.extend(
            [
                f"    time.sleep({sleep_s})",
                f"    out = root / 'artifacts/{output_dir}'",
                "    out.mkdir(parents=True, exist_ok=True)",
                f"    (out / 'interface.json').write_text(json.dumps({{'status':'ok','task':{task_name!r}}}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
        return "\n".join(lines) + "\n"

    _write(ws / "tasks/a.py", task_script("a", "a", 0.35))
    _write(ws / "tasks/b.py", task_script("b", "b", 0.35))
    _write(
        ws / "tasks/merge.py",
        task_script(
            "merge",
            "merge",
            0.01,
            extra_inputs=["artifacts/a/interface.json", "artifacts/b/interface.json"],
        ),
    )

    runner = PipelineRunner(ws / "claimguard.json")
    t0 = time.perf_counter()
    report = runner.run(max_workers=2)
    elapsed = time.perf_counter() - t0
    # Sequential would be ~0.35 + 0.35 + 0.01 plus overhead; parallel should be significantly lower.
    assert elapsed < 0.60
    assert report["summary"]["max_workers"] == 2
    statuses = {r["task"]: r["status"] for r in report["task_rows"]}
    assert statuses["a"] == "ok"
    assert statuses["b"] == "ok"
    assert statuses["merge"] == "ok"
