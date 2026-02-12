from __future__ import annotations

import json
import shutil
from pathlib import Path

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _contract(workspace: Path, *, policy: str, seed: int) -> Path:
    c = {
        "pipeline_name": "rng_policy",
        "task_roots": ["tasks"],
        "rng_policy": policy,
        "rng_seed": seed,
    }
    p = workspace / "claimguard.json"
    _write(p, json.dumps(c, indent=2) + "\n")
    return p


def test_rng_strict_blocks_unapproved_usage(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/in.txt", "in\n")
    contract = _contract(ws, policy="strict", seed=123)
    spec = {
        "inputs": ["inputs/in.txt"],
        "outputs": ["artifacts/interface.json"],
        "interface_output": "artifacts/interface.json",
        "gates": [],
        # allow_rng omitted -> False
    }
    _write(
        ws / "tasks/task.py",
        "\n".join(
            [
                f"CG_TASK = {repr(spec)}",
                "from pathlib import Path",
                "import json",
                "import random",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    (root / 'inputs/in.txt').read_text(encoding='utf-8')",
                "    x = random.random()",
                "    (root / 'artifacts').mkdir(parents=True, exist_ok=True)",
                "    (root / 'artifacts/interface.json').write_text(json.dumps({'status': 'ok', 'x': x}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
        + "\n",
    )
    report = PipelineRunner(contract).run()
    row = next(r for r in report["task_rows"] if r["task"] == "task")
    assert row["status"] == "blocked"
    assert str(row["blocked_reason"]).startswith("nonzero_exit")


def test_rng_strict_allow_rng_is_deterministic(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(ws / "inputs/in.txt", "in\n")
    contract = _contract(ws, policy="strict", seed=123)
    spec = {
        "inputs": ["inputs/in.txt"],
        "outputs": ["artifacts/interface.json"],
        "interface_output": "artifacts/interface.json",
        "gates": [],
        "allow_rng": True,
    }
    _write(
        ws / "tasks/task.py",
        "\n".join(
            [
                f"CG_TASK = {repr(spec)}",
                "from pathlib import Path",
                "import json",
                "import random",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    (root / 'inputs/in.txt').read_text(encoding='utf-8')",
                "    x = random.random()",
                "    (root / 'artifacts').mkdir(parents=True, exist_ok=True)",
                "    (root / 'artifacts/interface.json').write_text(json.dumps({'status': 'ok', 'x': x}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
        + "\n",
    )

    r1 = PipelineRunner(contract)
    rep1 = r1.run()
    row1 = next(r for r in rep1["task_rows"] if r["task"] == "task")
    assert row1["status"] == "ok"
    x1 = json.loads((ws / "artifacts/interface.json").read_text(encoding="utf-8"))["x"]

    shutil.rmtree(ws / ".claimguard", ignore_errors=True)
    r2 = PipelineRunner(contract)
    rep2 = r2.run()
    row2 = next(r for r in rep2["task_rows"] if r["task"] == "task")
    assert row2["status"] == "ok"
    x2 = json.loads((ws / "artifacts/interface.json").read_text(encoding="utf-8"))["x"]
    assert x1 == x2
