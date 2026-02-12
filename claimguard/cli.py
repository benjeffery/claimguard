"""CLI for minimal claimguard runtime."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from .discovery import graphviz_dot
from .runner import PipelineRunner


DEFAULT_CONTRACT = Path("claimguard.json")


class HumanLiveRenderer:
    def __init__(self) -> None:
        self.is_tty = sys.stdout.isatty()
        self.run_id = ""
        self.pipeline = ""
        self.task_count = 0
        self.task_done = 0
        self.current_task = ""
        self.current_index = 0
        self.status_counts = {"ok": 0, "replay_ok": 0, "diagnostic_only": 0, "blocked": 0}
        self.rows: list[dict[str, Any]] = []
        self.run_t0 = time.perf_counter()
        self.report_json = ""
        self.claim_certificate_json = ""
        self.claim_class = ""

    def emit(self, event: dict[str, Any]) -> None:
        et = str(event.get("event", ""))
        if et == "run_start":
            self.run_id = str(event.get("run_id", ""))
            self.pipeline = str(event.get("pipeline", ""))
            self.task_count = int(event.get("task_count", 0))
            self.run_t0 = time.perf_counter()
        elif et == "task_start":
            self.current_task = str(event.get("task", ""))
            self.current_index = int(event.get("index", 0))
        elif et == "task_end":
            status = str(event.get("status", ""))
            self.rows.append(
                {
                    "task": str(event.get("task", "")),
                    "status": status,
                    "cache_hit": bool(event.get("cache_hit", False)),
                    "cache_reason": str(event.get("cache_reason", "")),
                    "runtime_s": float(event.get("runtime_s", 0.0)),
                    "blocked_reason": str(event.get("blocked_reason", "")),
                }
            )
            self.task_done += 1
            self.current_task = ""
            self.current_index = 0
            if status in self.status_counts:
                self.status_counts[status] += 1
        elif et == "run_end":
            self.claim_class = str(event.get("claim_class", ""))
            self.report_json = str(event.get("report_json", ""))
            self.claim_certificate_json = str(event.get("claim_certificate_json", ""))
            summary = event.get("summary", {})
            if isinstance(summary, dict):
                counts = summary.get("task_status_counts", {})
                if isinstance(counts, dict):
                    for k in self.status_counts:
                        if k in counts:
                            self.status_counts[k] = int(counts[k])
        self._render(et == "run_end")

    def _status_label(self, row: dict[str, Any]) -> str:
        status = str(row["status"])
        if status == "ok":
            return "OK"
        if status == "replay_ok":
            return "REPLAY"
        if status == "diagnostic_only":
            return "DIAG"
        if status == "blocked":
            return "BLOCKED"
        return status.upper()

    def _progress_bar(self, width: int = 36) -> str:
        if self.task_count <= 0:
            return "[" + ("." * width) + "]"
        frac = min(max(self.task_done / self.task_count, 0.0), 1.0)
        fill = int(round(frac * width))
        return "[" + ("#" * fill) + ("." * (width - fill)) + "]"

    def _render(self, final: bool) -> None:
        elapsed = time.perf_counter() - self.run_t0
        if self.is_tty:
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write("claimguard live status\n")
            sys.stdout.write(f"pipeline: {self.pipeline or '-'}\n")
            sys.stdout.write(f"run_id: {self.run_id or '-'}\n")
            sys.stdout.write(
                f"progress: {self._progress_bar()} {self.task_done}/{self.task_count} "
                f"elapsed={elapsed:.1f}s\n"
            )
            sys.stdout.write(
                "counts: "
                f"ok={self.status_counts['ok']} "
                f"replay={self.status_counts['replay_ok']} "
                f"diag={self.status_counts['diagnostic_only']} "
                f"blocked={self.status_counts['blocked']}\n"
            )
            if self.current_task:
                sys.stdout.write(f"running: [{self.current_index}/{self.task_count}] {self.current_task}\n")
            else:
                sys.stdout.write("running: -\n")
            sys.stdout.write("\nrecent tasks:\n")
            sys.stdout.write("task                          status    runtime(s)  note\n")
            sys.stdout.write("---------------------------------------------------------------\n")
            for row in self.rows[-10:]:
                note = str(row["cache_reason"]) if row["cache_reason"] else row["blocked_reason"]
                sys.stdout.write(
                    f"{str(row['task'])[:28]:<28}  {self._status_label(row):<8}  "
                    f"{float(row['runtime_s']):>9.3f}  {str(note)[:18]}\n"
                )
            if final:
                sys.stdout.write("\n")
                sys.stdout.write(f"claim_class: {self.claim_class}\n")
                if self.report_json:
                    sys.stdout.write(f"report_json: {self.report_json}\n")
                if self.claim_certificate_json:
                    sys.stdout.write(f"claim_certificate_json: {self.claim_certificate_json}\n")
            sys.stdout.flush()
            return

        # Non-TTY fallback: line-oriented progress suitable for logs.
        if final:
            print(
                f"[run_end] claim_class={self.claim_class} "
                f"done={self.task_done}/{self.task_count} elapsed={elapsed:.1f}s "
                f"report={self.report_json} cert={self.claim_certificate_json}",
                flush=True,
            )
            return
        if self.current_task:
            print(
                f"[task_start] {self.current_index}/{self.task_count} {self.current_task}",
                flush=True,
            )
            return
        if self.rows:
            row = self.rows[-1]
            if row["cache_reason"]:
                note = f" {row['cache_reason']}"
            elif row["blocked_reason"]:
                note = f" {row['blocked_reason']}"
            else:
                note = ""
            print(
                f"[task_end] {row['task']} status={row['status']} t={row['runtime_s']:.3f}s{note}",
                flush=True,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claimguard")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a contract pipeline")
    run_p.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    run_p.add_argument("--clean-state", action="store_true", help="Remove .claimguard state folder before run")
    run_p.add_argument("--llm-output", action="store_true", help="Emit NDJSON event stream")
    run_p.add_argument("--jobs", type=int, default=None, help="Max parallel tasks (default: CPU core count)")
    run_p.add_argument(
        "--target",
        action="append",
        default=[],
        help="Target task name to run (with dependencies). Repeat for multiple targets.",
    )

    report_p = sub.add_parser("report", help="Show latest run report")
    report_p.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    report_p.add_argument("--json", action="store_true", help="Print full JSON report")

    doctor_p = sub.add_parser("doctor", help="Validate contract/task graph and environment")
    doctor_p.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    doctor_mode = doctor_p.add_mutually_exclusive_group()
    doctor_mode.add_argument("--graphviz", action="store_true", help="Print task DAG in Graphviz DOT format")
    doctor_mode.add_argument(
        "--audit-inputs",
        action="store_true",
        help="List root input files (declared task inputs with no producing task)",
    )

    args = parser.parse_args(argv)
    if args.command == "run":
        if not args.contract.exists():
            raise SystemExit(f"contract not found: {args.contract}")
        runner = PipelineRunner(args.contract)
        if args.clean_state:
            if runner.state_root.exists():
                shutil.rmtree(runner.state_root)
            runner.state_root.mkdir(parents=True, exist_ok=True)
            runner.cache_root.mkdir(parents=True, exist_ok=True)
            runner.report_root.mkdir(parents=True, exist_ok=True)
            runner.run_root.mkdir(parents=True, exist_ok=True)
        if args.llm_output:
            def emit(event: dict[str, object]) -> None:
                print(json.dumps(event, sort_keys=True), flush=True)
            runner.run(event_emitter=emit, max_workers=args.jobs, targets=list(args.target))
        else:
            human = HumanLiveRenderer()
            runner.run(event_emitter=human.emit, max_workers=args.jobs, targets=list(args.target))
        return 0
    if args.command == "report":
        if not args.contract.exists():
            raise SystemExit(f"contract not found: {args.contract}")
        runner = PipelineRunner(args.contract)
        report_path = runner.report_root / "run_report_latest.json"
        if not report_path.exists():
            raise SystemExit(f"run report not found: {report_path}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        summary = report.get("summary", {})
        claim = report.get("claim", {})
        cert_path = runner.report_root / "claim_certificate_latest.json"
        print(f"pipeline: {report.get('pipeline', '-')}")
        print(f"run_id: {report.get('run_id', '-')}")
        print(f"claim_class: {claim.get('claim_class', '-')}")
        print(f"claim_reason: {claim.get('claim_reason', '-')}")
        print(f"rng_policy: {summary.get('rng_policy', 'off')}")
        print(f"rng_seed_base: {summary.get('rng_seed_base', 0)}")
        print(
            "status_counts: "
            f"{summary.get('task_status_counts', {})}"
        )
        print(
            "cache_reasons: "
            f"{summary.get('cache_reason_counts', {})}"
        )
        ex = summary.get("policy_exemptions", [])
        print(f"policy_exemptions_count: {len(ex) if isinstance(ex, list) else 0}")
        print(f"report_json: {report_path.resolve()}")
        print(f"claim_certificate_json: {cert_path.resolve()}")
        return 0
    if args.command == "doctor":
        if not args.contract.exists():
            raise SystemExit(f"contract not found: {args.contract}")
        runner = PipelineRunner(args.contract)
        if args.graphviz:
            sys.stdout.write(graphviz_dot(runner.task_specs, runner.deps, graph_name=str(runner.contract.get("pipeline_name", "claimguard"))))
            return 0
        if args.audit_inputs:
            produced = {out for spec in runner.task_specs.values() for out in spec.outputs}
            root_inputs = sorted({rel for spec in runner.task_specs.values() for rel in spec.inputs if rel not in produced})
            for rel in root_inputs:
                print(rel)
            return 0
        print(f"workspace_root: {runner.workspace_root}")
        print(f"pipeline: {runner.contract.get('pipeline_name', '-')}")
        print(f"tasks_discovered: {len(runner.task_specs)}")
        print(f"task_order: {runner.order}")
        produced = {out for spec in runner.task_specs.values() for out in spec.outputs}
        missing_inputs = []
        for name, spec in runner.task_specs.items():
            for rel in spec.inputs:
                path = (runner.workspace_root / rel).resolve()
                if rel not in produced and not path.exists():
                    missing_inputs.append((name, rel))
        if missing_inputs:
            print("doctor: fail")
            print("missing_nonproduced_inputs:")
            for task, rel in missing_inputs:
                print(f"- {task}: {rel}")
            return 2
        print("doctor: ok")
        print("missing_nonproduced_inputs: none")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
