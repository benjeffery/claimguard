"""Task discovery and DAG inference."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gates import compile_gate_expr


def _load_optional_top_level_literal(path: Path, variable_name: str) -> dict[str, Any] | None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    value_node: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == variable_name:
                    value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == variable_name:
                value_node = node.value
    if value_node is None:
        return None
    try:
        value = ast.literal_eval(value_node)
    except (ValueError, SyntaxError) as exc:
        line = getattr(value_node, "lineno", "?")
        col = getattr(value_node, "col_offset", "?")
        expr = ast.get_source_segment(source, value_node) or ""
        expr = " ".join(expr.split())
        if len(expr) > 180:
            expr = expr[:177] + "..."
        raise RuntimeError(
            f"{path}: `{variable_name}` must be a top-level literal dict "
            f"(only Python literals: dict/list/str/num/bool/None). "
            f"Found non-literal expression at line {line}:{col}. "
            f"Do not use calls/comprehensions/variable-derived expressions "
            f"(e.g. `list(INPUTS.values())`). Use an explicit literal list/dict. "
            f"Expression: {expr!r}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"`{variable_name}` must evaluate to dict in {path}")
    return value


def _validate_rel_path_token(task_name: str, field_name: str, token: str) -> None:
    p = Path(token)
    if p.is_absolute():
        raise RuntimeError(f"task `{task_name}` `{field_name}` must use relative paths only: {token!r}")
    if any(part == ".." for part in p.parts):
        raise RuntimeError(f"task `{task_name}` `{field_name}` cannot contain parent traversal: {token!r}")


@dataclass(frozen=True)
class TaskSpec:
    name: str
    script_rel: str
    script_path: Path
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    read_exemptions: tuple[str, ...]
    write_exemptions: tuple[str, ...]
    interface_output: str
    gates: tuple[dict[str, Any], ...]
    claim_blocking: bool
    allow_subprocess: bool
    allow_rng: bool
    map_config: dict[str, Any] | None
    params: dict[str, Any]


def _validate_task_dict(task_name: str, spec: dict[str, Any]) -> None:
    if "inputs" not in spec or "outputs" not in spec:
        raise RuntimeError(f"task `{task_name}` missing required keys: inputs/outputs")
    if "interface_output" not in spec:
        raise RuntimeError(f"task `{task_name}` missing required key: interface_output")
    if not isinstance(spec.get("inputs"), list) or not all(isinstance(x, str) for x in spec["inputs"]):
        raise RuntimeError(f"task `{task_name}` has invalid `inputs`")
    if not isinstance(spec.get("outputs"), list) or not all(isinstance(x, str) for x in spec["outputs"]):
        raise RuntimeError(f"task `{task_name}` has invalid `outputs`")
    if not isinstance(spec.get("read_exemptions", []), list) or not all(
        isinstance(x, str) for x in spec.get("read_exemptions", [])
    ):
        raise RuntimeError(f"task `{task_name}` has invalid `read_exemptions`")
    if not isinstance(spec.get("write_exemptions", []), list) or not all(
        isinstance(x, str) for x in spec.get("write_exemptions", [])
    ):
        raise RuntimeError(f"task `{task_name}` has invalid `write_exemptions`")
    if not isinstance(spec.get("gates", []), list):
        raise RuntimeError(f"task `{task_name}` has invalid `gates`")
    if "claim_blocking" in spec and not isinstance(spec.get("claim_blocking"), bool):
        raise RuntimeError(f"task `{task_name}` has invalid `claim_blocking`")
    if "allow_subprocess" in spec and not isinstance(spec.get("allow_subprocess"), bool):
        raise RuntimeError(f"task `{task_name}` has invalid `allow_subprocess`")
    if "allow_rng" in spec and not isinstance(spec.get("allow_rng"), bool):
        raise RuntimeError(f"task `{task_name}` has invalid `allow_rng`")
    if "params" in spec and not isinstance(spec.get("params"), dict):
        raise RuntimeError(f"task `{task_name}` has invalid `params`")
    if str(spec["interface_output"]) not in set(spec["outputs"]):
        raise RuntimeError(f"task `{task_name}` interface_output must be listed in outputs")
    if len(spec["outputs"]) == 0:
        raise RuntimeError(f"task `{task_name}` must declare at least one output")

    for token in spec["inputs"]:
        _validate_rel_path_token(task_name, "inputs", str(token))
    for token in spec["outputs"]:
        _validate_rel_path_token(task_name, "outputs", str(token))
    _validate_rel_path_token(task_name, "interface_output", str(spec["interface_output"]))
    for token in spec.get("read_exemptions", []):
        _validate_rel_path_token(task_name, "read_exemptions", str(token))
    for token in spec.get("write_exemptions", []):
        _validate_rel_path_token(task_name, "write_exemptions", str(token))

    gates = list(spec.get("gates", []))
    gate_names: set[str] = set()
    for i, gate in enumerate(gates):
        if not isinstance(gate, dict):
            raise RuntimeError(f"task `{task_name}` gate[{i}] must be object")
        keys = set(gate.keys())
        required = {"name", "expr"}
        if keys != required:
            raise RuntimeError(
                f"task `{task_name}` gate[{i}] must have exactly keys {sorted(required)}; got {sorted(keys)}"
            )
        name = gate.get("name")
        expr = gate.get("expr")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError(f"task `{task_name}` gate[{i}] has invalid name")
        if name in gate_names:
            raise RuntimeError(f"task `{task_name}` duplicate gate name `{name}`")
        gate_names.add(name)
        if not isinstance(expr, str) or not expr.strip():
            raise RuntimeError(f"task `{task_name}` gate[{i}] has invalid expr")
        try:
            compile_gate_expr(expr)
        except Exception as e:
            raise RuntimeError(f"task `{task_name}` gate[{i}] expr invalid: {e}") from e
    if "map" in spec:
        m = spec.get("map")
        if not isinstance(m, dict):
            raise RuntimeError(f"task `{task_name}` has invalid `map`")
        if not isinstance(m.get("items_input"), str):
            raise RuntimeError(f"task `{task_name}` map must define string `items_input`")
        if str(m.get("items_input")) not in set(spec["inputs"]):
            raise RuntimeError(
                f"task `{task_name}` map.items_input must be declared in task inputs for dependency/freshness tracking"
            )
        _validate_rel_path_token(task_name, "map.items_input", str(m.get("items_input")))
        if "items_path" in m and not isinstance(m.get("items_path"), str):
            raise RuntimeError(f"task `{task_name}` map has invalid `items_path`")
        if "item_name_field" in m and not isinstance(m.get("item_name_field"), str):
            raise RuntimeError(f"task `{task_name}` map has invalid `item_name_field`")
        if "allow_empty" in m and not isinstance(m.get("allow_empty"), bool):
            raise RuntimeError(f"task `{task_name}` map has invalid `allow_empty`")
        marker_tokens = ("{map_index}", "{map_key}", "{map_hash}")
        if not all(any(tok in out for tok in marker_tokens) for out in spec["outputs"]):
            raise RuntimeError(
                f"task `{task_name}` map requires each output path to include one of {marker_tokens}"
            )
        if not any(tok in str(spec["interface_output"]) for tok in marker_tokens):
            raise RuntimeError(
                f"task `{task_name}` map requires interface_output to include one of {marker_tokens}"
            )
    if set(spec["inputs"]).intersection(set(spec["outputs"])):
        raise RuntimeError(f"task `{task_name}` cannot read and write same artifact path")


def discover_tasks(
    *,
    workspace_root: Path,
    task_roots: list[str],
    task_params: dict[str, dict[str, Any]] | None = None,
) -> dict[str, TaskSpec]:
    params_map = task_params or {}
    if not isinstance(params_map, dict):
        raise RuntimeError("task_params must be an object")
    for k, v in params_map.items():
        if not isinstance(k, str) or not k.strip():
            raise RuntimeError("task_params keys must be non-empty task-name strings")
        if not isinstance(v, dict):
            raise RuntimeError(f"task_params[{k!r}] must be an object")

    raw_by_name: dict[str, dict[str, Any]] = {}
    for root_rel in task_roots:
        root = (workspace_root / root_rel).resolve()
        if not root.exists() or not root.is_dir():
            raise RuntimeError(f"task_root does not exist or is not dir: {root_rel}")
        for script_path in sorted(root.rglob("*.py")):
            cg_task = _load_optional_top_level_literal(script_path, "CG_TASK")
            if cg_task is None:
                continue
            task_name = ".".join(script_path.relative_to(root).with_suffix("").parts)
            if task_name in raw_by_name:
                raise RuntimeError(f"duplicate discovered task name: {task_name}")
            merged = dict(cg_task)
            merged_params = dict(merged.get("params", {}))
            merged_params.update(dict(params_map.get(task_name, {})))
            merged["params"] = merged_params
            merged["script_rel"] = str(script_path.resolve().relative_to(workspace_root.resolve()))
            _validate_task_dict(task_name, merged)
            raw_by_name[task_name] = merged

    unknown_param_tasks = set(params_map).difference(set(raw_by_name))
    if unknown_param_tasks:
        raise RuntimeError(f"task_params references unknown tasks: {sorted(unknown_param_tasks)}")

    task_specs: dict[str, TaskSpec] = {}
    for name, spec in raw_by_name.items():
        task_specs[name] = TaskSpec(
            name=name,
            script_rel=str(spec["script_rel"]),
            script_path=(workspace_root / str(spec["script_rel"])).resolve(),
            inputs=tuple(str(x) for x in spec["inputs"]),
            outputs=tuple(str(x) for x in spec["outputs"]),
            read_exemptions=tuple(str(x) for x in spec.get("read_exemptions", [])),
            write_exemptions=tuple(str(x) for x in spec.get("write_exemptions", [])),
            interface_output=str(spec["interface_output"]),
            gates=tuple(
                {
                    "name": str(g["name"]),
                    "expr": str(g["expr"]),
                }
                for g in spec.get("gates", [])
            ),
            claim_blocking=bool(spec.get("claim_blocking", True)),
            allow_subprocess=bool(spec.get("allow_subprocess", False)),
            allow_rng=bool(spec.get("allow_rng", False)),
            map_config=dict(spec.get("map")) if isinstance(spec.get("map"), dict) else None,
            params=dict(spec.get("params", {})),
        )
    return task_specs


def infer_dependencies(tasks: dict[str, TaskSpec]) -> dict[str, list[str]]:
    producer_by_output: dict[str, str] = {}
    for task_name, spec in tasks.items():
        for out_rel in spec.outputs:
            prev = producer_by_output.get(out_rel)
            if prev is not None and prev != task_name:
                raise RuntimeError(f"duplicate output producer for `{out_rel}`: `{prev}` and `{task_name}`")
            producer_by_output[out_rel] = task_name

    deps: dict[str, list[str]] = {}
    for task_name, spec in tasks.items():
        inferred = sorted(
            {
                producer_by_output[in_rel]
                for in_rel in spec.inputs
                if in_rel in producer_by_output and producer_by_output[in_rel] != task_name
            }
        )
        deps[task_name] = inferred
    return deps


def topological_order(tasks: dict[str, TaskSpec], deps: dict[str, list[str]]) -> list[str]:
    pending = set(tasks.keys())
    done: set[str] = set()
    order: list[str] = []
    while pending:
        ready = sorted(t for t in pending if set(deps.get(t, [])).issubset(done))
        if not ready:
            raise RuntimeError("dependency cycle detected")
        for task in ready:
            pending.remove(task)
            done.add(task)
            order.append(task)
    return order


def graphviz_dot(
    tasks: dict[str, TaskSpec],
    deps: dict[str, list[str]],
    *,
    graph_name: str = "claimguard",
    rankdir: str = "LR",
) -> str:
    def esc(token: str) -> str:
        return str(token).replace("\\", "\\\\").replace('"', '\\"')

    safe_graph_name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(graph_name)).strip("_") or "claimguard"

    lines = [f"digraph {safe_graph_name} {{", f"  rankdir={rankdir};", "  node [shape=box];"]
    for name in sorted(tasks.keys()):
        lines.append(f'  "{esc(name)}";')
    for name in sorted(deps.keys()):
        for dep in deps.get(name, []):
            lines.append(f'  "{esc(dep)}" -> "{esc(name)}";')
    lines.append("}")
    return "\n".join(lines) + "\n"
