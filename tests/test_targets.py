from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from claimguard.runner import PipelineRunner


def test_targeted_run_executes_dependency_closure_only(tmp_path: Path) -> None:
    src = Path("examples/all-features-example").resolve()
    dst = (tmp_path / "all-features-example").resolve()
    shutil.copytree(src, dst)

    runner = PipelineRunner(dst / "claimguard.json")
    report = runner.run(max_workers=4, targets=["publish_rank"])

    rows = {r["task"]: r for r in report["task_rows"]}
    expected = {"prepare_data", "bootstrap_model", "feature_stats", "fit_model", "publish_rank"}
    assert set(rows) == expected
    assert report["claim"]["target_tasks"] == ["publish_rank"]
    assert report["claim"]["claim_class"] == "contract-certified"


def test_unknown_target_fails_fast(tmp_path: Path) -> None:
    src = Path("examples/minimal-example").resolve()
    dst = (tmp_path / "minimal-example").resolve()
    shutil.copytree(src, dst)

    runner = PipelineRunner(dst / "claimguard.json")
    with pytest.raises(RuntimeError, match="unknown target task"):
        runner.run(targets=["does_not_exist"])
