from __future__ import annotations

import json
from pathlib import Path

from claimguard.policy import classify_claim
from claimguard.runner import PipelineRunner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_task(workspace: Path, *, name: str, status: str, claim_blocking: bool) -> None:
    cg_task = {
        "inputs": [],
        "outputs": [f"artifacts/{name}/interface.json"],
        "interface_output": f"artifacts/{name}/interface.json",
        "claim_blocking": claim_blocking,
        "gates": [],
    }
    lines = [
        f"CG_TASK = {repr(cg_task)}",
        "from pathlib import Path",
        "import json",
        "",
        "if __name__ == '__main__':",
        f"    out = Path('artifacts/{name}')",
        "    out.mkdir(parents=True, exist_ok=True)",
        f"    (out / 'interface.json').write_text(json.dumps({{'status': '{status}', 'task': '{name}'}}), encoding='utf-8')",
        "",
    ]
    _write(workspace / f"tasks/{name}.py", "\n".join(lines))


def _write_contract(workspace: Path, name: str = "claim_policy") -> Path:
    path = workspace / "claimguard.json"
    _write(path, json.dumps({"pipeline_name": name, "task_roots": ["tasks"]}, indent=2) + "\n")
    return path


def test_policy_matrix() -> None:
    assert classify_claim({}).claim_class == "blocked"
    assert classify_claim({"a": "ok", "b": "replay_ok"}).claim_class == "contract-certified"
    d = classify_claim({"a": "diagnostic_only", "b": "ok"})
    assert d.claim_class == "diagnostic"
    assert d.claim_reason == "scope_diagnostic"
    b = classify_claim({"a": "blocked", "b": "diagnostic_only"})
    assert b.claim_class == "blocked"
    assert b.claim_reason == "scope_blocked"
    assert "a" in b.blockers


def test_claim_class_diagnostic_when_claim_scope_has_diagnostic(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    contract = _write_contract(ws)
    _write_task(ws, name="diag", status="diagnostic_only", claim_blocking=True)

    report = PipelineRunner(contract).run()
    claim = report["claim"]
    assert claim["claim_class"] == "diagnostic"
    assert claim["claim_reason"] == "scope_diagnostic"
    assert claim["claim_diagnostics"] == ["diag"]


def test_non_claim_diagnostic_does_not_downgrade_certified_claim(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    contract = _write_contract(ws)
    _write_task(ws, name="diag", status="diagnostic_only", claim_blocking=False)
    _write_task(ws, name="core", status="ok", claim_blocking=True)

    report = PipelineRunner(contract).run()
    claim = report["claim"]
    assert claim["claim_class"] == "contract-certified"
    assert claim["claim_reason"] == "scope_certified"
    assert claim["claim_blocking_tasks"] == ["core"]
    assert claim["claim_diagnostics"] == []


def test_target_scope_includes_targets_for_claim_class(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    contract = _write_contract(ws)
    _write_task(ws, name="diag", status="diagnostic_only", claim_blocking=False)
    _write_task(ws, name="core", status="ok", claim_blocking=True)

    report = PipelineRunner(contract).run(targets=["diag"])
    claim = report["claim"]
    assert claim["target_tasks"] == ["diag"]
    assert claim["claim_scope_tasks"] == ["diag"]
    assert claim["claim_class"] == "diagnostic"
    assert claim["claim_reason"] == "scope_diagnostic"
