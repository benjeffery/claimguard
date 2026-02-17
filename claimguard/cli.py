"""CLI for minimal claimguard runtime."""

from __future__ import annotations

import argparse
import json
import subprocess
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .discovery import graphviz_dot
from .runner import PipelineRunner


DEFAULT_CONTRACT = Path("claimguard.json")


class HumanLiveRenderer:
    def __init__(self) -> None:
        self.is_tty = sys.stdout.isatty()
        self.use_color = self._detect_color()
        self.max_tty_fps = 5.0
        self.non_ok_fraction = 0.4
        self.flask_min_cols = 88
        self.flask_min_lines = 18
        self.run_id = ""
        self.pipeline = ""
        self.task_count = 0
        self.task_done = 0
        self.current_task = ""
        self.current_index = 0
        self.active_tasks: dict[str, dict[str, Any]] = {}
        self.status_counts = {"ok": 0, "replay_ok": 0, "diagnostic_only": 0, "blocked": 0}
        self.rows: list[dict[str, Any]] = []
        self.run_t0 = time.perf_counter()
        self.report_json = ""
        self.claim_certificate_json = ""
        self.claim_class = ""
        self.total_runtime_s = 0.0
        self.top_runtime_tasks: list[dict[str, Any]] = []
        self.leaf_tasks: list[dict[str, Any]] = []
        self._last_tty_render_t = 0.0
        self._render_tick = 0
        self._alt_screen_active = False
        self._saw_run_end = False

    def emit(self, event: dict[str, Any]) -> None:
        et = str(event.get("event", ""))
        if et == "run_start":
            self.run_id = str(event.get("run_id", ""))
            self.pipeline = str(event.get("pipeline", ""))
            self.task_count = int(event.get("task_count", 0))
            self.run_t0 = time.perf_counter()
            self.active_tasks = {}
        elif et == "task_start":
            self.current_task = str(event.get("task", ""))
            self.current_index = int(event.get("index", 0))
            self.active_tasks[self.current_task] = {
                "index": self.current_index,
                "started_t": float(time.perf_counter()),
                "runtime_s": 0.0,
                "rss_bytes": 0,
                "shard_total": 0,
                "shard_done": 0,
                "shard_running": 0,
            }
        elif et == "task_stats":
            tasks = event.get("tasks", {})
            if isinstance(tasks, dict):
                for task_name, slot in tasks.items():
                    if not isinstance(slot, dict):
                        continue
                    if task_name not in self.active_tasks:
                        self.active_tasks[task_name] = {
                            "index": 0,
                            "started_t": float(time.perf_counter()),
                            "runtime_s": 0.0,
                            "rss_bytes": 0,
                            "shard_total": 0,
                            "shard_done": 0,
                            "shard_running": 0,
                        }
                    entry = self.active_tasks[task_name]
                    entry["runtime_s"] = float(slot.get("runtime_s", entry.get("runtime_s", 0.0)))
                    entry["rss_bytes"] = int(slot.get("rss_bytes", entry.get("rss_bytes", 0)))
        elif et == "map_progress":
            task_name = str(event.get("task", ""))
            if task_name not in self.active_tasks:
                self.active_tasks[task_name] = {
                    "index": 0,
                    "started_t": float(time.perf_counter()),
                    "runtime_s": 0.0,
                    "rss_bytes": 0,
                    "shard_total": 0,
                    "shard_done": 0,
                    "shard_running": 0,
                }
            entry = self.active_tasks[task_name]
            entry["shard_total"] = int(event.get("shard_total", 0))
            entry["shard_done"] = int(event.get("shard_done", 0))
            entry["shard_running"] = int(event.get("shard_running", 0))
        elif et == "task_progress":
            task_name = str(event.get("task", ""))
            if task_name not in self.active_tasks:
                self.active_tasks[task_name] = {
                    "index": 0,
                    "started_t": float(time.perf_counter()),
                    "runtime_s": 0.0,
                    "rss_bytes": 0,
                    "shard_total": 0,
                    "shard_done": 0,
                    "shard_running": 0,
                }
            entry = self.active_tasks[task_name]
            progress: dict[str, Any] = {}
            if "done" in event:
                progress["done"] = int(event.get("done", 0))
            if "total" in event:
                progress["total"] = int(event.get("total", 0))
            if "fraction" in event:
                progress["fraction"] = float(event.get("fraction", 0.0))
            if "phase" in event:
                phase = str(event.get("phase", "")).strip()
                if phase:
                    progress["phase"] = phase
            if "message" in event:
                message = str(event.get("message", "")).strip()
                if message:
                    progress["message"] = message
            if progress:
                entry["progress"] = progress
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
            done_task = str(event.get("task", ""))
            self.active_tasks.pop(done_task, None)
            if done_task == self.current_task:
                self.current_task = ""
                self.current_index = 0
            if status in self.status_counts:
                self.status_counts[status] += 1
        elif et == "run_end":
            self._saw_run_end = True
            self.claim_class = str(event.get("claim_class", ""))
            self.report_json = str(event.get("report_json", ""))
            self.claim_certificate_json = str(event.get("claim_certificate_json", ""))
            top_runtime = event.get("top_runtime_tasks", [])
            self.top_runtime_tasks = list(top_runtime) if isinstance(top_runtime, list) else []
            leaf_tasks = event.get("leaf_tasks", [])
            self.leaf_tasks = list(leaf_tasks) if isinstance(leaf_tasks, list) else []
            summary = event.get("summary", {})
            if isinstance(summary, dict):
                counts = summary.get("task_status_counts", {})
                if isinstance(counts, dict):
                    for k in self.status_counts:
                        if k in counts:
                            self.status_counts[k] = int(counts[k])
                self.total_runtime_s = float(summary.get("runtime_seconds", 0.0) or 0.0)
        final = et == "run_end"
        if self.is_tty and not final and et in {"task_stats", "map_progress", "task_progress"}:
            now = float(time.perf_counter())
            min_interval = 1.0 / max(self.max_tty_fps, 1.0)
            if (now - self._last_tty_render_t) < min_interval:
                return
        if self.is_tty:
            self._last_tty_render_t = float(time.perf_counter())
            self._render_tick += 1
        self._render(final, et)

    def close(self) -> None:
        if not self.is_tty:
            return
        if self._alt_screen_active:
            sys.stdout.write("\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
            self._alt_screen_active = False
        if self._saw_run_end:
            self._print_human_run_summary()

    def _detect_color(self) -> bool:
        if not self.is_tty:
            return False
        if os.environ.get("NO_COLOR") is not None:
            return False
        term = str(os.environ.get("TERM", "")).strip().lower()
        if not term or term == "dumb":
            return False
        return True

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

    def _fit(self, text: object, width: int) -> str:
        w = max(int(width), 0)
        if w <= 0:
            return ""
        s = str(text)
        if len(s) <= w:
            return s
        if w == 1:
            return s[:1]
        return s[: w - 1] + "…"

    def _colorize(self, text: str, code: str) -> str:
        if not self.use_color:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def _status_color_code(self, status: str) -> str:
        if status == "ok":
            return "32"
        if status == "replay_ok":
            return "36"
        if status == "diagnostic_only":
            return "33"
        if status == "blocked":
            return "31"
        return "37"

    def _system_mem_bytes(self) -> tuple[int, int]:
        total = 0
        available = 0
        try:
            meminfo = Path("/proc/meminfo")
            if meminfo.exists():
                for line in meminfo.read_text(encoding="utf-8").splitlines():
                    if line.startswith("MemTotal:"):
                        total = int(line.split()[1]) * 1024
                    elif line.startswith("MemAvailable:"):
                        available = int(line.split()[1]) * 1024
                if total > 0:
                    if available <= 0:
                        available = total
                    return total, min(max(available, 0), total)
        except Exception:
            pass
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            total_pages = int(os.sysconf("SC_PHYS_PAGES"))
            total = page_size * total_pages
            if total > 0:
                return total, total
        except Exception:
            pass
        return 0, 0

    def _select_recent_rows(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        recent_desc = list(reversed(self.rows))
        non_ok_candidates = [row for row in recent_desc if str(row.get("status", "")) != "ok"]
        reserve = int(limit * self.non_ok_fraction)
        if non_ok_candidates and reserve <= 0:
            reserve = 1
        reserve = max(0, min(limit, reserve))
        non_ok = non_ok_candidates[:reserve]
        used = {id(row) for row in non_ok}
        latest_budget = max(limit - len(non_ok), 0)
        latest: list[dict[str, Any]] = []
        if latest_budget > 0:
            for row in recent_desc:
                if id(row) in used:
                    continue
                latest.append(row)
                if len(latest) >= latest_budget:
                    break
        selected = non_ok + latest
        selected_ids = {id(row) for row in selected}
        hidden_non_ok_count = sum(
            1
            for row in non_ok_candidates
            if id(row) not in selected_ids
        )
        if hidden_non_ok_count > 0:
            if limit == 1:
                return [{"__kind": "more_non_ok", "more_count": int(hidden_non_ok_count)}]
            if len(selected) >= limit:
                removed = selected.pop()
                if str(removed.get("status", "")) != "ok":
                    hidden_non_ok_count += 1
            selected.append(
                {
                    "__kind": "more_non_ok",
                    "more_count": int(hidden_non_ok_count),
                }
            )
        return selected[:limit]

    def _flask_frame_lines(self) -> list[str]:
        canvas = [
            list("   │  │   "),
            list("  /    \\  "),
            list(" /      \\ "),
            list("/~~~~~~~~\\"),
            list("└────────┘"),
        ]

        # Bubble lanes rise from liquid level (row 3) to neck (row 0), then pause.
        cycle_len = 12
        bubble_lanes = [
            {"offset": 0, "x": {3: 2, 2: 3, 1: 4, 0: 4}, "chars": "oO*."},
            {"offset": 4, "x": {3: 5, 2: 5, 1: 5, 0: 5}, "chars": ".oO*"},
            {"offset": 8, "x": {3: 7, 2: 6, 1: 5, 0: 5}, "chars": "*.oO"},
        ]
        tick = int(self._render_tick)
        for lane in bubble_lanes:
            phase = (tick + int(lane["offset"])) % cycle_len
            if phase >= 4:
                continue
            row = 3 - phase
            col = int(lane["x"][row])
            glyphs = str(lane["chars"])
            canvas[row][col] = glyphs[(tick + phase) % len(glyphs)]

        return ["".join(row) for row in canvas]

    def _colorize_flask_line(self, line: str, *, row: int) -> str:
        if not self.use_color:
            return line
        glass_code = "96"
        liquid_codes = ("34", "36", "35")
        bubble_codes = ("33", "35", "36", "32", "94")
        out: list[str] = []
        for col, ch in enumerate(line):
            if ch in {"│", "/", "\\", "└", "┘", "─"}:
                out.append(self._colorize(ch, glass_code))
                continue
            if ch == "~":
                code = liquid_codes[(self._render_tick + row + col) % len(liquid_codes)]
                out.append(self._colorize(ch, code))
                continue
            if ch in {"o", "O", "*", "."}:
                code = bubble_codes[(self._render_tick + row + col) % len(bubble_codes)]
                out.append(self._colorize(ch, code))
                continue
            out.append(ch)
        return "".join(out)

    def _render(self, final: bool, event_type: str) -> None:
        elapsed = time.perf_counter() - self.run_t0
        if self.is_tty:
            if not self._alt_screen_active:
                sys.stdout.write("\x1b[?1049h\x1b[?25l")
                sys.stdout.flush()
                self._alt_screen_active = True
            term = shutil.get_terminal_size(fallback=(120, 40))
            width = max(int(term.columns) - 1, 1)
            height = max(int(term.lines), 16)
            show_flask = width >= int(self.flask_min_cols) and height >= int(self.flask_min_lines)
            total_mem_bytes, avail_mem_bytes = self._system_mem_bytes()
            headroom_ratio = (float(avail_mem_bytes) / float(total_mem_bytes)) if total_mem_bytes > 0 else 0.0

            progress_suffix = f" {self.task_done}/{self.task_count} elapsed={elapsed:.1f}s"
            bar_width = max(8, min(64, width - len("progress: ") - len(progress_suffix)))
            progress_line = f"progress: {self._progress_bar(width=bar_width)}{progress_suffix}"
            counts_line = (
                "counts: "
                f"ok={self.status_counts['ok']} "
                f"replay={self.status_counts['replay_ok']} "
                f"diag={self.status_counts['diagnostic_only']} "
                f"blocked={self.status_counts['blocked']}"
            )

            current_header = "current tasks:"
            recent_header = f"recent tasks (non-ok reserve={int(self.non_ok_fraction * 100)}%):"
            header_lines_count = 5 if show_flask else 4
            fixed_lines = header_lines_count + 8
            if final:
                fixed_lines += 4
            data_budget = max(height - fixed_lines - 2, 1)

            active_rows = sorted(
                self.active_tasks.items(),
                key=lambda kv: (int(kv[1].get("index", 0)), str(kv[0])),
            )
            current_needed = max(1, len(active_rows)) + 1
            current_rows_budget = min(current_needed, max(1, data_budget // 2))
            recent_rows_budget = max(data_budget - current_rows_budget, 1)
            if current_rows_budget + recent_rows_budget > data_budget:
                current_rows_budget = max(data_budget - recent_rows_budget, 1)

            runtime_w = 8
            mem_w = 8
            map_w = max(6, min(24, width // 4))
            task_w = width - runtime_w - mem_w - map_w - 3
            if task_w < 6:
                deficit = 6 - task_w
                shrink = min(deficit, map_w - 6)
                map_w -= shrink
                deficit -= shrink
                shrink = min(deficit, mem_w - 6)
                mem_w -= shrink
                deficit -= shrink
                shrink = min(deficit, runtime_w - 6)
                runtime_w -= shrink
                task_w = width - runtime_w - mem_w - map_w - 3
            task_w = max(task_w, 1)

            recent_status_w = 7
            recent_runtime_w = 8
            recent_note_w = max(6, min(28, width // 3))
            recent_task_w = width - recent_status_w - recent_runtime_w - recent_note_w - 3
            if recent_task_w < 6:
                deficit = 6 - recent_task_w
                shrink = min(deficit, recent_note_w - 6)
                recent_note_w -= shrink
                deficit -= shrink
                shrink = min(deficit, recent_status_w - 5)
                recent_status_w -= shrink
                deficit -= shrink
                shrink = min(deficit, recent_runtime_w - 6)
                recent_runtime_w -= shrink
                recent_task_w = width - recent_status_w - recent_runtime_w - recent_note_w - 3
            recent_task_w = max(recent_task_w, 1)

            now = float(time.perf_counter())
            total_active_rss_bytes = sum(int(slot.get("rss_bytes", 0)) for slot in self.active_tasks.values())
            total_active_mem_mib = float(total_active_rss_bytes / (1024 * 1024))
            if total_mem_bytes <= 0:
                mem_code = ""
            elif headroom_ratio < 0.05:
                mem_code = "31"
            elif headroom_ratio < 0.10:
                mem_code = "33"
            else:
                mem_code = "32"

            current_lines: list[str] = []
            row_slots_for_active = max(current_rows_budget - 1, 0)
            shown_active = active_rows[:row_slots_for_active]
            if shown_active:
                for task_name, slot in shown_active:
                    runtime_s = float(slot.get("runtime_s", 0.0))
                    if runtime_s <= 0.0:
                        runtime_s = max(now - float(slot.get("started_t", now)), 0.0)
                    mem_mib = float(int(slot.get("rss_bytes", 0)) / (1024 * 1024))
                    shard_total = int(slot.get("shard_total", 0))
                    if shard_total > 0:
                        shard_done = int(slot.get("shard_done", 0))
                        shard_running = int(slot.get("shard_running", 0))
                        pct = int((100.0 * shard_done / shard_total)) if shard_total > 0 else 0
                        map_note = f"{shard_done}/{shard_total} run={shard_running} {pct}%"
                        map_note = self._colorize(self._fit(map_note, map_w), "36")
                    else:
                        progress = slot.get("progress", {})
                        if isinstance(progress, dict) and progress:
                            note_bits: list[str] = []
                            if "done" in progress and "total" in progress:
                                note_bits.append(f"{int(progress['done'])}/{int(progress['total'])}")
                            elif "fraction" in progress:
                                note_bits.append(f"{int(float(progress['fraction']) * 100)}%")
                            for label in ("phase", "message"):
                                if label in progress:
                                    text = str(progress[label]).strip()
                                    if text:
                                        note_bits.append(text)
                                        break
                            map_note = self._fit(" ".join(note_bits), map_w) if note_bits else self._fit("-", map_w)
                        else:
                            map_note = self._fit("-", map_w)
                    current_lines.append(
                        f"{self._fit(task_name, task_w):<{task_w}}"
                        " "
                        f"{runtime_s:>{runtime_w}.1f}"
                        " "
                        f"{mem_mib:>{mem_w}.1f}"
                        " "
                        f"{map_note:>{map_w}}"
                    )
            elif current_rows_budget > 1:
                current_lines.append(
                    f"{self._fit('-', task_w):<{task_w}}"
                    " "
                    f"{'-':>{runtime_w}}"
                    " "
                    f"{'-':>{mem_w}}"
                    " "
                    f"{'-':>{map_w}}"
                )

            mem_total_plain = f"{total_active_mem_mib:>{mem_w}.1f}"
            mem_total_text = self._colorize(mem_total_plain, mem_code) if mem_code else mem_total_plain
            total_line = (
                f"{self._fit('TOTAL', task_w):<{task_w}}"
                " "
                f"{'-':>{runtime_w}}"
                " "
                f"{mem_total_text}"
                " "
                f"{'':>{map_w}}"
            )
            current_lines.append(total_line)
            if len(current_lines) > current_rows_budget:
                current_lines = current_lines[-current_rows_budget:]

            recent_lines: list[str] = []
            for row in self._select_recent_rows(recent_rows_budget):
                if str(row.get("__kind", "")) == "more_non_ok":
                    more = max(int(row.get("more_count", 0)), 1)
                    recent_lines.append(
                        f"{self._fit(f'+{more} more', recent_task_w):<{recent_task_w}}"
                        " "
                        f"{self._fit('-', recent_status_w):<{recent_status_w}}"
                        " "
                        f"{'-':>{recent_runtime_w}}"
                        " "
                        f"{self._fit('non-ok hidden', recent_note_w):>{recent_note_w}}"
                    )
                    continue
                note = str(row["cache_reason"]) if row["cache_reason"] else str(row["blocked_reason"])
                status = str(row.get("status", ""))
                status_plain = f"{self._status_label(row):<{recent_status_w}}"
                status_text = self._colorize(status_plain, self._status_color_code(status))
                recent_lines.append(
                    f"{self._fit(row['task'], recent_task_w):<{recent_task_w}}"
                    " "
                    f"{status_text}"
                    " "
                    f"{float(row['runtime_s']):>{recent_runtime_w}.3f}"
                    " "
                    f"{self._fit(note, recent_note_w):>{recent_note_w}}"
                )
            if not recent_lines:
                recent_lines.append(
                    f"{self._fit('-', recent_task_w):<{recent_task_w}}"
                    " "
                    f"{self._fit('-', recent_status_w):<{recent_status_w}}"
                    " "
                    f"{'-':>{recent_runtime_w}}"
                    " "
                    f"{self._fit('-', recent_note_w):>{recent_note_w}}"
                )

            screen_lines: list[str] = []
            if show_flask:
                flask_lines = self._flask_frame_lines()
                left_w = len(flask_lines[0]) if flask_lines else 10
                gap = 2
                right_w = max(width - left_w - gap, 0)
                right_lines = [
                    "claimguard live status",
                    f"pipeline: {self.pipeline or '-'}  run_id: {self.run_id or '-'}",
                    progress_line,
                    counts_line,
                ]
                block_h = max(len(flask_lines), len(right_lines))
                for i in range(block_h):
                    left = (
                        self._colorize_flask_line(flask_lines[i], row=i)
                        if i < len(flask_lines)
                        else (" " * left_w)
                    )
                    right = self._fit(right_lines[i], right_w) if i < len(right_lines) else ""
                    screen_lines.append(f"{left}{' ' * gap}{right}")
            else:
                screen_lines.append("claimguard live status")
                screen_lines.append(f"pipeline: {self.pipeline or '-'}  run_id: {self.run_id or '-'}")
                screen_lines.append(f"{self._fit(progress_line, width)}")
                screen_lines.append(f"{counts_line}")
            screen_lines.append("")
            screen_lines.append(self._fit(current_header, width))
            screen_lines.append(
                f"{self._fit('task', task_w):<{task_w}}"
                " "
                f"{'runtime(s)':>{runtime_w}}"
                " "
                f"{'mem(MiB)':>{mem_w}}"
                " "
                f"{self._fit('map', map_w):>{map_w}}"
            )
            screen_lines.append("-" * width)
            for line in current_lines:
                screen_lines.append(line)
            screen_lines.append("")
            screen_lines.append(self._fit(recent_header, width))
            screen_lines.append(
                f"{self._fit('task', recent_task_w):<{recent_task_w}}"
                " "
                f"{self._fit('status', recent_status_w):<{recent_status_w}}"
                " "
                f"{'runtime(s)':>{recent_runtime_w}}"
                " "
                f"{self._fit('note', recent_note_w):>{recent_note_w}}"
            )
            screen_lines.append("-" * width)
            for line in recent_lines:
                screen_lines.append(line)
            if final:
                screen_lines.append("")
                screen_lines.append(f"claim_class: {self.claim_class}")
                if self.report_json:
                    screen_lines.append(f"report_json: {self.report_json}")
                if self.claim_certificate_json:
                    screen_lines.append(f"claim_certificate_json: {self.claim_certificate_json}")
            sys.stdout.write("\x1b[2J\x1b[H" + "\n".join(screen_lines))
            sys.stdout.flush()
            return

        if final:
            self._print_human_run_summary()
            return
        if event_type == "task_start":
            print(
                f"[task_start] {self.current_index}/{self.task_count} {self.current_task}",
                flush=True,
            )
            return
        if event_type == "task_end" and self.rows:
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

    def _print_human_run_summary(self) -> None:
        runtime_s = float(self.total_runtime_s) if self.total_runtime_s > 0.0 else max(time.perf_counter() - self.run_t0, 0.0)
        print(
            f"run complete: pipeline={self.pipeline or '-'} run_id={self.run_id or '-'} "
            f"done={self.task_done}/{self.task_count} runtime={runtime_s:.3f}s",
            flush=True,
        )
        print(
            "status_counts: "
            f"ok={self.status_counts['ok']} "
            f"replay={self.status_counts['replay_ok']} "
            f"diag={self.status_counts['diagnostic_only']} "
            f"blocked={self.status_counts['blocked']}",
            flush=True,
        )
        print(f"claim_class: {self.claim_class}", flush=True)
        if self.top_runtime_tasks:
            print("top_runtime_tasks:", flush=True)
            for idx, row in enumerate(self.top_runtime_tasks[:3], start=1):
                task = str(row.get("task", ""))
                status = str(row.get("status", ""))
                task_runtime_s = float(row.get("runtime_seconds", 0.0) or 0.0)
                share = min(max(float(row.get("runtime_share", 0.0) or 0.0), 0.0), 1.0)
                print(
                    f"  {idx}. {task} status={status} runtime={task_runtime_s:.3f}s share={share * 100.0:.1f}%",
                    flush=True,
                )
        if self.leaf_tasks:
            print("leaf_tasks:", flush=True)
            for row in self.leaf_tasks:
                task = str(row.get("task", ""))
                status = str(row.get("status", ""))
                task_runtime_s = float(row.get("runtime_seconds", 0.0) or 0.0)
                print(f"  - {task}: {status} ({task_runtime_s:.3f}s)", flush=True)
        if self.report_json:
            print(f"report_json: {self.report_json}", flush=True)
        if self.claim_certificate_json:
            print(f"claim_certificate_json: {self.claim_certificate_json}", flush=True)


class LLMStreamRenderer:
    def __init__(self, *, interval_s: float = 60.0) -> None:
        self.interval_s = max(1.0, float(interval_s))
        self.run_id = ""
        self.task_count = 0
        self.task_started = 0
        self.task_done = 0
        self._active_tasks: list[str] = []
        self._map_progress_by_task: dict[str, dict[str, int]] = {}
        self._task_progress_by_task: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None

    def _summary_event(self) -> dict[str, object]:
        current_task = self._active_tasks[0] if self._active_tasks else ""
        done = int(self.task_done)
        started = int(self.task_started)
        running = int(len(self._active_tasks))
        total = int(self.task_count)
        left = max(total - done, 0)
        out: dict[str, object] = {
            "event": "task_summary",
            "run_id": self.run_id,
            "current_task": current_task,
            "task_started": started,
            "task_done": done,
            "task_left": left,
            "task_running": running,
        }
        in_progress_map_tasks = [
            task
            for task, progress in self._map_progress_by_task.items()
            if int(progress.get("shard_done", 0)) < int(progress.get("shard_total", 0))
        ]
        if in_progress_map_tasks:
            map_task = current_task if current_task in in_progress_map_tasks else in_progress_map_tasks[0]
            progress = self._map_progress_by_task.get(map_task, {})
            shard_total = int(progress.get("shard_total", 0))
            shard_done = int(progress.get("shard_done", 0))
            out["map_progress"] = {
                "task": map_task,
                "shard_total": shard_total,
                "shard_done": shard_done,
                "shard_left": max(shard_total - shard_done, 0),
                "shard_running": int(progress.get("shard_running", 0)),
            }
        task_progress = self._task_progress_by_task.get(current_task, {})
        if current_task and task_progress:
            out["current_task_progress"] = dict(task_progress)
        return out

    def _tick(self) -> None:
        while not self._stop.wait(self.interval_s):
            with self._lock:
                if not self.run_id:
                    continue
                payload = self._summary_event()
            print(json.dumps(payload, sort_keys=True), flush=True)

    def _start_ticker(self) -> None:
        if self._ticker is not None:
            return
        self._stop.clear()
        self._ticker = threading.Thread(target=self._tick, name="claimguard-llm-ticker", daemon=True)
        self._ticker.start()

    def _stop_ticker(self) -> None:
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join(timeout=max(self.interval_s, 1.0) + 1.0)
            self._ticker = None

    def emit(self, event: dict[str, object]) -> None:
        et = str(event.get("event", ""))
        if et == "run_start":
            with self._lock:
                self.run_id = str(event.get("run_id", ""))
                self.task_count = int(event.get("task_count", 0))
                self.task_started = 0
                self.task_done = 0
                self._active_tasks = []
                self._map_progress_by_task = {}
                self._task_progress_by_task = {}
            print(json.dumps(event, sort_keys=True), flush=True)
            self._start_ticker()
            return
        if et == "task_start":
            with self._lock:
                self.task_started += 1
                task_name = str(event.get("task", ""))
                if task_name and task_name not in self._active_tasks:
                    self._active_tasks.append(task_name)
            return
        if et == "task_end":
            with self._lock:
                self.task_done += 1
                task_name = str(event.get("task", ""))
                if task_name in self._active_tasks:
                    self._active_tasks.remove(task_name)
                self._map_progress_by_task.pop(task_name, None)
                self._task_progress_by_task.pop(task_name, None)
            return
        if et == "map_progress":
            with self._lock:
                task_name = str(event.get("task", ""))
                if task_name:
                    self._map_progress_by_task[task_name] = {
                        "shard_total": int(event.get("shard_total", 0)),
                        "shard_done": int(event.get("shard_done", 0)),
                        "shard_running": int(event.get("shard_running", 0)),
                    }
            return
        if et == "task_progress":
            with self._lock:
                task_name = str(event.get("task", ""))
                if task_name:
                    progress: dict[str, Any] = {}
                    for key in ("done", "total", "fraction", "phase", "message", "eta_s"):
                        if key in event:
                            progress[key] = event[key]
                    if progress:
                        self._task_progress_by_task[task_name] = progress
            return
        if et == "run_end":
            self._stop_ticker()
            with self._lock:
                if self.run_id:
                    print(json.dumps(self._summary_event(), sort_keys=True), flush=True)
            print(json.dumps(event, sort_keys=True), flush=True)
            return


def _safe_graph_name(raw: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    cleaned = cleaned.strip("._-")
    return cleaned or "claimguard"


def _write_graphviz_png(dot_text: str, *, output_png: Path) -> None:
    dot_executable = shutil.which("dot")
    if not dot_executable:
        raise SystemExit("graphviz `dot` executable not found; install Graphviz or use --graphviz for DOT output")

    output_png = output_png.resolve()
    output_png.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            [dot_executable, "-Tpng", "-o", str(output_png)],
            input=dot_text,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        raise SystemExit("graphviz `dot` executable not found; install Graphviz or use --graphviz for DOT output")

    if proc.returncode != 0:
        err = str(proc.stderr or proc.stdout or "").strip()
        if not err:
            err = f"graphviz dot returned exit code {proc.returncode}"
        raise SystemExit(f"failed to render graphviz PNG: {err}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claimguard")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a contract pipeline")
    run_p.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    run_p.add_argument("--clean-state", action="store_true", help="Remove .claimguard state folder before run")
    run_p.add_argument(
        "--llm-output",
        action="store_true",
        help="Emit NDJSON (`run_start`, `task_summary`, `run_end`)",
    )
    run_p.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Global CPU thread budget for scheduling (default: available CPU cores)",
    )
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
        "--png",
        nargs="?",
        const="",
        default=None,
        help="Render task DAG as PNG (optional path). If omitted, writes <workspace>/.claimguard/graphviz/<pipeline>.png",
    )
    doctor_mode.add_argument(
        "--audit-inputs",
        action="store_true",
        help="List root input files by consuming task (task<TAB>input)",
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
        try:
            if args.llm_output:
                llm = LLMStreamRenderer(interval_s=60.0)
                runner.run(event_emitter=llm.emit, max_workers=args.jobs, targets=list(args.target))
            else:
                human = HumanLiveRenderer()
                try:
                    runner.run(event_emitter=human.emit, max_workers=args.jobs, targets=list(args.target))
                finally:
                    human.close()
        except KeyboardInterrupt:
            return 130
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
        if args.png is not None:
            graph_name = str(runner.contract.get("pipeline_name", "claimguard"))
            default_png = runner.state_root / "graphviz" / f"{_safe_graph_name(graph_name)}.png"
            target = Path(args.png) if str(args.png).strip() else default_png
            dot_source = graphviz_dot(
                runner.task_specs,
                runner.deps,
                graph_name=graph_name,
            )
            _write_graphviz_png(dot_source, output_png=target)
            print(target.resolve())
            return 0
        if args.audit_inputs:
            produced = {out for spec in runner.task_specs.values() for out in spec.outputs}
            rows = sorted(
                (task_name, rel)
                for task_name, spec in runner.task_specs.items()
                for rel in spec.inputs
                if rel not in produced
            )
            for task_name, rel in rows:
                print(f"{task_name}\t{rel}")
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
