"""Managed task worker with strict I/O and subprocess policy enforcement."""

from __future__ import annotations

import argparse
import json
import os
import random
import runpy
import sys
from pathlib import Path
from typing import Any


def _env_json_list(name: str) -> list[str]:
    raw = os.environ.get(name, "[]")
    try:
        obj = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"invalid JSON in environment variable {name}") from e
    if not isinstance(obj, list):
        raise RuntimeError(f"environment variable {name} must decode to list")
    out: list[str] = []
    for v in obj:
        if isinstance(v, str):
            out.append(v)
        else:
            raise RuntimeError(f"environment variable {name} must be list[str]")
    return out


def _path_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _coerce_path(obj: Any) -> Path | None:
    if isinstance(obj, bytes):
        try:
            return Path(obj.decode("utf-8")).resolve()
        except Exception:
            return None
    if isinstance(obj, str):
        return Path(obj).resolve()
    if isinstance(obj, os.PathLike):
        try:
            return Path(obj).resolve()
        except Exception:
            return None
    return None


def _mode_is_write(mode: str) -> bool:
    return any(ch in mode for ch in ("w", "a", "+", "x"))


def _flags_is_write(flags: int) -> bool:
    write_flags = (
        os.O_WRONLY
        | os.O_RDWR
        | os.O_APPEND
        | os.O_CREAT
        | os.O_TRUNC
        | os.O_EXCL
    )
    return bool(flags & write_flags)


class _Policy:
    def __init__(self) -> None:
        self.workspace_root = Path(os.environ["CG_WORKSPACE_ROOT"]).resolve()
        self.enforce_io = os.environ.get("CG_ENFORCE_IO", "0") == "1"
        self.allow_subprocess = os.environ.get("CG_ALLOW_SUBPROCESS", "0") == "1"

        self.allowed_read_files: set[Path] = set()
        self.allowed_read_dirs: set[Path] = set()
        for p in _env_json_list("CG_ALLOWED_READS_JSON"):
            rp = Path(p).resolve()
            if rp.exists() and rp.is_dir():
                self.allowed_read_dirs.add(rp)
            else:
                self.allowed_read_files.add(rp)

        self.allowed_write_files: set[Path] = set()
        # Directories for tree-write semantics (e.g. managed tmp dirs, explicit dir exemptions).
        self.allowed_write_tree_dirs: set[Path] = set()
        # Directories allowed for mkdir/rmdir as parents of declared output files.
        self.allowed_mkdir_dirs: set[Path] = set()
        for p in _env_json_list("CG_ALLOWED_WRITES_JSON"):
            rp = Path(p).resolve()
            if rp.exists() and rp.is_dir():
                self.allowed_write_tree_dirs.add(rp)
            else:
                self.allowed_write_files.add(rp)
                cur = rp.parent
                while _path_under(cur, self.workspace_root):
                    self.allowed_mkdir_dirs.add(cur)
                    if cur == self.workspace_root:
                        break
                    cur = cur.parent

    def _check_read(self, path: Path, event: str) -> None:
        if path in self.allowed_read_files:
            return
        for d in self.allowed_read_dirs:
            if _path_under(path, d):
                return
        raise PermissionError(f"claimguard read denied ({event}): {path}")

    def _check_write_file(self, path: Path, event: str) -> None:
        if path in self.allowed_write_files:
            return
        for d in self.allowed_write_tree_dirs:
            if _path_under(path, d):
                return
        raise PermissionError(f"claimguard write denied ({event}): {path}")

    def _check_write_dir_mutation(self, path: Path, event: str) -> None:
        for d in self.allowed_write_tree_dirs:
            if _path_under(path, d):
                return
        if path in self.allowed_mkdir_dirs:
            return
        raise PermissionError(f"claimguard dir mutation denied ({event}): {path}")

    def check_file_access(self, path: Path, *, write: bool, event: str) -> None:
        if not self.enforce_io:
            return
        rp = path.resolve()
        if not _path_under(rp, self.workspace_root):
            return
        if write:
            self._check_write_file(rp, event)
        else:
            self._check_read(rp, event)

    def check_dir_mutation(self, path: Path, *, event: str) -> None:
        if not self.enforce_io:
            return
        rp = path.resolve()
        if not _path_under(rp, self.workspace_root):
            return
        self._check_write_dir_mutation(rp, event)

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event in {"subprocess.Popen", "os.system"} and not self.allow_subprocess:
            raise PermissionError(f"claimguard subprocess denied ({event})")

        if event in {"open", "os.open"}:
            if not args:
                return
            p = _coerce_path(args[0])
            if p is None:
                if self.enforce_io and not isinstance(args[0], int):
                    raise PermissionError(f"claimguard path decode denied ({event})")
                return
            write = False
            if len(args) >= 2 and isinstance(args[1], str):
                write = _mode_is_write(args[1])
            elif len(args) >= 2 and isinstance(args[1], int):
                write = _flags_is_write(int(args[1]))
            elif len(args) >= 3 and isinstance(args[2], int):
                write = _flags_is_write(int(args[2]))
            self.check_file_access(p, write=write, event=event)
            return

        if event in {"os.remove", "os.unlink", "os.rmdir", "os.mkdir"}:
            if not args:
                return
            p = _coerce_path(args[0])
            if p is None:
                if self.enforce_io:
                    raise PermissionError(f"claimguard path decode denied ({event})")
                return
            if event in {"os.mkdir", "os.rmdir"}:
                self.check_dir_mutation(p, event=event)
            else:
                self.check_file_access(p, write=True, event=event)
            return

        if event in {"os.rename", "os.replace"}:
            if len(args) < 2:
                return
            p0 = _coerce_path(args[0])
            p1 = _coerce_path(args[1])
            if p0 is None or p1 is None:
                if self.enforce_io:
                    raise PermissionError(f"claimguard path decode denied ({event})")
                return
            self.check_file_access(p0, write=True, event=event)
            self.check_file_access(p1, write=True, event=event)
            return


def _install_rng_blockers() -> None:
    def _blocked(name: str):
        def _fn(*_args: Any, **_kwargs: Any) -> Any:
            raise PermissionError(f"claimguard RNG denied ({name}); set allow_rng=true for this task")

        return _fn

    for name in [
        "random",
        "randrange",
        "randint",
        "choice",
        "choices",
        "shuffle",
        "sample",
        "uniform",
        "gauss",
        "triangular",
        "betavariate",
        "expovariate",
        "gammavariate",
        "lognormvariate",
        "normalvariate",
        "vonmisesvariate",
        "paretovariate",
        "weibullvariate",
        "getrandbits",
        "randbytes",
    ]:
        if hasattr(random, name):
            setattr(random, name, _blocked(f"random.{name}"))

    try:
        import numpy as np  # type: ignore
    except ModuleNotFoundError:
        return
    except Exception as e:
        raise RuntimeError("failed to import numpy while installing strict RNG blockers") from e

    for name in [
        "random",
        "rand",
        "randn",
        "randint",
        "choice",
        "uniform",
        "normal",
        "poisson",
        "binomial",
        "shuffle",
        "permutation",
    ]:
        if hasattr(np.random, name):
            setattr(np.random, name, _blocked(f"numpy.random.{name}"))

    if hasattr(np.random, "default_rng"):
        setattr(np.random, "default_rng", _blocked("numpy.random.default_rng"))


def _configure_rng() -> None:
    policy = str(os.environ.get("CG_RNG_POLICY", "off")).strip().lower()
    if policy == "off":
        return
    seed = int(os.environ.get("CG_SEED", "0"))
    allow_rng = os.environ.get("CG_ALLOW_RNG", "0") == "1"

    random.seed(seed)
    try:
        import numpy as np  # type: ignore
    except ModuleNotFoundError:
        np = None  # type: ignore[assignment]
    except Exception as e:
        raise RuntimeError(f"failed to import numpy for RNG policy `{policy}`") from e

    if np is not None:  # type: ignore[name-defined]
        try:
            np.random.seed(seed % (2**32))
        except Exception as e:
            raise RuntimeError(f"failed to seed numpy RNG under policy `{policy}`") from e

    if policy == "strict" and not allow_rng:
        _install_rng_blockers()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="claimguard-worker")
    ap.add_argument("--script", required=True, type=Path)
    args = ap.parse_args(argv)

    policy = _Policy()
    sys.addaudithook(policy.audit)
    _configure_rng()

    script_path = args.script.resolve()
    script_dir = str(script_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    runpy.run_path(str(script_path), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
