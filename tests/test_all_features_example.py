from __future__ import annotations

import json
import shutil
from pathlib import Path

from claimguard.runner import PipelineRunner


def test_all_features_example_smoke(tmp_path: Path) -> None:
    src = Path("examples/all-features-example").resolve()
    dst = (tmp_path / "all-features-example").resolve()
    shutil.copytree(src, dst)

    runner = PipelineRunner(dst / "claimguard.json")
    report = runner.run(max_workers=4)

    assert report["claim"]["claim_class"] == "contract-certified"
    rows = {r["task"]: r for r in report["task_rows"]}

    assert rows["blocked_probe"]["status"] == "blocked"
    assert rows["blocked_write_probe"]["status"] == "blocked"
    assert rows["rng_probe_blocked"]["status"] == "blocked"
    assert rows["subprocess_probe"]["status"] in {"ok", "replay_ok"}
    assert rows["subprocess_probe_blocked"]["status"] == "blocked"
    assert rows["diagnostic_probe"]["status"] in {"diagnostic_only", "replay_ok"}
    assert rows["solve_defect"]["status"] in {"ok", "replay_ok"}
    assert rows["reduce_defects"]["status"] in {"ok", "replay_ok"}
    assert rows["solve_defect"]["cache_reason"] in {"map_executed", "map_all_cache_hit"}
    # 4 shards x 2 declared outputs per shard
    assert len(rows["solve_defect"]["output_hashes"]) == 8

    reduce_iface = json.loads((dst / "artifacts/reduce_defects/interface.json").read_text(encoding="utf-8"))
    assert int(reduce_iface["metrics"]["shard_count"]) == 4

    summary = report["summary"]
    exemptions = {x["task"]: x for x in summary["policy_exemptions"]}
    assert "prepare_data" in exemptions
    assert exemptions["prepare_data"]["read_exemptions"] == ["inputs/exempt_reference.txt"]
    assert exemptions["prepare_data"]["write_exemptions"] == ["logs/prepare_data.trace.log"]
    assert "subprocess_probe" in exemptions
    assert bool(exemptions["subprocess_probe"]["allow_subprocess"]) is True

    cert = json.loads((dst / ".claimguard/reports/claim_certificate_latest.json").read_text(encoding="utf-8"))
    assert cert["claim_class"] == "contract-certified"
    assert set(cert["claim_blocking_tasks"]) == {e["task"] for e in cert["evidence"]}


def test_all_features_example_claim_fail_scenario(tmp_path: Path) -> None:
    src = Path("examples/all-features-example").resolve()
    dst = (tmp_path / "all-features-example").resolve()
    shutil.copytree(src, dst)

    runner = PipelineRunner(dst / "claimguard.claim-fail.json")
    report = runner.run(max_workers=4)
    rows = {r["task"]: r for r in report["task_rows"]}

    assert rows["fit_model"]["status"] in {"ok", "replay_ok"}
    assert rows["publish_rank"]["status"] == "blocked"
    assert rows["publish_rank"]["blocked_reason"] == "gate_failure"
    assert any((g["name"] == "stability_gate" and not g["pass"]) for g in rows["publish_rank"]["gate_rows"])
    assert report["claim"]["claim_class"] == "blocked"
