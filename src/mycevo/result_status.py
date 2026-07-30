"""Fail-closed result status transitions."""

from __future__ import annotations


class ResultStatusError(ValueError):
    pass


EXECUTED_UNVERIFIED = "executed_unverified"
PROMOTION_STATUSES = {"validated", "approved", "reusable", "pass", "paper_ready"}


def require_human_gate(status: str, *, human_decision: str | None = None, evidence_complete: bool = False) -> str:
    if status == EXECUTED_UNVERIFIED:
        return status
    if status in PROMOTION_STATUSES and human_decision in {"approved", "validated"} and evidence_complete:
        return status
    raise ResultStatusError("promotion requires explicit human decision and complete evidence")
