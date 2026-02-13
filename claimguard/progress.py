"""Optional task-progress helper for claimguard-managed tasks."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from typing import Any

_LOCK = threading.Lock()
_FD: int | None = None
_DISABLED = False
_LAST_EMIT_T = 0.0
_MIN_INTERVAL_S = 0.2
_PIPE_BUF_BYTES = 4096


def _encode_frame(payload: dict[str, Any]) -> bytes | None:
    def _to_bytes(obj: dict[str, Any]) -> bytes:
        return (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")

    frame = _to_bytes(payload)
    if len(frame) <= _PIPE_BUF_BYTES:
        return frame

    compact = dict(payload)
    compact.pop("meta", None)
    for key, max_chars in (("message", 160), ("phase", 80)):
        if key in compact:
            text = str(compact[key]).strip()
            if len(text) > max_chars:
                compact[key] = text[: max_chars - 1] + "…"
    frame = _to_bytes(compact)
    if len(frame) <= _PIPE_BUF_BYTES:
        return frame

    minimal = {k: compact[k] for k in ("done", "total", "fraction", "eta_s") if k in compact}
    if "message" in compact and not minimal:
        minimal["message"] = str(compact["message"])[:80]
    if not minimal:
        return None
    frame = _to_bytes(minimal)
    if len(frame) <= _PIPE_BUF_BYTES:
        return frame
    return None


def _ensure_fd() -> int | None:
    global _FD, _DISABLED, _MIN_INTERVAL_S, _PIPE_BUF_BYTES
    if _FD is not None:
        return _FD
    if _DISABLED:
        return None
    raw = str(os.environ.get("CG_PROGRESS_FD", "")).strip()
    if not raw:
        _DISABLED = True
        return None
    try:
        fd = int(raw)
    except Exception:
        _DISABLED = True
        return None
    if fd < 0:
        _DISABLED = True
        return None
    _FD = fd
    try:
        flags = int(fcntl.fcntl(fd, fcntl.F_GETFL))
        if (flags & os.O_NONBLOCK) == 0:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except Exception:
        pass
    try:
        pipe_buf = int(os.fpathconf(fd, "PC_PIPE_BUF"))
        if pipe_buf > 0:
            _PIPE_BUF_BYTES = max(256, min(pipe_buf, 16384))
    except Exception:
        _PIPE_BUF_BYTES = 4096
    raw_interval = str(os.environ.get("CG_PROGRESS_MIN_INTERVAL_S", "0.2")).strip()
    try:
        _MIN_INTERVAL_S = max(0.0, float(raw_interval))
    except Exception:
        _MIN_INTERVAL_S = 0.2
    return _FD


def update(
    *,
    done: int | None = None,
    total: int | None = None,
    fraction: float | None = None,
    message: str | None = None,
    phase: str | None = None,
    eta_s: float | None = None,
    meta: Any = None,
    force: bool = False,
) -> bool:
    """Emit a task-progress snapshot to the claimguard runner.

    This is a no-op when `CG_PROGRESS_FD` is not set.
    """
    global _FD, _LAST_EMIT_T, _DISABLED
    fd = _ensure_fd()
    if fd is None:
        return False

    payload: dict[str, Any] = {}
    if done is not None:
        try:
            payload["done"] = max(int(done), 0)
        except Exception:
            pass
    if total is not None:
        try:
            payload["total"] = max(int(total), 0)
        except Exception:
            pass
    if payload.get("total", 0) > 0 and "done" in payload:
        payload["fraction"] = min(max(float(payload["done"]) / float(payload["total"]), 0.0), 1.0)
    if fraction is not None:
        try:
            payload["fraction"] = min(max(float(fraction), 0.0), 1.0)
        except Exception:
            pass
    if message is not None:
        text = str(message).strip()
        if text:
            payload["message"] = text
    if phase is not None:
        text = str(phase).strip()
        if text:
            payload["phase"] = text
    if eta_s is not None:
        try:
            payload["eta_s"] = max(float(eta_s), 0.0)
        except Exception:
            pass
    if meta is not None:
        try:
            json.dumps(meta)
            payload["meta"] = meta
        except Exception:
            pass
    if not payload:
        return False

    with _LOCK:
        now = float(time.perf_counter())
        if not force and (now - _LAST_EMIT_T) < _MIN_INTERVAL_S:
            return False
        data = _encode_frame(payload)
        if data is None:
            return False
        try:
            written = os.write(fd, data)
            if written != len(data):
                return False
        except BlockingIOError:
            return False
        except BrokenPipeError:
            _DISABLED = True
            _FD = None
            return False
        except Exception:
            _DISABLED = True
            _FD = None
            return False
        _LAST_EMIT_T = now
        return True
