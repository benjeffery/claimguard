"""Claim classification policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


CERTIFIABLE_STATUSES = {"ok", "replay_ok"}
DIAGNOSTIC_STATUSES = {"diagnostic_only"}
BLOCKING_STATUSES = {"blocked", "error", "stale", "missing"}
KNOWN_STATUSES = CERTIFIABLE_STATUSES | DIAGNOSTIC_STATUSES | BLOCKING_STATUSES


@dataclass(frozen=True)
class ClaimDecision:
    claim_class: str
    claim_reason: str
    blockers: tuple[str, ...]
    diagnostics: tuple[str, ...]


def classify_claim(scope_statuses: Mapping[str, str]) -> ClaimDecision:
    if not scope_statuses:
        return ClaimDecision(
            claim_class="blocked",
            claim_reason="empty_scope",
            blockers=(),
            diagnostics=(),
        )

    blockers = sorted(
        task
        for task, status in scope_statuses.items()
        if status in BLOCKING_STATUSES or status not in KNOWN_STATUSES
    )
    if blockers:
        return ClaimDecision(
            claim_class="blocked",
            claim_reason="scope_blocked",
            blockers=tuple(blockers),
            diagnostics=(),
        )

    diagnostics = sorted(task for task, status in scope_statuses.items() if status in DIAGNOSTIC_STATUSES)
    if diagnostics:
        return ClaimDecision(
            claim_class="diagnostic",
            claim_reason="scope_diagnostic",
            blockers=(),
            diagnostics=tuple(diagnostics),
        )

    if all(status in CERTIFIABLE_STATUSES for status in scope_statuses.values()):
        return ClaimDecision(
            claim_class="contract-certified",
            claim_reason="scope_certified",
            blockers=(),
            diagnostics=(),
        )

    return ClaimDecision(
        claim_class="blocked",
        claim_reason="scope_unknown",
        blockers=(),
        diagnostics=(),
    )
