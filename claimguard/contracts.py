"""Contract loading helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise RuntimeError(f"expected JSON object in {path}")
    return obj


def resolve_workspace_root(contract_path: Path, contract_obj: dict[str, Any]) -> Path:
    # Workspace root is always the directory that contains claimguard.json.
    # Keep the signature for compatibility with existing call sites.
    _ = contract_obj
    root = contract_path.parent.resolve()
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"workspace_root does not exist or is not directory: {root}")
    return root


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
