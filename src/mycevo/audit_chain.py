"""Append-only authorization audit chain and signer checkpoint contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .workflow_ir import canonical_sha256


class AuditChainError(ValueError):
    pass


class CheckpointSigner(Protocol):
    signer_type: str

    def sign(self, payload: bytes) -> str: ...
    def verify(self, payload: bytes, signature: str) -> bool: ...


@dataclass(frozen=True)
class Checkpoint:
    workspace_id: str
    last_sequence: int
    log_head: str
    created_at: str
    signer_type: str
    signature: str | None = None

    def payload(self) -> bytes:
        return json.dumps({"workspace_id": self.workspace_id, "last_sequence": self.last_sequence, "log_head": self.log_head, "created_at": self.created_at}, sort_keys=True, separators=(",", ":")).encode()


def append_record(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append one canonical record, refusing malformed chain continuity."""
    previous = "sha256:" + "0" * 64
    sequence = 1
    if path.exists():
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            last = json.loads(lines[-1])
            previous = last.get("record_hash", "")
            sequence = int(last.get("sequence", 0)) + 1
            if not previous.startswith("sha256:"):
                raise AuditChainError("invalid previous record hash")
    item = {"sequence": sequence, "previous_record_hash": previous, **record}
    item["record_hash"] = "sha256:" + canonical_sha256(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return item


def verify_chain(path: Path) -> dict[str, Any]:
    previous = "sha256:" + "0" * 64
    count = 0
    if not path.exists():
        return {"ok": True, "count": 0, "head": previous}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("sequence") != count + 1 or item.get("previous_record_hash") != previous:
            raise AuditChainError("authorization audit chain is discontinuous")
        expected = item.pop("record_hash", None)
        if expected != "sha256:" + canonical_sha256(item):
            raise AuditChainError("authorization audit record hash mismatch")
        previous = expected
        count += 1
    return {"ok": True, "count": count, "head": previous}
