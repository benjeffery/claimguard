from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from claimguard.cli import HumanLiveRenderer, LLMStreamRenderer, main
from claimguard import cli as cli_module


def test_cli_report_and_doctor_commands(capsys) -> None:
    contract = Path("examples/minimal-example/claimguard.json").resolve()
    assert contract.exists()

    rc = main(["run", "--contract", str(contract), "--clean-state", "--llm-output"])
    assert rc == 0

    rc = main(["report", "--contract", str(contract)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "claim_class:" in out
    assert "status_counts:" in out
    assert "policy_exemptions_count:" in out
    assert "claim_certificate_json:" in out

    cert_path = contract.parent / ".claimguard" / "reports" / "claim_certificate_latest.json"
    assert cert_path.exists()
    cert = json.loads(cert_path.read_text(encoding="utf-8"))
    assert cert["artifact"] == "claimguard_claim_certificate"

    rc = main(["doctor", "--contract", str(contract)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "doctor: ok" in out


def test_cli_doctor_graphviz_outputs_dot(capsys) -> None:
    contract = Path("examples/minimal-example/claimguard.json").resolve()
    assert contract.exists()

    rc = main(["doctor", "--contract", str(contract), "--graphviz"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("digraph ")
    assert '"prepare_data" -> "fit_model";' in out
    assert '"fit_model" -> "publish_rank";' in out
    assert '"fit_model" -> "diagnostic_probe";' in out


def test_cli_doctor_graphviz_outputs_png(tmp_path, monkeypatch, capsys) -> None:
    contract = Path("examples/minimal-example/claimguard.json").resolve()
    assert contract.exists()

    out_png = tmp_path / "graph.png"

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_dot_which(_: str) -> str:
        return "dot"

    def fake_run(cmd: list[str], input: str, text: bool, capture_output: bool, check: bool = False) -> Any:
        assert cmd[:1] == ["dot"]
        output_index = cmd.index("-o")
        out_path = Path(cmd[output_index + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"png")
        return FakeCompleted()

    monkeypatch.setattr(cli_module.shutil, "which", fake_dot_which)
    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    rc = main(["doctor", "--contract", str(contract), "--png", str(out_png)])
    assert rc == 0

    out = capsys.readouterr().out
    assert str(out_png.resolve()) in out
    assert out_png.exists()
    assert out_png.read_bytes() == b"png"


def test_cli_doctor_audit_inputs_lists_root_inputs(capsys) -> None:
    contract = Path("examples/minimal-example/claimguard.json").resolve()
    assert contract.exists()

    rc = main(["doctor", "--contract", str(contract), "--audit-inputs"])
    assert rc == 0
    out = capsys.readouterr().out
    rows = [line.strip() for line in out.splitlines() if line.strip()]
    assert rows == [
        "fit_model\tinputs/fit_config.json",
        "prepare_data\tinputs/fit_config.json",
        "prepare_data\tinputs/raw_measurements.csv",
    ]


def test_cli_llm_output_uses_run_and_summary_events_only(capsys) -> None:
    contract = Path("examples/minimal-example/claimguard.json").resolve()
    assert contract.exists()

    rc = main(["run", "--contract", str(contract), "--clean-state", "--llm-output"])
    assert rc == 0
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert events

    event_types = [str(e.get("event", "")) for e in events]
    assert event_types[0] == "run_start"
    assert event_types[-1] == "run_end"
    assert "task_start" not in event_types
    assert "task_end" not in event_types

    summaries = [e for e in events if e.get("event") == "task_summary"]
    assert summaries
    final_summary = summaries[-1]
    run_start = events[0]
    assert final_summary["run_id"] == run_start["run_id"]
    assert "current_task" in final_summary
    assert "task_started" in final_summary
    assert "task_running" in final_summary
    assert int(final_summary["task_started"]) == int(run_start["task_count"])
    assert int(final_summary["task_done"]) == int(run_start["task_count"])
    assert int(final_summary["task_left"]) == 0
    assert int(final_summary["task_running"]) == 0


def test_cli_run_defaults_to_human_output(capsys) -> None:
    contract = Path("examples/minimal-example/claimguard.json").resolve()
    assert contract.exists()

    rc = main(["run", "--contract", str(contract), "--clean-state"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[task_start]" in out
    assert "[task_end]" in out
    assert "run complete:" in out
    assert "top_runtime_tasks:" in out
    assert "leaf_tasks:" in out


def test_llm_renderer_includes_map_progress_in_task_summary() -> None:
    renderer = LLMStreamRenderer(interval_s=60.0)
    renderer.emit({"event": "run_start", "run_id": "r1", "task_count": 2})
    renderer.emit({"event": "task_start", "task": "map_worker"})
    renderer.emit(
        {
            "event": "task_progress",
            "task": "map_worker",
            "done": 2,
            "total": 5,
            "fraction": 0.4,
            "message": "processing",
        }
    )
    renderer.emit(
        {
            "event": "map_progress",
            "task": "map_worker",
            "shard_total": 5,
            "shard_done": 2,
            "shard_running": 2,
        }
    )
    summary = renderer._summary_event()
    assert summary["current_task"] == "map_worker"
    assert summary["task_started"] == 1
    assert summary["task_done"] == 0
    assert summary["task_running"] == 1
    assert summary["current_task_progress"] == {
        "done": 2,
        "total": 5,
        "fraction": 0.4,
        "message": "processing",
    }
    assert summary["map_progress"] == {
        "task": "map_worker",
        "shard_total": 5,
        "shard_done": 2,
        "shard_left": 3,
        "shard_running": 2,
    }
    renderer.emit({"event": "run_end", "run_id": "r1"})


def test_human_renderer_tracks_current_task_stats_and_map_progress(capsys) -> None:
    renderer = HumanLiveRenderer()
    renderer.is_tty = False
    renderer.emit({"event": "run_start", "run_id": "r1", "task_count": 2, "pipeline": "p"})
    renderer.emit({"event": "task_start", "task": "map_worker", "index": 1})
    capsys.readouterr()
    renderer.emit(
        {
            "event": "task_stats",
            "tasks": {
                "map_worker": {
                    "runtime_s": 3.2,
                    "rss_bytes": 104857600,
                }
            },
        }
    )
    assert capsys.readouterr().out == ""
    renderer.emit(
        {
            "event": "map_progress",
            "task": "map_worker",
            "shard_total": 5,
            "shard_done": 2,
            "shard_running": 2,
        }
    )
    renderer.emit(
        {
            "event": "task_progress",
            "task": "map_worker",
            "done": 4,
            "total": 10,
            "fraction": 0.4,
            "phase": "fit",
            "message": "epoch 4",
        }
    )
    slot = renderer.active_tasks["map_worker"]
    assert float(slot["runtime_s"]) == 3.2
    assert int(slot["rss_bytes"]) == 104857600
    assert int(slot["shard_total"]) == 5
    assert int(slot["shard_done"]) == 2
    assert int(slot["shard_running"]) == 2
    assert slot["progress"] == {
        "done": 4,
        "total": 10,
        "fraction": 0.4,
        "phase": "fit",
        "message": "epoch 4",
    }


def test_human_renderer_run_end_prints_helpful_summary(capsys) -> None:
    renderer = HumanLiveRenderer()
    renderer.is_tty = False
    renderer.emit({"event": "run_start", "run_id": "r1", "task_count": 3, "pipeline": "p"})
    capsys.readouterr()
    renderer.emit(
        {
            "event": "run_end",
            "run_id": "r1",
            "claim_class": "contract-certified",
            "report_json": "/tmp/report.json",
            "claim_certificate_json": "/tmp/cert.json",
            "summary": {
                "runtime_seconds": 12.5,
                "task_status_counts": {"ok": 2, "replay_ok": 1, "diagnostic_only": 0, "blocked": 0},
            },
            "top_runtime_tasks": [
                {"task": "a", "status": "ok", "runtime_seconds": 6.0, "runtime_share": 0.6},
                {"task": "b", "status": "ok", "runtime_seconds": 3.0, "runtime_share": 0.3},
            ],
            "leaf_tasks": [
                {"task": "publish", "status": "ok", "runtime_seconds": 1.2},
                {"task": "diagnostic", "status": "diagnostic_only", "runtime_seconds": 0.8},
            ],
        }
    )
    out = capsys.readouterr().out
    assert "run complete: pipeline=p run_id=r1 done=0/3 runtime=12.500s" in out
    assert "status_counts: ok=2 replay=1 diag=0 blocked=0" in out
    assert "top_runtime_tasks:" in out
    assert "1. a status=ok runtime=6.000s share=60.0%" in out
    assert "leaf_tasks:" in out
    assert "- publish: ok (1.200s)" in out
    assert "claim_class: contract-certified" in out
    assert "report_json: /tmp/report.json" in out
    assert "claim_certificate_json: /tmp/cert.json" in out


def test_human_renderer_recent_rows_returns_unused_non_ok_space() -> None:
    renderer = HumanLiveRenderer()
    renderer.rows = [
        {"task": "a", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
        {"task": "b", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
        {"task": "c", "status": "blocked", "cache_reason": "", "blocked_reason": "x", "runtime_s": 1.0},
        {"task": "d", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
        {"task": "e", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
    ]

    selected = renderer._select_recent_rows(limit=5)
    assert [row["task"] for row in selected] == ["c", "e", "d", "b", "a"]


def test_human_renderer_recent_rows_uses_all_for_latest_when_non_ok_absent() -> None:
    renderer = HumanLiveRenderer()
    renderer.rows = [
        {"task": "a", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
        {"task": "b", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
        {"task": "c", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
    ]

    selected = renderer._select_recent_rows(limit=2)
    assert [row["task"] for row in selected] == ["c", "b"]


def test_human_renderer_recent_rows_appends_more_for_hidden_non_ok() -> None:
    renderer = HumanLiveRenderer()
    renderer.non_ok_fraction = 0.4
    renderer.rows = [
        {"task": "a", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
        {"task": "b", "status": "blocked", "cache_reason": "", "blocked_reason": "x", "runtime_s": 1.0},
        {"task": "c", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
        {"task": "d", "status": "blocked", "cache_reason": "", "blocked_reason": "x", "runtime_s": 1.0},
        {"task": "e", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
        {"task": "f", "status": "blocked", "cache_reason": "", "blocked_reason": "x", "runtime_s": 1.0},
        {"task": "g", "status": "blocked", "cache_reason": "", "blocked_reason": "x", "runtime_s": 1.0},
    ]

    selected = renderer._select_recent_rows(limit=5)
    assert [str(row.get("task", "")) for row in selected[:-1]] == ["g", "f", "e", "d"]
    assert selected[-1]["__kind"] == "more_non_ok"
    assert int(selected[-1]["more_count"]) == 1


def test_human_renderer_recent_rows_limit_one_does_not_overflow() -> None:
    renderer = HumanLiveRenderer()
    renderer.rows = [
        {"task": "a", "status": "blocked", "cache_reason": "", "blocked_reason": "x", "runtime_s": 1.0},
        {"task": "b", "status": "ok", "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0},
        {"task": "c", "status": "blocked", "cache_reason": "", "blocked_reason": "x", "runtime_s": 1.0},
    ]

    selected = renderer._select_recent_rows(limit=1)
    assert len(selected) == 1
    assert selected[0]["__kind"] == "more_non_ok"


def test_human_renderer_flask_frame_has_expected_size() -> None:
    renderer = HumanLiveRenderer()
    renderer._render_tick = 0
    frame = renderer._flask_frame_lines()
    assert len(frame) == 5
    assert all(len(line) == 10 for line in frame)


def test_human_renderer_flask_frames_keep_fixed_size_across_animation() -> None:
    renderer = HumanLiveRenderer()
    for tick in range(24):
        renderer._render_tick = tick
        frame = renderer._flask_frame_lines()
        assert len(frame) == 5
        assert all(len(line) == 10 for line in frame)


def test_human_renderer_tty_render_reserves_last_terminal_row(monkeypatch, capsys) -> None:
    renderer = HumanLiveRenderer()
    renderer.is_tty = True
    renderer.use_color = False
    renderer.flask_min_cols = 9999
    renderer._alt_screen_active = True
    renderer.task_count = 100
    renderer.task_done = 50
    renderer.pipeline = "p"
    renderer.run_id = "r1"
    renderer.rows = [
        {"task": f"t{i}", "status": ("blocked" if i % 7 == 0 else "ok"), "cache_reason": "", "blocked_reason": "", "runtime_s": 1.0}
        for i in range(120)
    ]
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=(120, 40): os.terminal_size((80, 20)))

    renderer._render(final=False, event_type="task_stats")
    out = capsys.readouterr().out
    clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out)
    assert len(clean.splitlines()) <= 18
    assert not clean.endswith("\n")
