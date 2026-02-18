"""Helpers for resolving task-local artifact aliases."""

from __future__ import annotations

import json
import os
from pathlib import Path


def task_paths() -> dict[str, Path]:
    """Return alias-backed task paths resolved against current task workspace."""
    raw = os.environ.get("CG_TASK_PATHS_JSON", "{}")
    try:
        obj = json.loads(raw)
    except Exception as e:
        raise RuntimeError("invalid JSON in environment variable CG_TASK_PATHS_JSON") from e
    if not isinstance(obj, dict):
        raise RuntimeError("environment variable CG_TASK_PATHS_JSON must decode to object")

    out: dict[str, Path] = {}
    for k, v in obj.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise RuntimeError("environment variable CG_TASK_PATHS_JSON must be object[str, str]")
        out[k] = Path(v).resolve()
    return out


def get_task_paths() -> dict[str, Path]:
    """Alias for task_paths()."""
    return task_paths()


__all__ = ["task_paths", "get_task_paths"]
