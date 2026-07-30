import pytest

from mycevo.result_status import ResultStatusError, require_human_gate


def test_execution_stays_unverified_without_promotion() -> None:
    assert require_human_gate("executed_unverified") == "executed_unverified"
    with pytest.raises(ResultStatusError):
        require_human_gate("paper_ready")


def test_promotion_requires_human_decision_and_evidence() -> None:
    assert require_human_gate("validated", human_decision="validated", evidence_complete=True) == "validated"
    with pytest.raises(ResultStatusError):
        require_human_gate("validated", human_decision="validated", evidence_complete=False)
