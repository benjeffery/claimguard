"""Minimal pipeline runner."""

from __future__ import annotations

import ast
import concurrent.futures
import hashlib
import json
import os
import re
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .contracts import load_json, resolve_workspace_root, write_json
from .discovery import TaskSpec, discover_tasks, infer_dependencies, topological_order
from .gates import evaluate_gate
from .policy import CERTIFIABLE_STATUSES, classify_claim


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(task_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", task_name)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _collect_import_nodes(path: Path) -> list[ast.AST]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"unable to read Python source for dependency hashing: {path}: {e}") from e
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        raise RuntimeError(f"syntax error in Python source used for dependency hashing: {path}: {e}") from e
    nodes: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            nodes.append(node)
    return nodes


def _module_candidate_files(root: Path, dotted_module: str) -> list[Path]:
    parts = [p for p in dotted_module.split(".") if p]
    if not parts:
        return []
    module_path = root.joinpath(*parts)
    return [module_path.with_suffix(".py"), module_path / "__init__.py"]


def _package_init_chain(path: Path, workspace_root: Path) -> list[Path]:
    out: list[Path] = []
    cur = path.parent
    ws = workspace_root.resolve()
    while True:
        if ws not in cur.parents and cur != ws:
            break
        init_py = cur / "__init__.py"
        if init_py.exists():
            out.append(init_py.resolve())
        if cur == ws:
            break
        cur = cur.parent
    return out


def _resolve_local_import_files(
    *,
    importer_path: Path,
    workspace_root: Path,
    node: ast.AST,
) -> list[Path]:
    importer_path = importer_path.resolve()
    workspace_root = workspace_root.resolve()
    roots = [importer_path.parent, workspace_root]
    found: list[Path] = []

    def add_existing(candidates: list[Path]) -> None:
        for c in candidates:
            rc = c.resolve()
            if rc.exists() and rc.is_file():
                found.append(rc)
                found.extend(_package_init_chain(rc, workspace_root))

    if isinstance(node, ast.Import):
        for alias in node.names:
            name = str(alias.name)
            for root in roots:
                add_existing(_module_candidate_files(root, name))
            # `import a.b` first imports package `a`; include it if local.
            head = name.split(".", 1)[0]
            for root in roots:
                add_existing(_module_candidate_files(root, head))
        return found

    if isinstance(node, ast.ImportFrom):
        base = importer_path.parent
        if node.level > 0:
            # from .x import y => level=1 stays at current dir.
            for _ in range(node.level - 1):
                base = base.parent
        module = str(node.module) if node.module else ""
        search_roots = [base] if node.level > 0 else roots
        if module:
            for root in search_roots:
                add_existing(_module_candidate_files(root, module))
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    add_existing(_module_candidate_files(root, f"{module}.{alias.name}"))
        else:
            # from . import helper
            for root in search_roots:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    add_existing(_module_candidate_files(root, alias.name))
        return found

    return found


def _local_import_closure(entry_script: Path, workspace_root: Path) -> list[Path]:
    workspace_root = workspace_root.resolve()
    start = entry_script.resolve()
    todo = [start]
    seen: set[Path] = set()
    ordered: list[Path] = []

    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
        for node in _collect_import_nodes(path):
            for dep in _resolve_local_import_files(importer_path=path, workspace_root=workspace_root, node=node):
                if dep not in seen:
                    todo.append(dep)
    # stable order for hash payload
    return sorted(set(ordered))


def _validate_rel_contract_path(field_name: str, token: str) -> None:
    p = Path(token)
    if p.is_absolute():
        raise RuntimeError(f"`{field_name}` entries must be relative paths: {token!r}")
    if any(part == ".." for part in p.parts):
        raise RuntimeError(f"`{field_name}` entries cannot contain parent traversal: {token!r}")


def _validate_contract(contract: dict[str, Any]) -> None:
    allowed_keys = {"pipeline_name", "task_roots", "task_params", "rng_policy", "rng_seed"}
    unknown = sorted(set(contract.keys()).difference(allowed_keys))
    if unknown:
        raise RuntimeError(f"unsupported contract key(s): {unknown}")

    if "pipeline_name" in contract and not isinstance(contract.get("pipeline_name"), str):
        raise RuntimeError("`pipeline_name` must be a string")

    task_roots = contract.get("task_roots", None)
    if not isinstance(task_roots, list) or not task_roots or not all(
        isinstance(x, str) and str(x).strip() for x in task_roots
    ):
        raise RuntimeError("contract must define non-empty string list `task_roots`")
    for root in task_roots:
        _validate_rel_contract_path("task_roots", str(root))

    if "task_params" in contract:
        task_params = contract.get("task_params")
        if not isinstance(task_params, dict):
            raise RuntimeError("`task_params` must be an object")
        for k, v in task_params.items():
            if not isinstance(k, str) or not k.strip():
                raise RuntimeError("`task_params` keys must be non-empty strings")
            if not isinstance(v, dict):
                raise RuntimeError(f"`task_params[{k}]` must be an object")

    if "rng_policy" in contract:
        policy = str(contract.get("rng_policy", "")).strip().lower()
        if policy not in {"off", "seeded", "strict"}:
            raise RuntimeError(f"unsupported rng_policy: {policy}")
    if "rng_seed" in contract and not isinstance(contract.get("rng_seed"), int):
        raise RuntimeError("`rng_seed` must be an integer")


def _make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    token = secrets.token_hex(4)
    return f"{stamp}_{token}"


def _normalize_path_tokens(tokens: list[str], workspace_root: Path) -> list[Path]:
    ws = workspace_root.resolve()
    out: list[Path] = []
    for token in tokens:
        p = Path(token)
        rp = p.resolve() if p.is_absolute() else (ws / token).resolve()
        out.append(rp)
    return out


def _path_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _proc_rss_bytes(pid: int) -> int:
    if pid <= 0:
        return 0
    status_path = Path("/proc") / str(pid) / "status"
    try:
        text = status_path.read_text(encoding="utf-8")
    except Exception:
        return 0
    for line in text.splitlines():
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return 0
        try:
            return max(int(parts[1]), 0) * 1024
        except Exception:
            return 0
    return 0


def _normalize_task_progress_payload(obj: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    done: int | None = None
    total: int | None = None

    if "done" in obj:
        try:
            done = max(int(obj.get("done", 0)), 0)
            out["done"] = done
        except Exception:
            done = None
    if "total" in obj:
        try:
            total = max(int(obj.get("total", 0)), 0)
            out["total"] = total
        except Exception:
            total = None
    if done is not None and total is not None and total > 0:
        out["fraction"] = min(max(float(done) / float(total), 0.0), 1.0)

    if "fraction" in obj:
        try:
            out["fraction"] = min(max(float(obj.get("fraction", 0.0)), 0.0), 1.0)
        except Exception:
            pass

    if "message" in obj:
        msg = str(obj.get("message", "")).strip()
        if msg:
            out["message"] = msg
    if "phase" in obj:
        phase = str(obj.get("phase", "")).strip()
        if phase:
            out["phase"] = phase
    if "eta_s" in obj:
        try:
            eta_s = max(float(obj.get("eta_s", 0.0)), 0.0)
            out["eta_s"] = eta_s
        except Exception:
            pass
    if "meta" in obj:
        meta = obj.get("meta")
        try:
            json.dumps(meta)
            out["meta"] = meta
        except Exception:
            pass

    return out


def _dependency_fingerprint(workspace_root: Path) -> dict[str, str]:
    ws = workspace_root.resolve()
    candidates = [
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "poetry.lock",
        "Pipfile.lock",
        "environment.yml",
        "conda-lock.yml",
    ]
    out: dict[str, str] = {}
    for rel in candidates:
        p = (ws / rel).resolve()
        if p.exists() and p.is_file():
            out[rel] = _sha256(p)
    out["_python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return out


def _copy_rel_if_exists(src_root: Path, dst_root: Path, rel: str) -> None:
    src = (src_root / rel).resolve()
    if not src.exists():
        return
    dst = (dst_root / rel).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        raise RuntimeError(f"directory artifacts are not supported: {rel!r}")
    shutil.copy2(src, dst)


def _get_dotted(obj: dict[str, Any], dotted: str) -> Any:
    cur: Any = obj
    if not dotted:
        return cur
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


def _short_item_hash(item: Any) -> str:
    raw = json.dumps(item, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _expand_map_token(token: str, *, map_index: int, map_key: str, map_hash: str) -> str:
    return (
        token.replace("{map_index}", str(map_index))
        .replace("{map_key}", map_key)
        .replace("{map_hash}", map_hash)
    )


def _task_seed(base_seed: int, task_name: str) -> int:
    h = hashlib.sha256(task_name.encode("utf-8")).hexdigest()[:8]
    return (int(base_seed) + int(h, 16)) % (2**32)


def _dependency_closure(targets: list[str], deps: dict[str, list[str]]) -> set[str]:
    out: set[str] = set()
    stack = list(targets)
    while stack:
        t = stack.pop()
        if t in out:
            continue
        out.add(t)
        stack.extend(list(deps.get(t, [])))
    return out


@dataclass
class TaskRow:
    task: str
    status: str
    cache_hit: bool
    cache_reason: str
    blocked_reason: str
    gate_rows: list[dict[str, Any]]
    runtime_seconds: float
    inputs_hashes: dict[str, str]
    output_hashes: dict[str, str]
    cpu_threads_alloc: int = 1
    cpu_affinity: list[int] | None = None


class PipelineRunner:
    def __init__(self, contract_path: Path) -> None:
        self.contract_path = contract_path.resolve()
        self.contract = load_json(self.contract_path)
        _validate_contract(self.contract)
        self.workspace_root = resolve_workspace_root(self.contract_path, self.contract)
        task_roots = [str(x) for x in self.contract.get("task_roots", [])]
        self.task_specs = discover_tasks(
            workspace_root=self.workspace_root,
            task_roots=task_roots,
            task_params={str(k): dict(v) for k, v in dict(self.contract.get("task_params", {})).items()},
        )
        if not self.task_specs:
            raise RuntimeError("no tasks discovered under configured task_roots")
        self.deps = infer_dependencies(self.task_specs)
        self.order = topological_order(self.task_specs, self.deps)
        self._map_output_template_to_task: dict[str, str] = {}
        for name, spec in sorted(self.task_specs.items()):
            if spec.map_config is None:
                continue
            for out_rel in spec.outputs:
                self._map_output_template_to_task[out_rel] = name

        self.state_root = self.workspace_root / ".claimguard"
        self.cache_root = self.state_root / "cache"
        self.report_root = self.state_root / "reports"
        self.run_root = self.state_root / "runs"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.report_root.mkdir(parents=True, exist_ok=True)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._rng_policy = "off"
        self._rng_seed_base = 0
        self._cancel_event = threading.Event()
        self._active_proc_lock = threading.Lock()
        self._active_procs: dict[int, tuple[subprocess.Popen[str], str, float]] = {}

    def _register_active_process(self, proc: subprocess.Popen[str], *, task_name: str) -> None:
        if proc.pid is None:
            return
        with self._active_proc_lock:
            self._active_procs[int(proc.pid)] = (proc, str(task_name), float(time.perf_counter()))

    def _unregister_active_process(self, proc: subprocess.Popen[str]) -> None:
        if proc.pid is None:
            return
        with self._active_proc_lock:
            self._active_procs.pop(int(proc.pid), None)

    def _terminate_process(self, proc: subprocess.Popen[str], *, grace_s: float = 1.0) -> None:
        if proc.poll() is not None:
            return
        pid = int(proc.pid or 0)
        sent_term = False
        if pid > 0:
            try:
                os.killpg(pid, signal.SIGTERM)
                sent_term = True
            except ProcessLookupError:
                return
            except Exception:
                sent_term = False
        if not sent_term:
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            except Exception:
                pass
        try:
            proc.wait(timeout=max(float(grace_s), 0.1))
            return
        except subprocess.TimeoutExpired:
            pass
        sent_kill = False
        if pid > 0:
            try:
                os.killpg(pid, signal.SIGKILL)
                sent_kill = True
            except ProcessLookupError:
                return
            except Exception:
                sent_kill = False
        if sent_kill:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        except Exception:
            pass

    def _request_cancel(self) -> None:
        self._cancel_event.set()
        with self._active_proc_lock:
            running = [v[0] for v in self._active_procs.values()]
        for proc in running:
            self._terminate_process(proc)

    def _active_task_stats(self) -> dict[str, dict[str, float | int]]:
        with self._active_proc_lock:
            entries = list(self._active_procs.values())
        now = float(time.perf_counter())
        by_task: dict[str, dict[str, float | int]] = {}
        for proc, task_name, started_t in entries:
            if proc.poll() is not None:
                continue
            pid = int(proc.pid or 0)
            rss = _proc_rss_bytes(pid)
            if task_name not in by_task:
                by_task[task_name] = {
                    "runtime_s": max(now - float(started_t), 0.0),
                    "rss_bytes": int(rss),
                    "_started_t": float(started_t),
                }
                continue
            slot = by_task[task_name]
            slot["rss_bytes"] = int(slot.get("rss_bytes", 0)) + int(rss)
            prev_started = float(slot.get("_started_t", started_t))
            if float(started_t) < prev_started:
                slot["_started_t"] = float(started_t)
                slot["runtime_s"] = max(now - float(started_t), 0.0)
        out: dict[str, dict[str, float | int]] = {}
        for task_name, vals in by_task.items():
            out[task_name] = {
                "runtime_s": float(vals.get("runtime_s", 0.0)),
                "rss_bytes": int(vals.get("rss_bytes", 0)),
            }
        return out

    def _task_cpu_limits(self, spec: TaskSpec) -> tuple[int, int, int | None]:
        resources = dict(getattr(spec, "resources", {}) or {})
        min_threads = max(int(resources.get("cpu_threads_min", 1)), 1)
        pref_threads = max(int(resources.get("cpu_threads_pref", min_threads)), min_threads)
        max_raw = resources.get("cpu_threads_max", pref_threads)
        max_threads = max(int(max_raw), pref_threads) if max_raw is not None else None
        return min_threads, pref_threads, max_threads

    def _available_cpu_ids(self) -> list[int]:
        try:
            return sorted(int(x) for x in os.sched_getaffinity(0))
        except Exception:
            count = int(os.cpu_count() or 1)
            return list(range(max(count, 1)))

    def _critical_path_scores(self, selected_tasks: set[str]) -> dict[str, int]:
        reverse_deps: dict[str, list[str]] = {t: [] for t in selected_tasks}
        for t in selected_tasks:
            for dep in self.deps.get(t, []):
                if dep in selected_tasks:
                    reverse_deps.setdefault(dep, []).append(t)
        memo: dict[str, int] = {}

        def score(task: str) -> int:
            if task in memo:
                return memo[task]
            kids = [k for k in reverse_deps.get(task, []) if k in selected_tasks]
            if not kids:
                memo[task] = 1
            else:
                memo[task] = 1 + max(score(k) for k in kids)
            return memo[task]

        for task in selected_tasks:
            score(task)
        return memo

    def _expand_map_inputs(self, spec: TaskSpec, *, strict: bool) -> list[str]:
        out: list[str] = []
        for rel in spec.inputs:
            if "{map_index}" not in rel and "{map_key}" not in rel and "{map_hash}" not in rel:
                out.append(rel)
                continue

            producer = self._map_output_template_to_task.get(rel)
            if not producer:
                if strict:
                    raise RuntimeError(f"task `{spec.name}` input uses map tokens but no map producer found: {rel!r}")
                out.append(rel)
                continue

            map_spec = self.task_specs[producer]
            m = map_spec.map_config or {}
            items_input = str(m.get("items_input", ""))
            items_path = str(m.get("items_path", ""))
            item_name_field = str(m.get("item_name_field", "")).strip()
            items_file = (self.workspace_root / items_input).resolve()
            if not items_file.exists():
                if strict:
                    raise RuntimeError(f"map items_input missing for map task `{producer}`: {items_input!r}")
                out.append(rel)
                continue

            try:
                root_obj = _read_json(items_file)
                raw_items = _get_dotted(root_obj, items_path) if items_path else root_obj
                if not isinstance(raw_items, list):
                    raise RuntimeError("items_not_list")
                items: list[Any] = list(raw_items)
            except Exception as e:
                if strict:
                    raise RuntimeError(f"failed to load map items for map task `{producer}`") from e
                out.append(rel)
                continue

            for idx, item in enumerate(items):
                item_hash = _short_item_hash(item)
                if item_name_field and isinstance(item, dict) and item_name_field in item:
                    map_key_raw = str(item[item_name_field])
                else:
                    map_key_raw = str(idx)
                map_key = _safe_name(map_key_raw)
                out.append(_expand_map_token(rel, map_index=idx, map_key=map_key, map_hash=item_hash))

        # stable order + de-dupe
        return list(dict.fromkeys(out))

    def _rng_env_for(self, spec: TaskSpec) -> dict[str, str]:
        policy = str(self._rng_policy)
        if policy == "off":
            return {}
        seed = _task_seed(self._rng_seed_base, spec.name)
        return {
            "CG_RNG_POLICY": policy,
            "CG_SEED": str(seed),
            "CG_ALLOW_RNG": "1" if spec.allow_rng else "0",
        }

    def _compute_input_hashes(self, spec: TaskSpec) -> dict[str, str]:
        expanded_inputs = self._expand_map_inputs(spec, strict=False)
        out: dict[str, str] = {}
        for rel in expanded_inputs:
            p = (self.workspace_root / rel).resolve()
            if not p.exists():
                out[rel] = "MISSING"
            elif p.is_dir():
                out[rel] = "DIRECTORY"
            else:
                out[rel] = _sha256(p)
        return out

    def _compute_output_hashes(self, spec: TaskSpec) -> dict[str, str]:
        out: dict[str, str] = {}
        for rel in spec.outputs:
            p = (self.workspace_root / rel).resolve()
            if not p.exists():
                out[rel] = "MISSING"
            elif p.is_dir():
                out[rel] = "DIRECTORY"
            else:
                out[rel] = _sha256(p)
        return out

    def _cache_file(self, task_name: str) -> Path:
        return self.cache_root / f"{_safe_name(task_name)}.json"

    def _map_outputs_manifest_file(self, task_name: str) -> Path:
        return self.cache_root / f"{_safe_name(task_name)}.map_outputs.json"

    def _read_map_outputs_manifest(self, task_name: str) -> list[str]:
        mf = self._map_outputs_manifest_file(task_name)
        if not mf.exists():
            return []
        obj = _read_json(mf)
        if not isinstance(obj, dict):
            raise RuntimeError(f"invalid map outputs manifest (expected object): {mf}")
        outputs = obj.get("outputs", [])
        if not isinstance(outputs, list) or not all(isinstance(x, str) for x in outputs):
            raise RuntimeError(f"invalid map outputs manifest outputs field: {mf}")
        return list(dict.fromkeys(str(x) for x in outputs))

    def _write_map_outputs_manifest(self, task_name: str, outputs: list[str]) -> None:
        write_json(
            self._map_outputs_manifest_file(task_name),
            {
                "task": task_name,
                "updated_utc": _utc_now(),
                "outputs": sorted(list(dict.fromkeys(outputs))),
            },
        )

    def _mark_map_outputs_stale(self, task_name: str, current_outputs: list[str]) -> None:
        previous = set(self._read_map_outputs_manifest(task_name))
        current = set(current_outputs)
        stale = sorted(previous.difference(current))
        ws = self.workspace_root.resolve()
        for rel in stale:
            src = (ws / rel).resolve()
            if not _path_under(src, ws):
                raise RuntimeError(f"map stale output escapes workspace: {rel!r}")
            if not src.exists():
                continue
            if src.is_dir():
                raise RuntimeError(f"directory map outputs are not supported: {rel!r}")
            dst = src.with_name(f"{src.name}.stale")
            if dst.exists():
                idx = 1
                while True:
                    candidate = src.with_name(f"{src.name}.stale.{idx}")
                    if not candidate.exists():
                        dst = candidate
                        break
                    idx += 1
            src.replace(dst)
        self._write_map_outputs_manifest(task_name, sorted(current))

    def _cache_key(self, spec: TaskSpec, input_hashes: dict[str, str]) -> str:
        code_files = _local_import_closure(spec.script_path, self.workspace_root)
        code_hashes = {
            str(p.resolve().relative_to(self.workspace_root.resolve())): _sha256(p) for p in code_files if p.exists()
        }
        payload = {
            "task": spec.name,
            "inputs": input_hashes,
            "code_hashes": code_hashes,
            "dependency_fingerprint": _dependency_fingerprint(self.workspace_root),
            "params": spec.params,
            "claimguard_version": __version__,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _cache_hit_reason(self, spec: TaskSpec, cache_key: str, output_hashes: dict[str, str]) -> tuple[bool, str]:
        cf = self._cache_file(spec.name)
        if not cf.exists():
            return False, "no_cache_record"
        obj = _read_json(cf)
        if not isinstance(obj, dict):
            raise RuntimeError(f"invalid cache record (expected object): {cf}")
        if str(obj.get("cache_key", "")) != cache_key:
            return False, "cache_key_changed"
        if any(v == "MISSING" for v in output_hashes.values()):
            return False, "output_missing"
        if dict(obj.get("output_hashes", {})) != output_hashes:
            return False, "output_hash_changed"
        if str(obj.get("status", "")) not in {"ok", "diagnostic_only"}:
            return False, "cache_status_not_replayable"
        return True, "cache_hit"

    def _write_cache(self, spec: TaskSpec, *, cache_key: str, status: str, output_hashes: dict[str, str]) -> None:
        write_json(
            self._cache_file(spec.name),
            {
                "task": spec.name,
                "updated_utc": _utc_now(),
                "cache_key": cache_key,
                "status": status,
                "output_hashes": output_hashes,
            },
        )

    def _prepare_stage_workspace(self, spec: TaskSpec, stage_root: Path) -> None:
        stage_root.mkdir(parents=True, exist_ok=True)
        # Inputs and declared read exemptions are materialized into staging.
        expanded_inputs = self._expand_map_inputs(spec, strict=False)
        for rel in list(expanded_inputs) + list(spec.read_exemptions):
            _copy_rel_if_exists(self.workspace_root, stage_root, rel)

    def _promote_stage_outputs(self, spec: TaskSpec, stage_root: Path, *, run_id: str) -> None:
        staged: list[tuple[Path, Path, Path]] = []
        for rel in spec.outputs:
            src = (stage_root / rel).resolve()
            if not src.exists():
                raise RuntimeError(f"missing staged output for promotion: {rel}")
            if src.is_dir():
                raise RuntimeError(f"directory outputs are not supported: {rel!r}")
            dst = (self.workspace_root / rel).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f"{dst.name}.cgpromote.{run_id}.tmp")
            if tmp.exists():
                if tmp.is_dir():
                    shutil.rmtree(tmp)
                else:
                    tmp.unlink()
            shutil.copy2(src, tmp)
            staged.append((src, dst, tmp))

        try:
            for _, dst, tmp in staged:
                if dst.exists():
                    if dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                tmp.replace(dst)
        finally:
            for _, _, tmp in staged:
                if tmp.exists():
                    if tmp.is_dir():
                        shutil.rmtree(tmp, ignore_errors=True)
                    else:
                        tmp.unlink(missing_ok=True)

    def _run_task(
        self,
        spec: TaskSpec,
        *,
        run_id: str,
        extra_env: dict[str, str] | None = None,
        top_task: str | None = None,
        progress_emitter: Callable[[dict[str, Any]], None] | None = None,
        cpu_threads_alloc: int = 1,
        cpu_affinity: list[int] | None = None,
    ) -> TaskRow:
        t0 = time.perf_counter()
        alloc_threads = max(int(cpu_threads_alloc), 1)
        affinity_ids = sorted({int(x) for x in (cpu_affinity or [])}) if cpu_affinity else None
        input_hashes = self._compute_input_hashes(spec)
        output_hashes = self._compute_output_hashes(spec)
        if any(v == "DIRECTORY" for v in input_hashes.values()):
            return TaskRow(
                task=spec.name,
                status="blocked",
                cache_hit=False,
                cache_reason="directory_input",
                blocked_reason="directory_input",
                gate_rows=[],
                runtime_seconds=float(time.perf_counter() - t0),
                inputs_hashes=input_hashes,
                output_hashes=output_hashes,
                cpu_threads_alloc=alloc_threads,
                cpu_affinity=affinity_ids,
            )
        if any(v == "DIRECTORY" for v in output_hashes.values()):
            return TaskRow(
                task=spec.name,
                status="blocked",
                cache_hit=False,
                cache_reason="directory_output",
                blocked_reason="directory_output",
                gate_rows=[],
                runtime_seconds=float(time.perf_counter() - t0),
                inputs_hashes=input_hashes,
                output_hashes=output_hashes,
                cpu_threads_alloc=alloc_threads,
                cpu_affinity=affinity_ids,
            )
        if any(
            (self.workspace_root / rel).resolve().exists() and (self.workspace_root / rel).resolve().is_dir()
            for rel in list(spec.read_exemptions) + list(spec.write_exemptions)
        ):
            return TaskRow(
                task=spec.name,
                status="blocked",
                cache_hit=False,
                cache_reason="directory_exemption",
                blocked_reason="directory_exemption",
                gate_rows=[],
                runtime_seconds=float(time.perf_counter() - t0),
                inputs_hashes=input_hashes,
                output_hashes=output_hashes,
                cpu_threads_alloc=alloc_threads,
                cpu_affinity=affinity_ids,
            )
        cache_key = self._cache_key(spec, input_hashes)
        cache_hit, cache_reason = self._cache_hit_reason(spec, cache_key, output_hashes)
        if cache_hit:
            return TaskRow(
                task=spec.name,
                status="replay_ok",
                cache_hit=True,
                cache_reason=cache_reason,
                blocked_reason="",
                gate_rows=[],
                runtime_seconds=float(time.perf_counter() - t0),
                inputs_hashes=input_hashes,
                output_hashes=output_hashes,
                cpu_threads_alloc=alloc_threads,
                cpu_affinity=affinity_ids,
            )
        if self._cancel_event.is_set():
            return TaskRow(
                task=spec.name,
                status="blocked",
                cache_hit=False,
                cache_reason="cancelled",
                blocked_reason="cancelled",
                gate_rows=[],
                runtime_seconds=float(time.perf_counter() - t0),
                inputs_hashes=input_hashes,
                output_hashes=output_hashes,
                cpu_threads_alloc=alloc_threads,
                cpu_affinity=affinity_ids,
            )

        run_dir = self.run_root / run_id / _safe_name(spec.name)
        stage_root = run_dir / "staging" / "workspace"
        tmp_dir = run_dir / "tmp"
        run_dir.mkdir(parents=True, exist_ok=True)
        self._prepare_stage_workspace(spec, stage_root)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        params_json = json.dumps(spec.params, sort_keys=True)
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parent.parent)
        py_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = package_root + (os.pathsep + py_path if py_path else "")

        implicit_read_paths = _local_import_closure(spec.script_path, self.workspace_root)
        expanded_inputs = self._expand_map_inputs(spec, strict=False)
        declared_read_tokens = list(expanded_inputs) + list(spec.outputs) + [spec.interface_output] + list(spec.read_exemptions)
        declared_write_tokens = list(spec.outputs) + list(spec.write_exemptions)
        read_paths = _normalize_path_tokens(declared_read_tokens, stage_root)
        read_paths.extend(implicit_read_paths)
        write_paths = _normalize_path_tokens(declared_write_tokens, stage_root)
        read_paths.append(tmp_dir.resolve())
        write_paths.append(tmp_dir.resolve())
        allowed_reads_json = json.dumps(sorted({str(p.resolve()) for p in read_paths}), sort_keys=True)
        allowed_writes_json = json.dumps(sorted({str(p.resolve()) for p in write_paths}), sort_keys=True)

        env.update(
            {
                "CG_WORKSPACE_ROOT": str(self.workspace_root.resolve()),
                "CG_TASK_NAME": spec.name,
                "CG_TASK_PARAMS_JSON": params_json,
                "CG_CPU_THREADS": str(alloc_threads),
                "CG_PROGRESS_MIN_INTERVAL_S": "0.2",
                "CG_ENFORCE_IO": "1",
                "CG_ALLOWED_READS_JSON": allowed_reads_json,
                "CG_ALLOWED_WRITES_JSON": allowed_writes_json,
                "CG_ALLOW_SUBPROCESS": "1" if spec.allow_subprocess else "0",
                "CG_TMPDIR": str(tmp_dir.resolve()),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        if affinity_ids:
            env["CG_CPU_AFFINITY"] = ",".join(str(x) for x in affinity_ids)
        env.update(self._rng_env_for(spec))
        if extra_env:
            env.update({str(k): str(v) for k, v in extra_env.items()})
        progress_read_fd: int | None = None
        progress_write_fd: int | None = None
        progress_buffer = ""
        progress_pending: dict[str, Any] | None = None
        last_progress_emit_t = 0.0
        progress_emit_interval_s = 0.2
        if progress_emitter is not None:
            progress_read_fd, progress_write_fd = os.pipe()
            os.set_blocking(progress_read_fd, False)
            env["CG_PROGRESS_FD"] = str(progress_write_fd)

        def maybe_emit_progress(force: bool = False) -> None:
            nonlocal progress_pending, last_progress_emit_t
            if progress_pending is None or progress_emitter is None:
                return
            now = float(time.perf_counter())
            if not force and (now - last_progress_emit_t) < progress_emit_interval_s:
                return
            event: dict[str, Any] = {
                "event": "task_progress",
                "run_id": run_id,
                "task": top_task or spec.name,
            }
            if top_task and top_task != spec.name:
                event["source_task"] = spec.name
            event.update(progress_pending)
            progress_emitter(event)
            progress_pending = None
            last_progress_emit_t = now

        def drain_progress_frames(force: bool = False) -> None:
            nonlocal progress_buffer, progress_pending
            if progress_read_fd is None:
                return
            while True:
                try:
                    chunk = os.read(progress_read_fd, 16384)
                except BlockingIOError:
                    break
                except Exception:
                    break
                if not chunk:
                    break
                progress_buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in progress_buffer:
                line, progress_buffer = progress_buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                normalized = _normalize_task_progress_payload(obj)
                if normalized:
                    progress_pending = normalized
            if force:
                tail = progress_buffer.strip()
                progress_buffer = ""
                if tail:
                    try:
                        obj = json.loads(tail)
                    except Exception:
                        obj = None
                    if isinstance(obj, dict):
                        normalized = _normalize_task_progress_payload(obj)
                        if normalized:
                            progress_pending = normalized
            maybe_emit_progress(force=force)

        popen_kwargs: dict[str, Any] = {}
        if progress_write_fd is not None:
            popen_kwargs["pass_fds"] = (progress_write_fd,)
        proc = subprocess.Popen(
            [sys.executable, "-m", "claimguard.worker", "--script", str(spec.script_path.resolve())],
            cwd=str(stage_root.resolve()),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            **popen_kwargs,
        )
        if progress_write_fd is not None:
            try:
                os.close(progress_write_fd)
            except Exception:
                pass
            progress_write_fd = None
        self._register_active_process(proc, task_name=top_task or spec.name)
        stdout = ""
        stderr = ""
        try:
            while True:
                try:
                    out_text, err_text = proc.communicate(timeout=0.2)
                    stdout = out_text or ""
                    stderr = err_text or ""
                    drain_progress_frames(force=True)
                    break
                except subprocess.TimeoutExpired:
                    drain_progress_frames(force=False)
                    if self._cancel_event.is_set():
                        self._terminate_process(proc)
                        continue
        finally:
            self._unregister_active_process(proc)
            if progress_write_fd is not None:
                try:
                    os.close(progress_write_fd)
                except Exception:
                    pass
            if progress_read_fd is not None:
                try:
                    os.close(progress_read_fd)
                except Exception:
                    pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
        write_json(
            run_dir / "process.json",
            {
                "task": spec.name,
                "returncode": int(proc.returncode),
                "stdout_tail": stdout[-2000:],
                "stderr_tail": stderr[-2000:],
            },
        )

        status = "ok"
        blocked_reason = ""
        gate_rows: list[dict[str, Any]] = []

        if proc.returncode != 0:
            status = "blocked"
            blocked_reason = f"nonzero_exit:{proc.returncode}"
        else:
            stage_output_hashes = {}
            for rel in spec.outputs:
                p = (stage_root / rel).resolve()
                if not p.exists():
                    stage_output_hashes[rel] = "MISSING"
                elif p.is_dir():
                    stage_output_hashes[rel] = "DIRECTORY"
                else:
                    stage_output_hashes[rel] = _sha256(p)
            if any(v == "MISSING" for v in stage_output_hashes.values()):
                status = "blocked"
                blocked_reason = "missing_output"
            elif any(v == "DIRECTORY" for v in stage_output_hashes.values()):
                status = "blocked"
                blocked_reason = "directory_output"
            else:
                iface_path = (stage_root / spec.interface_output).resolve()
                if not iface_path.exists():
                    status = "blocked"
                    blocked_reason = "missing_interface_output"
                elif iface_path.is_dir():
                    status = "blocked"
                    blocked_reason = "directory_interface_output"
                else:
                    interface_obj = _read_json(iface_path)
                    if not isinstance(interface_obj, dict):
                        status = "blocked"
                        blocked_reason = "invalid_interface_json_object"
                    else:
                        gate_rows = [evaluate_gate(interface_obj, g) for g in spec.gates]
                        if not all(bool(g["pass"]) for g in gate_rows):
                            status = "blocked"
                            blocked_reason = "gate_failure"
                        else:
                            iface_status = str(interface_obj.get("status", "ok"))
                            if iface_status == "ok":
                                status = "ok"
                            elif iface_status == "diagnostic_only":
                                status = "diagnostic_only"
                            else:
                                status = "blocked"
                                blocked_reason = f"unsupported_interface_status:{iface_status}"
                    if status in {"ok", "diagnostic_only"}:
                        self._promote_stage_outputs(spec, stage_root, run_id=run_id)

        output_hashes = self._compute_output_hashes(spec)
        if status in {"ok", "diagnostic_only"}:
            self._write_cache(spec, cache_key=cache_key, status=status, output_hashes=output_hashes)

        return TaskRow(
            task=spec.name,
            status=status,
            cache_hit=False,
            cache_reason=cache_reason,
            blocked_reason=blocked_reason,
            gate_rows=gate_rows,
            runtime_seconds=float(time.perf_counter() - t0),
            inputs_hashes=input_hashes,
            output_hashes=output_hashes,
            cpu_threads_alloc=alloc_threads,
            cpu_affinity=affinity_ids,
        )

    def _run_map_task(
        self,
        spec: TaskSpec,
        *,
        run_id: str,
        max_workers: int,
        progress_emitter: Callable[[dict[str, Any]], None] | None = None,
        cpu_threads_alloc: int = 1,
        cpu_affinity: list[int] | None = None,
    ) -> TaskRow:
        t0 = time.perf_counter()
        alloc_threads = max(int(cpu_threads_alloc), 1)
        affinity_ids = sorted({int(x) for x in (cpu_affinity or [])}) if cpu_affinity else None
        m = spec.map_config or {}
        items_input = str(m.get("items_input", ""))
        items_path = str(m.get("items_path", ""))
        item_name_field = str(m.get("item_name_field", "")).strip()
        allow_empty = bool(m.get("allow_empty", False))

        input_hashes = self._compute_input_hashes(spec)
        items_file = (self.workspace_root / items_input).resolve()
        if not items_file.exists():
            return TaskRow(
                task=spec.name,
                status="blocked",
                cache_hit=False,
                cache_reason="map_invalid_items",
                blocked_reason="map_items_input_missing",
                gate_rows=[],
                runtime_seconds=float(time.perf_counter() - t0),
                inputs_hashes=input_hashes,
                output_hashes={},
                cpu_threads_alloc=alloc_threads,
                cpu_affinity=affinity_ids,
            )
        try:
            root_obj = _read_json(items_file)
            raw_items = _get_dotted(root_obj, items_path) if items_path else root_obj
            if not isinstance(raw_items, list):
                raise RuntimeError("items_not_list")
            items: list[Any] = list(raw_items)
        except Exception:
            return TaskRow(
                task=spec.name,
                status="blocked",
                cache_hit=False,
                cache_reason="map_invalid_items",
                blocked_reason="map_items_parse_error",
                gate_rows=[],
                runtime_seconds=float(time.perf_counter() - t0),
                inputs_hashes=input_hashes,
                output_hashes={},
                cpu_threads_alloc=alloc_threads,
                cpu_affinity=affinity_ids,
            )

        if not items:
            status = "diagnostic_only" if allow_empty else "blocked"
            cache_hit = allow_empty
            cache_reason = "map_empty_allowed" if allow_empty else "map_empty"
            blocked_reason = "" if allow_empty else "map_empty"
            if allow_empty:
                try:
                    self._mark_map_outputs_stale(spec.name, current_outputs=[])
                except Exception:
                    status = "blocked"
                    cache_hit = False
                    cache_reason = "map_stale_mark_failed"
                    blocked_reason = "map_stale_mark_failed"
            return TaskRow(
                task=spec.name,
                status=status,
                cache_hit=cache_hit,
                cache_reason=cache_reason,
                blocked_reason=blocked_reason,
                gate_rows=[],
                runtime_seconds=float(time.perf_counter() - t0),
                inputs_hashes=input_hashes,
                output_hashes={},
                cpu_threads_alloc=alloc_threads,
                cpu_affinity=affinity_ids,
            )

        shard_specs: list[tuple[TaskSpec, dict[str, str]]] = []
        for idx, item in enumerate(items):
            item_hash = _short_item_hash(item)
            if item_name_field and isinstance(item, dict) and item_name_field in item:
                map_key_raw = str(item[item_name_field])
            else:
                map_key_raw = str(idx)
            map_key = _safe_name(map_key_raw)
            shard_name = f"{spec.name}[{idx}:{map_key}]"
            shard = TaskSpec(
                name=shard_name,
                script_rel=spec.script_rel,
                script_path=spec.script_path,
                inputs=tuple(
                    _expand_map_token(x, map_index=idx, map_key=map_key, map_hash=item_hash) for x in spec.inputs
                ),
                outputs=tuple(
                    _expand_map_token(x, map_index=idx, map_key=map_key, map_hash=item_hash) for x in spec.outputs
                ),
                read_exemptions=tuple(
                    _expand_map_token(x, map_index=idx, map_key=map_key, map_hash=item_hash)
                    for x in spec.read_exemptions
                ),
                write_exemptions=tuple(
                    _expand_map_token(x, map_index=idx, map_key=map_key, map_hash=item_hash)
                    for x in spec.write_exemptions
                ),
                interface_output=_expand_map_token(
                    spec.interface_output, map_index=idx, map_key=map_key, map_hash=item_hash
                ),
                gates=spec.gates,
                claim_blocking=False,
                allow_subprocess=spec.allow_subprocess,
                allow_rng=spec.allow_rng,
                map_config=None,
                resources={"cpu_threads_min": 1, "cpu_threads_pref": 1, "cpu_threads_max": 1, "memory_gb_max": None},
                params=spec.params,
            )
            shard_env = {
                "CG_MAP_PARENT_TASK": spec.name,
                "CG_MAP_INDEX": str(idx),
                "CG_MAP_KEY": map_key,
                "CG_MAP_HASH": item_hash,
                "CG_MAP_ITEM_JSON": json.dumps(item, sort_keys=True),
            }
            shard_specs.append((shard, shard_env))

        shard_rows: list[TaskRow] = []
        shard_workers = max(1, min(int(max_workers), alloc_threads))
        if progress_emitter is not None:
            progress_emitter(
                {
                    "event": "map_progress",
                    "run_id": run_id,
                    "task": spec.name,
                    "shard_total": len(shard_specs),
                    "shard_done": 0,
                    "shard_running": 0,
                }
            )
            progress_emitter(
                {
                    "event": "task_stats",
                    "run_id": run_id,
                    "tasks": self._active_task_stats(),
                }
            )
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=min(shard_workers, len(shard_specs)))
        futs: list[concurrent.futures.Future[TaskRow]] = []
        try:
            shard_affinity_pool = list(affinity_ids or [])
            if not shard_affinity_pool:
                shard_affinity_pool = self._available_cpu_ids()[: max(shard_workers, 1)]
            futs = [
                ex.submit(
                    self._run_task,
                    shard,
                    run_id=run_id,
                    extra_env=env,
                    top_task=spec.name,
                    progress_emitter=progress_emitter,
                    cpu_threads_alloc=1,
                    cpu_affinity=[shard_affinity_pool[idx % len(shard_affinity_pool)]],
                )
                for idx, (shard, env) in enumerate(shard_specs)
            ]
            pending = set(futs)
            while pending:
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=0.2,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    if progress_emitter is not None:
                        progress_emitter(
                            {
                                "event": "task_stats",
                                "run_id": run_id,
                                "tasks": self._active_task_stats(),
                            }
                        )
                    continue
                for fut in done:
                    shard_rows.append(fut.result())
                    if progress_emitter is not None:
                        done_count = len(shard_rows)
                        running_count = sum(1 for f in pending if f.running())
                        progress_emitter(
                            {
                                "event": "map_progress",
                                "run_id": run_id,
                                "task": spec.name,
                                "shard_total": len(shard_specs),
                                "shard_done": done_count,
                                "shard_running": running_count,
                            }
                        )
                        progress_emitter(
                            {
                                "event": "task_stats",
                                "run_id": run_id,
                                "tasks": self._active_task_stats(),
                            }
                        )
        except KeyboardInterrupt:
            self._request_cancel()
            ex.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            ex.shutdown(wait=True, cancel_futures=True)

        shard_rows = sorted(shard_rows, key=lambda r: r.task)
        if any(r.status == "blocked" for r in shard_rows):
            status = "blocked"
            blocked = sum(1 for r in shard_rows if r.status == "blocked")
            blocked_reason = f"map_shard_blocked:{blocked}/{len(shard_rows)}"
        else:
            blocked_reason = ""
            if all(r.status == "replay_ok" for r in shard_rows):
                status = "replay_ok"
            elif all(r.status in {"diagnostic_only", "replay_ok"} for r in shard_rows):
                status = "diagnostic_only"
            else:
                status = "ok"

        cache_hit = all(r.cache_hit for r in shard_rows)
        cache_reason = "map_all_cache_hit" if cache_hit else "map_executed"
        current_outputs = sorted({rel for shard, _ in shard_specs for rel in shard.outputs})
        if status in {"ok", "replay_ok", "diagnostic_only"}:
            try:
                self._mark_map_outputs_stale(spec.name, current_outputs=current_outputs)
            except Exception:
                status = "blocked"
                cache_hit = False
                cache_reason = "map_stale_mark_failed"
                blocked_reason = "map_stale_mark_failed"
        combined_output_hashes: dict[str, str] = {}
        for row in shard_rows:
            for rel, h in row.output_hashes.items():
                combined_output_hashes[f"{row.task}:{rel}"] = h

        return TaskRow(
            task=spec.name,
            status=status,
            cache_hit=cache_hit,
            cache_reason=cache_reason,
            blocked_reason=blocked_reason,
            gate_rows=[],
            runtime_seconds=float(time.perf_counter() - t0),
            inputs_hashes=input_hashes,
            output_hashes=combined_output_hashes,
            cpu_threads_alloc=alloc_threads,
            cpu_affinity=affinity_ids,
        )

    def run(
        self,
        *,
        event_emitter: Callable[[dict[str, Any]], None] | None = None,
        max_workers: int | None = None,
        targets: list[str] | None = None,
    ) -> dict[str, Any]:
        def emit(event: dict[str, Any]) -> None:
            if event_emitter is not None:
                event_emitter(event)

        run_id = _make_run_id()
        self._cancel_event.clear()
        configured_workers = max_workers
        if configured_workers is None:
            configured_workers = int(os.cpu_count() or 1)
        available_cpu_ids = self._available_cpu_ids()
        configured_jobs = max(1, int(configured_workers))
        cpu_thread_budget = max(1, min(configured_jobs, len(available_cpu_ids)))
        policy = str(self.contract.get("rng_policy", "off")).strip().lower()
        if policy not in {"off", "seeded", "strict"}:
            raise RuntimeError(f"unsupported rng_policy: {policy}")
        self._rng_policy = policy
        self._rng_seed_base = int(self.contract.get("rng_seed", 0))
        selected_targets: list[str] = list(dict.fromkeys(targets or []))
        if selected_targets:
            unknown = sorted(set(selected_targets).difference(self.task_specs.keys()))
            if unknown:
                raise RuntimeError(f"unknown target task(s): {unknown}")
            selected_tasks = _dependency_closure(selected_targets, self.deps)
        else:
            selected_tasks = set(self.task_specs.keys())
        impossible = sorted(
            t
            for t in selected_tasks
            if self._task_cpu_limits(self.task_specs[t])[0] > cpu_thread_budget
        )
        if impossible:
            raise RuntimeError(
                f"task(s) require cpu_threads_min above global budget {cpu_thread_budget}: {impossible}"
            )
        emit(
            {
                "event": "run_start",
                "run_id": run_id,
                "pipeline": str(self.contract.get("pipeline_name", "")),
                "task_count": len(selected_tasks),
                "max_workers": configured_jobs,
                "cpu_thread_budget": cpu_thread_budget,
                "rng_policy": self._rng_policy,
                "targets": selected_targets,
            }
        )

        critical_scores = self._critical_path_scores(selected_tasks)
        rows: dict[str, TaskRow] = {}
        task_total = len(selected_tasks)
        pending: set[str] = set(selected_tasks)
        completed: set[str] = set()
        started_count = 0
        running: dict[concurrent.futures.Future[TaskRow], tuple[str, int, list[int]]] = {}
        allocated_cpu_ids: set[int] = set()
        last_task_stats_emit = 0.0

        def maybe_emit_task_stats(force: bool = False) -> None:
            nonlocal last_task_stats_emit
            now = float(time.perf_counter())
            if not force and (now - last_task_stats_emit) < 0.2:
                return
            emit(
                {
                    "event": "task_stats",
                    "run_id": run_id,
                    "tasks": self._active_task_stats(),
                }
            )
            last_task_stats_emit = now

        def try_launch_ready(executor: concurrent.futures.ThreadPoolExecutor) -> bool:
            nonlocal started_count
            made_progress = False
            running_cpu_threads = sum(int(v[1]) for v in running.values())
            free_threads = max(cpu_thread_budget - running_cpu_threads, 0)
            if free_threads <= 0:
                return False

            ready_all = [t for t in pending if set(self.deps.get(t, [])).issubset(completed)]
            if not ready_all:
                return False
            ready_sorted = sorted(
                ready_all,
                key=lambda t: (-int(critical_scores.get(t, 0)), t),
            )
            if running:
                ready_sorted = [t for t in ready_sorted if self.task_specs[t].map_config is None]
                if not ready_sorted:
                    return False

            selected: list[str] = []
            remaining_for_mins = free_threads
            for task_name in ready_sorted:
                min_threads, _, _ = self._task_cpu_limits(self.task_specs[task_name])
                if min_threads > remaining_for_mins:
                    continue
                selected.append(task_name)
                remaining_for_mins -= min_threads
            if not selected:
                return False
            if self.task_specs[selected[0]].map_config is not None:
                selected = [selected[0]]

            limits: dict[str, tuple[int, int, int | None]] = {
                task_name: self._task_cpu_limits(self.task_specs[task_name]) for task_name in selected
            }
            allocations: dict[str, int] = {}
            for task_name in selected:
                min_threads, _, _ = limits[task_name]
                allocations[task_name] = min_threads
            extra_threads = max(free_threads - sum(allocations.values()), 0)

            def spread_extra(cap_fn: Callable[[str], int]) -> None:
                nonlocal extra_threads
                while extra_threads > 0:
                    progressed = False
                    for task_name in selected:
                        cap = max(cap_fn(task_name), allocations[task_name])
                        if allocations[task_name] >= cap:
                            continue
                        allocations[task_name] += 1
                        extra_threads -= 1
                        progressed = True
                        if extra_threads <= 0:
                            break
                    if not progressed:
                        break

            spread_extra(
                lambda task_name: min(
                    limits[task_name][1],
                    limits[task_name][2] if limits[task_name][2] is not None else limits[task_name][1],
                )
            )
            spread_extra(
                lambda task_name: (
                    limits[task_name][2] if limits[task_name][2] is not None else cpu_thread_budget
                )
            )

            def claim_cpu_ids(threads: int) -> list[int]:
                if threads <= 0:
                    return []
                free_ids = [cpu for cpu in available_cpu_ids if cpu not in allocated_cpu_ids]
                chosen = free_ids[:threads]
                if len(chosen) < threads:
                    for cpu in available_cpu_ids:
                        if cpu in chosen:
                            continue
                        chosen.append(cpu)
                        if len(chosen) >= threads:
                            break
                for cpu in chosen:
                    allocated_cpu_ids.add(cpu)
                return chosen

            for task_name in selected:
                pending.remove(task_name)
                started_count += 1
                cpu_threads_alloc = max(int(allocations.get(task_name, 1)), 1)
                spec = self.task_specs[task_name]
                cpu_affinity = claim_cpu_ids(cpu_threads_alloc)
                emit(
                    {
                        "event": "task_start",
                        "run_id": run_id,
                        "task": task_name,
                        "index": started_count,
                        "of": task_total,
                        "cpu_threads_alloc": cpu_threads_alloc,
                    }
                )
                blocked_upstream = any(rows[d].status == "blocked" for d in self.deps.get(task_name, []))
                if blocked_upstream:
                    row = TaskRow(
                        task=task_name,
                        status="blocked",
                        cache_hit=False,
                        cache_reason="skipped_blocked_upstream",
                        blocked_reason="blocked_upstream",
                        gate_rows=[],
                        runtime_seconds=0.0,
                        inputs_hashes=self._compute_input_hashes(spec),
                        output_hashes=self._compute_output_hashes(spec),
                        cpu_threads_alloc=cpu_threads_alloc,
                        cpu_affinity=cpu_affinity,
                    )
                    for cpu in cpu_affinity:
                        allocated_cpu_ids.discard(cpu)
                    rows[task_name] = row
                    completed.add(task_name)
                    emit(
                        {
                            "event": "task_end",
                            "run_id": run_id,
                            "task": task_name,
                            "status": row.status,
                            "cache_hit": bool(row.cache_hit),
                            "cache_reason": row.cache_reason,
                            "blocked_reason": row.blocked_reason,
                            "runtime_s": float(row.runtime_seconds),
                        }
                    )
                    made_progress = True
                    continue
                if spec.map_config is not None:
                    try:
                        row = self._run_map_task(
                            spec,
                            run_id=run_id,
                            max_workers=cpu_thread_budget,
                            progress_emitter=emit,
                            cpu_threads_alloc=cpu_threads_alloc,
                            cpu_affinity=cpu_affinity,
                        )
                    finally:
                        for cpu in cpu_affinity:
                            allocated_cpu_ids.discard(cpu)
                    rows[task_name] = row
                    completed.add(task_name)
                    emit(
                        {
                            "event": "task_end",
                            "run_id": run_id,
                            "task": task_name,
                            "status": row.status,
                            "cache_hit": bool(row.cache_hit),
                            "cache_reason": row.cache_reason,
                            "blocked_reason": row.blocked_reason,
                            "runtime_s": float(row.runtime_seconds),
                        }
                    )
                    made_progress = True
                    continue
                fut = executor.submit(
                    self._run_task,
                    spec,
                    run_id=run_id,
                    top_task=task_name,
                    progress_emitter=emit,
                    cpu_threads_alloc=cpu_threads_alloc,
                    cpu_affinity=cpu_affinity,
                )
                running[fut] = (task_name, cpu_threads_alloc, cpu_affinity)
                maybe_emit_task_stats(force=True)
                made_progress = True
            return made_progress

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(task_total, cpu_thread_budget)))
        try:
            while pending or running:
                launched = try_launch_ready(executor)
                if not running:
                    if pending and not launched:
                        raise RuntimeError("scheduler deadlock: pending tasks with no runnable tasks")
                    continue
                done, _ = concurrent.futures.wait(
                    set(running.keys()),
                    timeout=0.2,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    maybe_emit_task_stats(force=False)
                    continue
                for fut in done:
                    task_name, _, affinity = running.pop(fut)
                    for cpu in affinity:
                        allocated_cpu_ids.discard(cpu)
                    row = fut.result()
                    rows[task_name] = row
                    completed.add(task_name)
                    emit(
                        {
                            "event": "task_end",
                            "run_id": run_id,
                            "task": task_name,
                            "status": row.status,
                            "cache_hit": bool(row.cache_hit),
                            "cache_reason": row.cache_reason,
                            "blocked_reason": row.blocked_reason,
                                "runtime_s": float(row.runtime_seconds),
                            }
                        )
                maybe_emit_task_stats(force=True)
        except KeyboardInterrupt:
            self._request_cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            self._cancel_event.clear()

        claim_targets = sorted(selected_targets)
        claim_target = claim_targets[0] if len(claim_targets) == 1 else ""
        claim_blocking_tasks = sorted([t for t, s in self.task_specs.items() if s.claim_blocking and t in selected_tasks])
        claim_scope_tasks = sorted(set(claim_blocking_tasks + claim_targets))
        claim_scope_statuses = {t: rows[t].status if t in rows else "missing" for t in claim_scope_tasks}
        decision = classify_claim(claim_scope_statuses)
        claim_class = decision.claim_class
        claim_reason = decision.claim_reason
        claim_blockers = list(decision.blockers)
        claim_diagnostics = list(decision.diagnostics)
        claim_blocking_ready = all(rows[t].status in CERTIFIABLE_STATUSES for t in claim_blocking_tasks)
        target_ready = all(rows[t].status in CERTIFIABLE_STATUSES for t in claim_targets) if claim_targets else True

        row_list = [
            {
                "task": r.task,
                "status": r.status,
                "cache_hit": r.cache_hit,
                "cache_reason": r.cache_reason,
                "blocked_reason": r.blocked_reason,
                "gate_rows": r.gate_rows,
                "runtime_seconds": r.runtime_seconds,
                "inputs_hashes": r.inputs_hashes,
                "output_hashes": r.output_hashes,
                "cpu_threads_alloc": int(r.cpu_threads_alloc),
                "cpu_affinity": list(r.cpu_affinity or []),
            }
            for _, r in sorted(rows.items())
        ]
        report = {
            "artifact": "claimguard_run_report",
            "created_utc": _utc_now(),
            "run_id": run_id,
            "pipeline": str(self.contract.get("pipeline_name", "")),
            "summary": {
                "task_count": len(selected_tasks),
                "max_workers": configured_jobs,
                "cpu_thread_budget": cpu_thread_budget,
                "rng_policy": self._rng_policy,
                "rng_seed_base": self._rng_seed_base,
                "cache_hits": sum(1 for r in rows.values() if r.cache_hit),
                "cache_reason_counts": {
                    k: sum(1 for r in rows.values() if r.cache_reason == k)
                    for k in sorted({r.cache_reason for r in rows.values()})
                },
                "task_status_counts": {
                    "ok": sum(1 for r in rows.values() if r.status == "ok"),
                    "replay_ok": sum(1 for r in rows.values() if r.status == "replay_ok"),
                    "diagnostic_only": sum(1 for r in rows.values() if r.status == "diagnostic_only"),
                    "blocked": sum(1 for r in rows.values() if r.status == "blocked"),
                },
                "policy_exemptions": [
                    {
                        "task": name,
                        "read_exemptions": list(spec.read_exemptions),
                        "write_exemptions": list(spec.write_exemptions),
                        "allow_subprocess": bool(spec.allow_subprocess),
                    }
                    for name, spec in sorted(self.task_specs.items())
                    if name in selected_tasks and (spec.read_exemptions or spec.write_exemptions or spec.allow_subprocess)
                ],
            },
            "claim": {
                "target_task": claim_target,
                "target_tasks": claim_targets,
                "target_ready": target_ready,
                "claim_blocking_tasks": claim_blocking_tasks,
                "claim_blocking_ready": claim_blocking_ready,
                "claim_scope_tasks": claim_scope_tasks,
                "claim_scope_statuses": claim_scope_statuses,
                "claim_class": claim_class,
                "claim_reason": claim_reason,
                "claim_blockers": claim_blockers,
                "claim_diagnostics": claim_diagnostics,
            },
            "task_rows": row_list,
        }
        write_json(self.report_root / "run_report_latest.json", report)
        cert_tasks = sorted(set(claim_scope_tasks))
        certificate = {
            "artifact": "claimguard_claim_certificate",
            "created_utc": _utc_now(),
            "run_id": run_id,
            "pipeline": str(self.contract.get("pipeline_name", "")),
            "claim_class": claim_class,
            "claim_reason": claim_reason,
            "target_task": claim_target,
            "target_tasks": claim_targets,
            "claim_blocking_tasks": claim_blocking_tasks,
            "claim_scope_tasks": claim_scope_tasks,
            "claim_scope_statuses": claim_scope_statuses,
            "claim_blockers": claim_blockers,
            "claim_diagnostics": claim_diagnostics,
            "evidence": [
                {
                    "task": t,
                    "status": rows[t].status if t in rows else "missing",
                    "cache_hit": bool(rows[t].cache_hit) if t in rows else False,
                    "cache_reason": rows[t].cache_reason if t in rows else "missing",
                    "blocked_reason": rows[t].blocked_reason if t in rows else "missing",
                    "cpu_threads_alloc": int(rows[t].cpu_threads_alloc) if t in rows else 0,
                    "cpu_affinity": list(rows[t].cpu_affinity or []) if t in rows else [],
                    "interface_output": self.task_specs[t].interface_output if t in self.task_specs else "",
                    "output_hashes": rows[t].output_hashes if t in rows else {},
                    "gate_rows": rows[t].gate_rows if t in rows else [],
                }
                for t in cert_tasks
            ],
        }
        write_json(self.report_root / "claim_certificate_latest.json", certificate)
        md_lines = [
            "# claimguard Run Report",
            "",
            f"- run_id: `{run_id}`",
            f"- pipeline: `{report['pipeline']}`",
            f"- claim_class: `{claim_class}`",
            f"- cache_hits: `{report['summary']['cache_hits']}`",
            f"- claim_certificate_json: `{(self.report_root / 'claim_certificate_latest.json').resolve()}`",
            "",
            "## Task Status",
            "| task | status | cache_hit | blocked_reason |",
            "|---|---|---:|---|",
        ]
        for row in row_list:
            md_lines.append(
                f"| `{row['task']}` | `{row['status']}` | {int(bool(row['cache_hit']))} | "
                f"`{row['cache_reason']}` / `{row['blocked_reason']}` |"
            )
        (self.report_root / "run_report_latest.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
        emit(
            {
                "event": "run_end",
                "run_id": run_id,
                "claim_class": claim_class,
                "claim_reason": claim_reason,
                "report_json": str((self.report_root / "run_report_latest.json").resolve()),
                "claim_certificate_json": str((self.report_root / "claim_certificate_latest.json").resolve()),
                "summary": dict(report["summary"]),
            }
        )
        return report
