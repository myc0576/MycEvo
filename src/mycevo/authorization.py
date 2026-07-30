"""Canonical plan and execution authorization records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .workflow_ir import canonical_sha256


class AuthorizationError(ValueError):
    pass


def plan_digest(plan: Mapping[str, Any]) -> str:
    return f"sha256:{canonical_sha256(dict(plan))}"


@dataclass(frozen=True)
class AuthorizationRecord:
    authorization_id: str
    plan_id: str
    plan_digest: str
    plugin_id: str
    plugin_version: str
    plugin_digest: str
    policy_digest: str
    principal: Mapping[str, str]
    capabilities: Mapping[str, Any]
    authorization_mode: str
    auth_strength: str
    issued_at: str
    expires_at: str

    def as_dict(self) -> dict[str, Any]:
        return {"schema": "mycevo.authorization_record.v1", **self.__dict__}

    def validate_for(self, plan: Mapping[str, Any], *, now: datetime | None = None) -> None:
        if self.plan_digest != plan_digest(plan):
            raise AuthorizationError("authorization plan_digest does not match plan")
        current = now or datetime.now(timezone.utc)
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AuthorizationError("invalid authorization expiry") from exc
        if expiry <= current:
            raise AuthorizationError("authorization record is expired")


def issue_self_authorization(*, authorization_id: str, plan_id: str, plan: Mapping[str, Any], plugin_id: str, plugin_version: str, plugin_digest: str, policy_digest: str, principal_id: str, capabilities: Mapping[str, Any], issued_at: str, expires_at: str) -> AuthorizationRecord:
    return AuthorizationRecord(
        authorization_id=authorization_id, plan_id=plan_id, plan_digest=plan_digest(plan),
        plugin_id=plugin_id, plugin_version=plugin_version, plugin_digest=plugin_digest,
        policy_digest=policy_digest, principal={"type": "local_user", "id": principal_id},
        capabilities=capabilities, authorization_mode="local_cli_self_authorization",
        auth_strength="self_asserted", issued_at=issued_at, expires_at=expires_at,
    )
