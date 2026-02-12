from __future__ import annotations

import json
from pathlib import Path

from claimguard.cli import main


def test_cli_report_and_doctor_commands(capsys) -> None:
    contract = Path("examples/minimal-example/claimguard.json").resolve()
    assert contract.exists()

    rc = main(["run", "--contract", str(contract), "--clean-state", "--llm-output"])
    assert rc == 0

    rc = main(["report", "--contract", str(contract)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "claim_class:" in out
    assert "status_counts:" in out
    assert "policy_exemptions_count:" in out
    assert "claim_certificate_json:" in out

    cert_path = contract.parent / ".claimguard" / "reports" / "claim_certificate_latest.json"
    assert cert_path.exists()
    cert = json.loads(cert_path.read_text(encoding="utf-8"))
    assert cert["artifact"] == "claimguard_claim_certificate"

    rc = main(["doctor", "--contract", str(contract)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "doctor: ok" in out
