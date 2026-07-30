from pathlib import Path

import pytest

from mycevo.audit_chain import AuditChainError, append_record, verify_chain


def test_authorization_chain_appends_and_verifies(tmp_path: Path) -> None:
    path = tmp_path / "authorization.jsonl"
    append_record(path, {"authorization_id": "a1", "plan_digest": "sha256:" + "a" * 64})
    append_record(path, {"authorization_id": "a2", "plan_digest": "sha256:" + "b" * 64})
    result = verify_chain(path)
    assert result["ok"] is True
    assert result["count"] == 2


def test_authorization_chain_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "authorization.jsonl"
    append_record(path, {"authorization_id": "a1"})
    path.write_text(path.read_text(encoding="utf-8").replace("a1", "tampered"), encoding="utf-8")
    with pytest.raises(AuditChainError):
        verify_chain(path)
