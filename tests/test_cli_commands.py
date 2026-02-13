from __future__ import annotations

import json
from pathlib import Path

from claimguard.cli import HumanLiveRenderer, LLMStreamRenderer, main


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
    assert "[run_end]" in out


def test_llm_renderer_includes_map_progress_in_task_summary() -> None:
    renderer = LLMStreamRenderer(interval_s=60.0)
    renderer.emit({"event": "run_start", "run_id": "r1", "task_count": 2})
    renderer.emit({"event": "task_start", "task": "map_worker"})
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
    slot = renderer.active_tasks["map_worker"]
    assert float(slot["runtime_s"]) == 3.2
    assert int(slot["rss_bytes"]) == 104857600
    assert int(slot["shard_total"]) == 5
    assert int(slot["shard_done"]) == 2
    assert int(slot["shard_running"]) == 2


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


def test_human_renderer_flask_frame_has_expected_size() -> None:
    renderer = HumanLiveRenderer()
    renderer._render_tick = 0
    frame = renderer._flask_frame_lines()
    assert len(frame) == 5
    assert all(len(line) == 10 for line in frame)
