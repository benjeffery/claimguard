from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_progress_helper_writes_json_frame(monkeypatch) -> None:
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setenv("CG_PROGRESS_FD", str(write_fd))
        monkeypatch.setenv("CG_PROGRESS_MIN_INTERVAL_S", "0")
        import claimguard.progress as progress

        importlib.reload(progress)
        wrote = progress.update(
            done=1,
            total=4,
            message="loading",
            phase="prep",
            eta_s=12.5,
            meta={"batch": 2},
            force=True,
        )
        assert wrote is True
        os.close(write_fd)
        raw = os.read(read_fd, 4096).decode("utf-8")
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
        try:
            os.close(write_fd)
        except OSError:
            pass

    frame = json.loads(raw.strip())
    assert frame["done"] == 1
    assert frame["total"] == 4
    assert frame["fraction"] == 0.25
    assert frame["message"] == "loading"
    assert frame["phase"] == "prep"
    assert frame["eta_s"] == 12.5
    assert frame["meta"] == {"batch": 2}


def test_progress_helper_ignores_invalid_fields(monkeypatch) -> None:
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setenv("CG_PROGRESS_FD", str(write_fd))
        monkeypatch.setenv("CG_PROGRESS_MIN_INTERVAL_S", "0")
        import claimguard.progress as progress

        importlib.reload(progress)
        wrote = progress.update(
            done=object(),  # type: ignore[arg-type]
            total=object(),  # type: ignore[arg-type]
            fraction=object(),  # type: ignore[arg-type]
            eta_s=object(),  # type: ignore[arg-type]
            meta=set([1]),
            message="still-ok",
            force=True,
        )
        assert wrote is True
        os.close(write_fd)
        raw = os.read(read_fd, 4096).decode("utf-8")
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
        try:
            os.close(write_fd)
        except OSError:
            pass

    frame = json.loads(raw.strip())
    assert frame == {"message": "still-ok"}


def test_progress_helper_drops_update_when_pipe_is_full(monkeypatch) -> None:
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setenv("CG_PROGRESS_FD", str(write_fd))
        monkeypatch.setenv("CG_PROGRESS_MIN_INTERVAL_S", "0")
        import claimguard.progress as progress

        importlib.reload(progress)
        os.set_blocking(write_fd, False)
        while True:
            try:
                os.write(write_fd, b"x" * 4096)
            except BlockingIOError:
                break
        wrote = progress.update(done=1, total=2, force=True)
        assert wrote is False
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
        try:
            os.close(write_fd)
        except OSError:
            pass


def test_progress_helper_caps_large_payload(monkeypatch) -> None:
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setenv("CG_PROGRESS_FD", str(write_fd))
        monkeypatch.setenv("CG_PROGRESS_MIN_INTERVAL_S", "0")
        import claimguard.progress as progress

        importlib.reload(progress)
        huge_meta = {"blob": "x" * 50000}
        wrote = progress.update(done=3, total=10, message="large", meta=huge_meta, force=True)
        assert wrote is True
        os.close(write_fd)
        raw = os.read(read_fd, 65536).decode("utf-8")
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
        try:
            os.close(write_fd)
        except OSError:
            pass

    frame = json.loads(raw.strip())
    assert frame["done"] == 3
    assert frame["total"] == 10
    assert frame["fraction"] == 0.3
    assert frame["message"] == "large"
    assert "meta" not in frame


def test_runner_emits_task_progress_events(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    _write(
        ws / "claimguard.json",
        json.dumps(
            {
                "pipeline_name": "task_progress_demo",
                "task_roots": ["tasks"],
            },
            indent=2,
        )
        + "\n",
    )
    _write(
        ws / "tasks/work.py",
        "\n".join(
            [
                "CG_TASK = {",
                "    'inputs': {},",
                "    'outputs': {'interface': 'artifacts/work/interface.json'},",
                "    'interface_output': 'interface',",
                "}",
                "from pathlib import Path",
                "import json",
                "import time",
                "from claimguard.progress import update",
                "def main() -> int:",
                "    root = Path.cwd()",
                "    for i in range(3):",
                "        update(done=i + 1, total=3, message=f'step {i + 1}', force=True)",
                "        time.sleep(0.01)",
                "    out = root / 'artifacts/work'",
                "    out.mkdir(parents=True, exist_ok=True)",
                "    (out / 'interface.json').write_text(json.dumps({'status':'ok'}), encoding='utf-8')",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
        + "\n",
    )

    runner = PipelineRunner(ws / "claimguard.json")
    events: list[dict[str, object]] = []
    report = runner.run(max_workers=1, event_emitter=events.append)
    rows = {str(row["task"]): str(row["status"]) for row in report["task_rows"]}
    assert rows["work"] == "ok"

    progress_events = [
        e for e in events if str(e.get("event", "")) == "task_progress" and str(e.get("task", "")) == "work"
    ]
    assert progress_events
    latest = progress_events[-1]
    assert int(latest["done"]) == 3
    assert int(latest["total"]) == 3
    assert float(latest["fraction"]) == 1.0
    assert str(latest["message"]) == "step 3"
