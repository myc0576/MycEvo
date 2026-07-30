from datetime import datetime, timedelta, timezone

import pytest

from mycevo.authorization import AuthorizationError, issue_self_authorization


def test_authorization_binds_canonical_plan_digest() -> None:
    now = datetime.now(timezone.utc)
    record = issue_self_authorization(
        authorization_id="auth-1", plan_id="plan-1", plan={"command": ["python"], "inputs": ["a"]},
        plugin_id="paperframes-reference", plugin_version="0.1.0", plugin_digest="sha256:" + "a" * 64,
        policy_digest="sha256:" + "b" * 64, principal_id="user", capabilities={"network": False},
        issued_at=now.isoformat(), expires_at=(now + timedelta(minutes=5)).isoformat(),
    )
    record.validate_for({"inputs": ["a"], "command": ["python"]}, now=now)
    with pytest.raises(AuthorizationError, match="does not match"):
        record.validate_for({"inputs": ["changed"], "command": ["python"]}, now=now)


def test_expired_authorization_is_rejected() -> None:
    now = datetime.now(timezone.utc)
    record = issue_self_authorization(
        authorization_id="auth-1", plan_id="plan-1", plan={}, plugin_id="p", plugin_version="0.1.0",
        plugin_digest="sha256:" + "a" * 64, policy_digest="sha256:" + "b" * 64, principal_id="user",
        capabilities={}, issued_at=now.isoformat(), expires_at=(now - timedelta(seconds=1)).isoformat(),
    )
    with pytest.raises(AuthorizationError, match="expired"):
        record.validate_for({}, now=now)
