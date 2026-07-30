"""Deterministic, immutable ChangeSet validation and materialization.

This module accepts data-only external proposals.  It never executes workflow
nodes, evaluates candidates, records authoritative decisions, writes files, or
moves a canonical pointer.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from .diagnostics import DiagnosticIssue, DiagnosticPolicy, ORDERING_EDGE_KINDS, validate_graph
from .workflow_ir import (
    EdgeKind,
    GraphLineage,
    MethodEdge,
    MethodGraphVersion,
    MethodModule,
    TaskContract,
    canonical_json_bytes,
)


EXTERNAL_PROPOSAL_SCHEMA = "mycevo.external_proposal.v1"
CHANGESET_SCHEMA = "mycevo.evolution_changeset.v1"
SELECTION_SCHEMA = "mycevo.selection.v1"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ChangeSetError(ValueError):
    """Stable fail-closed rejection from the pure ChangeSet core."""

    def __init__(self, code: str, message: str, *, delta_ids: tuple[str, ...] = ()) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.delta_ids = tuple(sorted(set(delta_ids)))


class DeltaCategory(str, Enum):
    TOPOLOGY = "topology"
    CONTENT = "content"
    RULES = "rules"
    MEMORY = "memory"


class DeltaOperation(str, Enum):
    ADD = "add"
    DELETE = "delete"
    MERGE = "merge"
    SPLIT = "split"
    REPLACE = "replace"
    REORDER = "reorder"


class Disposition(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class SelectionStatus(str, Enum):
    READY = "ready"
    NO_CHANGE = "no_change"


class ObjectKind(str, Enum):
    MODULE = "module"
    EDGE = "edge"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChangeSetStatus(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    INVALID = "invalid"
    UNDER_SELECTION = "under_selection"
    CLOSURE_MATERIALIZED = "closure_materialized"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class DeltaStatus(str, Enum):
    PROPOSED = "proposed"
    STRUCTURALLY_VALID = "structurally_valid"
    INVALID = "invalid"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    DEPENDENCY_CLOSED = "dependency_closed"
    CONFLICT_BLOCKED = "conflict_blocked"
    CANDIDATE_MATERIALIZED = "candidate_materialized"
    CANONICALIZED = "canonicalized"
    SUPERSEDED = "superseded"


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ChangeSetError("CS_SCHEMA_INVALID", f"{field_name} must be a portable identifier")
    return value


def _nonempty(value: str, field_name: str, *, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ChangeSetError("CS_SCHEMA_INVALID", f"{field_name} must be non-empty and at most {maximum} characters")
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def object_content_hash(value: MethodModule | MethodEdge) -> str:
    """Hash one complete immutable graph object using canonical JSON."""
    if not isinstance(value, (MethodModule, MethodEdge)):
        raise ChangeSetError("CS_SCHEMA_INVALID", "only MethodModule and MethodEdge objects can be hashed")
    return _hash(value)


def _strict_keys(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ChangeSetError("CS_SCHEMA_INVALID", f"{name} contains unsupported fields: {sorted(unknown)}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChangeSetError("CS_SCHEMA_INVALID", f"proposal contains duplicate object key: {key!r}")
        result[key] = value
    return result


def _sorted_unique(values: tuple[str, ...], field_name: str, *, identifiers: bool = True) -> tuple[str, ...]:
    normalized = tuple(values)
    if len(set(normalized)) != len(normalized):
        raise ChangeSetError("CS_SCHEMA_INVALID", f"{field_name} must contain unique values")
    for value in normalized:
        (_identifier if identifiers else _nonempty)(value, field_name)
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class ObjectExpectation:
    kind: ObjectKind
    object_id: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ObjectKind(self.kind))
        _identifier(self.object_id, "expectation.object_id")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ChangeSetError("CS_SCHEMA_INVALID", "expectation.sha256 must be lowercase SHA-256")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "object_id": self.object_id, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObjectExpectation":
        _strict_keys(value, {"kind", "object_id", "sha256"}, "ObjectExpectation")
        try:
            return cls(ObjectKind(value["kind"]), value["object_id"], value["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ChangeSetError):
                raise
            raise ChangeSetError("CS_SCHEMA_INVALID", "invalid ObjectExpectation") from exc


@dataclass(frozen=True, slots=True)
class BoundaryPatch:
    expected_entry_module_ids: tuple[str, ...] = ()
    result_entry_module_ids: tuple[str, ...] = ()
    expected_exit_module_ids: tuple[str, ...] = ()
    result_exit_module_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "expected_entry_module_ids",
            "result_entry_module_ids",
            "expected_exit_module_ids",
            "result_exit_module_ids",
        ):
            object.__setattr__(self, name, _sorted_unique(tuple(getattr(self, name)), f"boundary.{name}"))
        if not self.result_entry_module_ids or not self.result_exit_module_ids:
            raise ChangeSetError("DELTA_STRUCTURE_INVALID", "boundary results must keep non-empty graph entries and exits")

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_entry_module_ids": list(self.expected_entry_module_ids),
            "result_entry_module_ids": list(self.result_entry_module_ids),
            "expected_exit_module_ids": list(self.expected_exit_module_ids),
            "result_exit_module_ids": list(self.result_exit_module_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BoundaryPatch":
        allowed = {
            "expected_entry_module_ids", "result_entry_module_ids",
            "expected_exit_module_ids", "result_exit_module_ids",
        }
        _strict_keys(value, allowed, "BoundaryPatch")
        try:
            return cls(**{name: tuple(value.get(name, ())) for name in allowed})
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ChangeSetError):
                raise
            raise ChangeSetError("CS_SCHEMA_INVALID", "invalid BoundaryPatch") from exc


@dataclass(frozen=True, slots=True)
class GraphPatch:
    expectations: tuple[ObjectExpectation, ...] = ()
    remove_module_ids: tuple[str, ...] = ()
    remove_edge_ids: tuple[str, ...] = ()
    put_modules: tuple[MethodModule, ...] = ()
    put_edges: tuple[MethodEdge, ...] = ()
    boundary: BoundaryPatch | None = None

    def __post_init__(self) -> None:
        expectations = tuple(sorted(tuple(self.expectations), key=lambda item: (item.kind.value, item.object_id)))
        if not all(isinstance(item, ObjectExpectation) for item in expectations):
            raise ChangeSetError("CS_SCHEMA_INVALID", "patch expectations must be ObjectExpectation values")
        expectation_keys = tuple((item.kind, item.object_id) for item in expectations)
        if len(set(expectation_keys)) != len(expectation_keys):
            raise ChangeSetError("CS_SCHEMA_INVALID", "patch expectations must be unique by kind and ID")
        object.__setattr__(self, "expectations", expectations)
        object.__setattr__(self, "remove_module_ids", _sorted_unique(tuple(self.remove_module_ids), "patch.remove_module_ids"))
        object.__setattr__(self, "remove_edge_ids", _sorted_unique(tuple(self.remove_edge_ids), "patch.remove_edge_ids"))
        modules = tuple(sorted(tuple(self.put_modules), key=lambda item: item.module_id))
        edges = tuple(sorted(tuple(self.put_edges), key=lambda item: item.edge_id))
        if not all(isinstance(item, MethodModule) for item in modules):
            raise ChangeSetError("CS_SCHEMA_INVALID", "patch.put_modules must contain MethodModule values")
        if not all(isinstance(item, MethodEdge) for item in edges):
            raise ChangeSetError("CS_SCHEMA_INVALID", "patch.put_edges must contain MethodEdge values")
        if len({item.module_id for item in modules}) != len(modules):
            raise ChangeSetError("CS_SCHEMA_INVALID", "patch.put_modules contains duplicate IDs")
        if len({item.edge_id for item in edges}) != len(edges):
            raise ChangeSetError("CS_SCHEMA_INVALID", "patch.put_edges contains duplicate IDs")
        object.__setattr__(self, "put_modules", modules)
        object.__setattr__(self, "put_edges", edges)
        if self.boundary is not None and not isinstance(self.boundary, BoundaryPatch):
            raise ChangeSetError("CS_SCHEMA_INVALID", "patch.boundary must be a BoundaryPatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectations": [item.to_dict() for item in self.expectations],
            "remove_module_ids": list(self.remove_module_ids),
            "remove_edge_ids": list(self.remove_edge_ids),
            "put_modules": [item.to_dict() for item in self.put_modules],
            "put_edges": [item.to_dict() for item in self.put_edges],
            "boundary": None if self.boundary is None else self.boundary.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GraphPatch":
        allowed = {"expectations", "remove_module_ids", "remove_edge_ids", "put_modules", "put_edges", "boundary"}
        _strict_keys(value, allowed, "GraphPatch")
        try:
            boundary = value.get("boundary")
            return cls(
                expectations=tuple(ObjectExpectation.from_dict(item) for item in value.get("expectations", ())),
                remove_module_ids=tuple(value.get("remove_module_ids", ())),
                remove_edge_ids=tuple(value.get("remove_edge_ids", ())),
                put_modules=tuple(MethodModule.from_dict(item) for item in value.get("put_modules", ())),
                put_edges=tuple(MethodEdge.from_dict(item) for item in value.get("put_edges", ())),
                boundary=None if boundary is None else BoundaryPatch.from_dict(boundary),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ChangeSetError):
                raise
            raise ChangeSetError("CS_SCHEMA_INVALID", "invalid GraphPatch") from exc


@dataclass(frozen=True, slots=True)
class ProposalProvenance:
    source_kind: str
    source_id: str
    source_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    confidence: Confidence = Confidence.MEDIUM
    falsification_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.source_kind, "provenance.source_kind")
        _identifier(self.source_id, "provenance.source_id")
        object.__setattr__(self, "source_refs", _sorted_unique(tuple(self.source_refs), "provenance.source_refs", identifiers=False))
        object.__setattr__(self, "evidence_refs", _sorted_unique(tuple(self.evidence_refs), "provenance.evidence_refs", identifiers=False))
        object.__setattr__(self, "confidence", Confidence(self.confidence))
        object.__setattr__(self, "falsification_conditions", _sorted_unique(
            tuple(self.falsification_conditions), "provenance.falsification_conditions", identifiers=False,
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "source_refs": list(self.source_refs),
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence.value,
            "falsification_conditions": list(self.falsification_conditions),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProposalProvenance":
        allowed = {"source_kind", "source_id", "source_refs", "evidence_refs", "confidence", "falsification_conditions"}
        _strict_keys(value, allowed, "ProposalProvenance")
        try:
            return cls(
                source_kind=value["source_kind"],
                source_id=value["source_id"],
                source_refs=tuple(value.get("source_refs", ())),
                evidence_refs=tuple(value.get("evidence_refs", ())),
                confidence=Confidence(value.get("confidence", "medium")),
                falsification_conditions=tuple(value.get("falsification_conditions", ())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ChangeSetError):
                raise
            raise ChangeSetError("CS_SCHEMA_INVALID", "invalid ProposalProvenance") from exc


@dataclass(frozen=True, slots=True)
class EvolutionDelta:
    delta_id: str
    category: DeltaCategory
    operation: DeltaOperation
    patch: GraphPatch
    rationale: str
    depends_on: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    falsification_conditions: tuple[str, ...] = ()
    evaluator_requirement_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.delta_id, "delta.delta_id")
        object.__setattr__(self, "category", DeltaCategory(self.category))
        object.__setattr__(self, "operation", DeltaOperation(self.operation))
        if not isinstance(self.patch, GraphPatch):
            raise ChangeSetError("DELTA_SCHEMA_INVALID", "delta.patch must be a GraphPatch", delta_ids=(self.delta_id,))
        _nonempty(self.rationale, "delta.rationale")
        object.__setattr__(self, "depends_on", _sorted_unique(tuple(self.depends_on), "delta.depends_on"))
        object.__setattr__(self, "conflicts_with", _sorted_unique(tuple(self.conflicts_with), "delta.conflicts_with"))
        object.__setattr__(self, "evidence_refs", _sorted_unique(tuple(self.evidence_refs), "delta.evidence_refs", identifiers=False))
        object.__setattr__(self, "falsification_conditions", _sorted_unique(
            tuple(self.falsification_conditions), "delta.falsification_conditions", identifiers=False,
        ))
        object.__setattr__(self, "evaluator_requirement_refs", _sorted_unique(
            tuple(self.evaluator_requirement_refs), "delta.evaluator_requirement_refs",
        ))
        if self.delta_id in self.depends_on or self.delta_id in self.conflicts_with:
            raise ChangeSetError("DELTA_SCHEMA_INVALID", "delta cannot depend on or conflict with itself", delta_ids=(self.delta_id,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "category": self.category.value,
            "operation": self.operation.value,
            "patch": self.patch.to_dict(),
            "rationale": self.rationale,
            "depends_on": list(self.depends_on),
            "conflicts_with": list(self.conflicts_with),
            "evidence_refs": list(self.evidence_refs),
            "falsification_conditions": list(self.falsification_conditions),
            "evaluator_requirement_refs": list(self.evaluator_requirement_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvolutionDelta":
        allowed = {
            "delta_id", "category", "operation", "patch", "rationale", "depends_on", "conflicts_with",
            "evidence_refs", "falsification_conditions", "evaluator_requirement_refs",
        }
        _strict_keys(value, allowed, "EvolutionDelta")
        try:
            return cls(
                delta_id=value["delta_id"],
                category=DeltaCategory(value["category"]),
                operation=DeltaOperation(value["operation"]),
                patch=GraphPatch.from_dict(value["patch"]),
                rationale=value["rationale"],
                depends_on=tuple(value.get("depends_on", ())),
                conflicts_with=tuple(value.get("conflicts_with", ())),
                evidence_refs=tuple(value.get("evidence_refs", ())),
                falsification_conditions=tuple(value.get("falsification_conditions", ())),
                evaluator_requirement_refs=tuple(value.get("evaluator_requirement_refs", ())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ChangeSetError):
                raise
            raise ChangeSetError("DELTA_SCHEMA_INVALID", "invalid EvolutionDelta") from exc


@dataclass(frozen=True, slots=True)
class ExternalProposal:
    proposal_id: str
    base_graph_hash: str
    provenance: ProposalProvenance
    deltas: tuple[EvolutionDelta, ...]
    schema: str = field(default=EXTERNAL_PROPOSAL_SCHEMA, init=False)

    def __post_init__(self) -> None:
        _identifier(self.proposal_id, "proposal.proposal_id")
        if not isinstance(self.base_graph_hash, str) or not _SHA256.fullmatch(self.base_graph_hash):
            raise ChangeSetError("CS_SCHEMA_INVALID", "proposal.base_graph_hash must be lowercase SHA-256")
        if not isinstance(self.provenance, ProposalProvenance):
            raise ChangeSetError("CS_SCHEMA_INVALID", "proposal.provenance must be ProposalProvenance")
        deltas = tuple(sorted(tuple(self.deltas), key=lambda item: item.delta_id))
        if not deltas or not all(isinstance(item, EvolutionDelta) for item in deltas):
            raise ChangeSetError("CS_SCHEMA_INVALID", "proposal requires at least one EvolutionDelta")
        if len({item.delta_id for item in deltas}) != len(deltas):
            raise ChangeSetError("CS_SCHEMA_INVALID", "proposal delta IDs must be unique")
        object.__setattr__(self, "deltas", deltas)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "proposal_id": self.proposal_id,
            "base_graph_hash": self.base_graph_hash,
            "provenance": self.provenance.to_dict(),
            "deltas": [item.to_dict() for item in self.deltas],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalProposal":
        _strict_keys(value, {"schema", "proposal_id", "base_graph_hash", "provenance", "deltas"}, "ExternalProposal")
        if value.get("schema") != EXTERNAL_PROPOSAL_SCHEMA:
            raise ChangeSetError("CS_SCHEMA_INVALID", "unsupported external proposal schema")
        try:
            return cls(
                proposal_id=value["proposal_id"],
                base_graph_hash=value["base_graph_hash"],
                provenance=ProposalProvenance.from_dict(value["provenance"]),
                deltas=tuple(EvolutionDelta.from_dict(item) for item in value["deltas"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ChangeSetError):
                raise
            raise ChangeSetError("CS_SCHEMA_INVALID", "invalid ExternalProposal") from exc


@dataclass(frozen=True, slots=True)
class ChangeSetBudget:
    max_payload_bytes: int = 1_000_000
    max_deltas: int = 64
    max_dependencies: int = 256
    max_conflicts: int = 256
    max_dependency_depth: int = 16
    max_modules_added: int = 64
    max_modules_removed: int = 64
    max_edges_added: int = 256
    max_edges_removed: int = 256
    max_touched_objects: int = 512
    max_result_modules: int = 10_000
    max_result_edges: int = 100_000
    max_analysis_steps: int = 5_000_000

    def __post_init__(self) -> None:
        positive = {"max_payload_bytes", "max_deltas", "max_dependency_depth", "max_result_modules", "max_analysis_steps"}
        for name in self.to_dict():
            value = getattr(self, name)
            minimum = 1 if name in positive else 0
            if type(value) is not int or not minimum <= value <= 1_000_000_000:
                raise ChangeSetError("CS_SCHEMA_INVALID", f"budget.{name} must be an integer from {minimum} to 1000000000")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_payload_bytes": self.max_payload_bytes,
            "max_deltas": self.max_deltas,
            "max_dependencies": self.max_dependencies,
            "max_conflicts": self.max_conflicts,
            "max_dependency_depth": self.max_dependency_depth,
            "max_modules_added": self.max_modules_added,
            "max_modules_removed": self.max_modules_removed,
            "max_edges_added": self.max_edges_added,
            "max_edges_removed": self.max_edges_removed,
            "max_touched_objects": self.max_touched_objects,
            "max_result_modules": self.max_result_modules,
            "max_result_edges": self.max_result_edges,
            "max_analysis_steps": self.max_analysis_steps,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChangeSetBudget":
        allowed = set(cls().to_dict())
        _strict_keys(value, allowed, "ChangeSetBudget")
        try:
            return cls(**{name: value.get(name, getattr(cls(), name)) for name in allowed})
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ChangeSetError):
                raise
            raise ChangeSetError("CS_SCHEMA_INVALID", "invalid ChangeSetBudget") from exc


@dataclass(frozen=True, slots=True)
class ChangeSetPolicy:
    budget: ChangeSetBudget = field(default_factory=ChangeSetBudget)
    allowed_operations: tuple[DeltaOperation, ...] = tuple(DeltaOperation)

    def __post_init__(self) -> None:
        if not isinstance(self.budget, ChangeSetBudget):
            raise ChangeSetError("CS_SCHEMA_INVALID", "policy.budget must be ChangeSetBudget")
        operations = tuple(sorted((DeltaOperation(item) for item in self.allowed_operations), key=lambda item: item.value))
        if not operations or len(set(operations)) != len(operations):
            raise ChangeSetError("CS_SCHEMA_INVALID", "policy.allowed_operations must be non-empty and unique")
        object.__setattr__(self, "allowed_operations", operations)

    def to_dict(self) -> dict[str, Any]:
        return _policy_payload(self.budget, self.allowed_operations)

    @property
    def content_hash(self) -> str:
        return _hash(self.to_dict())


def _policy(value: ChangeSetPolicy | ChangeSetBudget | None) -> ChangeSetPolicy:
    if value is None:
        return ChangeSetPolicy()
    if isinstance(value, ChangeSetBudget):
        return ChangeSetPolicy(value)
    if isinstance(value, ChangeSetPolicy):
        return value
    raise ChangeSetError("CS_SCHEMA_INVALID", "policy must be ChangeSetPolicy or ChangeSetBudget")


def _policy_payload(
    budget: ChangeSetBudget,
    allowed_operations: tuple[DeltaOperation, ...],
) -> dict[str, Any]:
    return {
        "allowed_operations": sorted(item.value for item in allowed_operations),
        "effective_budget": budget.to_dict(),
    }


def _bound_policy_hash(
    budget: ChangeSetBudget,
    allowed_operations: tuple[DeltaOperation, ...],
) -> str:
    return _hash(_policy_payload(budget, allowed_operations))


@dataclass(frozen=True, slots=True)
class EvolutionChangeSet:
    change_set_id: str
    base_graph_hash: str
    provenance: ProposalProvenance
    deltas: tuple[EvolutionDelta, ...]
    effective_budget: ChangeSetBudget
    allowed_operations: tuple[DeltaOperation, ...]
    policy_hash: str
    schema: str = field(default=CHANGESET_SCHEMA, init=False)

    def __post_init__(self) -> None:
        _identifier(self.change_set_id, "changeset.change_set_id")
        if not isinstance(self.base_graph_hash, str) or not _SHA256.fullmatch(self.base_graph_hash):
            raise ChangeSetError("CS_SCHEMA_INVALID", "changeset.base_graph_hash must be lowercase SHA-256")
        if not isinstance(self.provenance, ProposalProvenance) or not isinstance(self.effective_budget, ChangeSetBudget):
            raise ChangeSetError("CS_SCHEMA_INVALID", "changeset provenance and effective budget must be typed")
        deltas = tuple(sorted(tuple(self.deltas), key=lambda item: item.delta_id))
        if not deltas or not all(isinstance(item, EvolutionDelta) for item in deltas):
            raise ChangeSetError("CS_SCHEMA_INVALID", "changeset requires typed deltas")
        if len({item.delta_id for item in deltas}) != len(deltas):
            raise ChangeSetError("CS_SCHEMA_INVALID", "changeset delta IDs must be unique")
        object.__setattr__(self, "deltas", deltas)
        try:
            operations = tuple(sorted(
                (DeltaOperation(item) for item in self.allowed_operations),
                key=lambda item: item.value,
            ))
        except (TypeError, ValueError) as exc:
            raise ChangeSetError("CS_SCHEMA_INVALID", "changeset.allowed_operations must be typed") from exc
        if not operations or len(set(operations)) != len(operations):
            raise ChangeSetError("CS_SCHEMA_INVALID", "changeset.allowed_operations must be non-empty and unique")
        object.__setattr__(self, "allowed_operations", operations)
        if not isinstance(self.policy_hash, str) or not _SHA256.fullmatch(self.policy_hash):
            raise ChangeSetError("CS_SCHEMA_INVALID", "changeset.policy_hash must be lowercase SHA-256")
        if self.policy_hash != _bound_policy_hash(self.effective_budget, operations):
            raise ChangeSetError("CS_POLICY_MISMATCH", "ChangeSet policy hash does not match its embedded policy")
        if any(delta.operation not in operations for delta in deltas):
            raise ChangeSetError("CS_OPERATION_FORBIDDEN", "ChangeSet contains an operation forbidden by its bound policy")

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "change_set_id": self.change_set_id,
            "base_graph_hash": self.base_graph_hash,
            "provenance": self.provenance.to_dict(),
            "deltas": [item.to_dict() for item in self.deltas],
            "effective_budget": self.effective_budget.to_dict(),
            "allowed_operations": [item.value for item in self.allowed_operations],
            "policy_hash": self.policy_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvolutionChangeSet":
        allowed = {
            "schema", "change_set_id", "base_graph_hash", "provenance", "deltas",
            "effective_budget", "allowed_operations", "policy_hash",
        }
        _strict_keys(value, allowed, "EvolutionChangeSet")
        if value.get("schema") != CHANGESET_SCHEMA:
            raise ChangeSetError("CS_SCHEMA_INVALID", "unsupported EvolutionChangeSet schema")
        try:
            return cls(
                change_set_id=value["change_set_id"],
                base_graph_hash=value["base_graph_hash"],
                provenance=ProposalProvenance.from_dict(value["provenance"]),
                deltas=tuple(EvolutionDelta.from_dict(item) for item in value["deltas"]),
                effective_budget=ChangeSetBudget.from_dict(value["effective_budget"]),
                allowed_operations=tuple(DeltaOperation(item) for item in value["allowed_operations"]),
                policy_hash=value["policy_hash"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ChangeSetError):
                raise
            raise ChangeSetError("CS_SCHEMA_INVALID", "invalid EvolutionChangeSet") from exc


def _validate_bound_policy(changeset: EvolutionChangeSet) -> None:
    expected = _bound_policy_hash(changeset.effective_budget, changeset.allowed_operations)
    if changeset.policy_hash != expected:
        raise ChangeSetError("CS_POLICY_MISMATCH", "ChangeSet embedded policy no longer matches policy_hash")
    if any(delta.operation not in changeset.allowed_operations for delta in changeset.deltas):
        raise ChangeSetError("CS_OPERATION_FORBIDDEN", "ChangeSet contains an operation forbidden by its bound policy")


@dataclass(frozen=True, slots=True)
class DeltaDisposition:
    delta_id: str
    disposition: Disposition

    def __post_init__(self) -> None:
        _identifier(self.delta_id, "disposition.delta_id")
        object.__setattr__(self, "disposition", Disposition(self.disposition))

    def to_dict(self) -> dict[str, Any]:
        return {"delta_id": self.delta_id, "disposition": self.disposition.value}


@dataclass(frozen=True, slots=True)
class SelectionResolution:
    change_set_hash: str
    dispositions: tuple[DeltaDisposition, ...]
    closure_delta_ids: tuple[str, ...]
    application_order: tuple[str, ...]
    selection_hash: str
    status: SelectionStatus

    def __post_init__(self) -> None:
        for name in ("change_set_hash", "selection_hash"):
            if not isinstance(getattr(self, name), str) or not _SHA256.fullmatch(getattr(self, name)):
                raise ChangeSetError("CS_SCHEMA_INVALID", f"selection.{name} must be lowercase SHA-256")
        dispositions = tuple(sorted(tuple(self.dispositions), key=lambda item: item.delta_id))
        if not all(isinstance(item, DeltaDisposition) for item in dispositions):
            raise ChangeSetError("CS_SCHEMA_INVALID", "selection dispositions must be typed")
        if len({item.delta_id for item in dispositions}) != len(dispositions):
            raise ChangeSetError("CS_SCHEMA_INVALID", "selection dispositions must be unique")
        object.__setattr__(self, "dispositions", dispositions)
        object.__setattr__(self, "closure_delta_ids", _sorted_unique(tuple(self.closure_delta_ids), "selection.closure_delta_ids"))
        application_order = tuple(self.application_order)
        if set(application_order) != set(self.closure_delta_ids) or len(application_order) != len(self.closure_delta_ids):
            raise ChangeSetError("CS_SCHEMA_INVALID", "selection application order must contain the closure exactly once")
        object.__setattr__(self, "application_order", application_order)
        object.__setattr__(self, "status", SelectionStatus(self.status))
        if (self.status is SelectionStatus.NO_CHANGE) != (not self.closure_delta_ids):
            raise ChangeSetError("CS_SCHEMA_INVALID", "selection status must agree with closure emptiness")

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_set_hash": self.change_set_hash,
            "dispositions": [item.to_dict() for item in self.dispositions],
            "closure_delta_ids": list(self.closure_delta_ids),
            "application_order": list(self.application_order),
            "selection_hash": self.selection_hash,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class MaterializationContext:
    task: TaskContract
    known_refs: frozenset[str]
    diagnostic_policy: DiagnosticPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.task, TaskContract):
            raise ChangeSetError("GRAPH_REVALIDATION_CONTEXT_MISSING", "materialization requires a TaskContract")
        if not isinstance(self.known_refs, frozenset):
            try:
                object.__setattr__(self, "known_refs", frozenset(self.known_refs))
            except (TypeError, ValueError) as exc:
                raise ChangeSetError(
                    "GRAPH_REVALIDATION_CONTEXT_MISSING",
                    "materialization known_refs must be a finite string catalog",
                ) from exc
        if any(not isinstance(item, str) or not item.strip() for item in self.known_refs):
            raise ChangeSetError(
                "GRAPH_REVALIDATION_CONTEXT_MISSING",
                "materialization known_refs must contain non-empty strings",
            )
        if not isinstance(self.diagnostic_policy, DiagnosticPolicy) or not self.diagnostic_policy.require_known_refs:
            raise ChangeSetError(
                "GRAPH_REVALIDATION_CONTEXT_MISSING",
                "materialization requires DiagnosticPolicy(require_known_refs=True)",
            )

    def to_dict(self) -> dict[str, Any]:
        policy = self.diagnostic_policy
        return {
            "task": self.task.to_dict(),
            "known_refs": sorted(self.known_refs),
            "diagnostic_policy": {
                "require_evidence_gate": policy.require_evidence_gate,
                "require_human_gate": policy.require_human_gate,
                "require_loop_exit": policy.require_loop_exit,
                "require_validation_before_irreversible_write": policy.require_validation_before_irreversible_write,
                "require_known_refs": policy.require_known_refs,
                "max_modules": policy.max_modules,
                "max_edges": policy.max_edges,
                "max_issues": policy.max_issues,
                "max_analysis_steps": policy.max_analysis_steps,
            },
        }

    @property
    def content_hash(self) -> str:
        return _hash(self.to_dict())


@dataclass(frozen=True, slots=True)
class ReevaluationRequirement:
    base_graph_hash: str
    change_set_hash: str
    selection_hash: str
    candidate_graph_hash: str
    evaluator_requirement_refs: tuple[str, ...]
    requires_fresh_evaluation: bool = True

    def __post_init__(self) -> None:
        for name in ("base_graph_hash", "change_set_hash", "selection_hash", "candidate_graph_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ChangeSetError("CS_SCHEMA_INVALID", f"reevaluation.{name} must be lowercase SHA-256")
        object.__setattr__(self, "evaluator_requirement_refs", _sorted_unique(
            tuple(self.evaluator_requirement_refs), "reevaluation.evaluator_requirement_refs",
        ))
        if self.requires_fresh_evaluation is not True:
            raise ChangeSetError("CS_SCHEMA_INVALID", "fresh exact-result evaluation cannot be disabled")

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_graph_hash": self.base_graph_hash,
            "change_set_hash": self.change_set_hash,
            "selection_hash": self.selection_hash,
            "candidate_graph_hash": self.candidate_graph_hash,
            "evaluator_requirement_refs": list(self.evaluator_requirement_refs),
            "requires_fresh_evaluation": True,
        }


@dataclass(frozen=True, slots=True)
class NoChangeReceipt:
    base_graph_hash: str
    change_set_hash: str
    selection_hash: str
    rejected_delta_ids: tuple[str, ...]
    deferred_delta_ids: tuple[str, ...]
    status: SelectionStatus = SelectionStatus.NO_CHANGE

    def __post_init__(self) -> None:
        for name in ("base_graph_hash", "change_set_hash", "selection_hash"):
            if not isinstance(getattr(self, name), str) or not _SHA256.fullmatch(getattr(self, name)):
                raise ChangeSetError("CS_SCHEMA_INVALID", f"no_change.{name} must be lowercase SHA-256")
        object.__setattr__(self, "rejected_delta_ids", _sorted_unique(tuple(self.rejected_delta_ids), "no_change.rejected_delta_ids"))
        object.__setattr__(self, "deferred_delta_ids", _sorted_unique(tuple(self.deferred_delta_ids), "no_change.deferred_delta_ids"))
        object.__setattr__(self, "status", SelectionStatus(self.status))
        if self.status is not SelectionStatus.NO_CHANGE:
            raise ChangeSetError("CS_SCHEMA_INVALID", "NoChangeReceipt status must be no_change")

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_graph_hash": self.base_graph_hash,
            "change_set_hash": self.change_set_hash,
            "selection_hash": self.selection_hash,
            "rejected_delta_ids": list(self.rejected_delta_ids),
            "deferred_delta_ids": list(self.deferred_delta_ids),
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class MaterializationReceipt:
    base_graph_hash: str
    change_set_hash: str
    selection_hash: str
    closure_delta_ids: tuple[str, ...]
    candidate_graph: MethodGraphVersion
    candidate_graph_hash: str
    validation_context_hash: str
    diagnostics: tuple[DiagnosticIssue, ...]
    reevaluation: ReevaluationRequirement

    def __post_init__(self) -> None:
        for name in (
            "base_graph_hash", "change_set_hash", "selection_hash", "candidate_graph_hash",
            "validation_context_hash",
        ):
            if not isinstance(getattr(self, name), str) or not _SHA256.fullmatch(getattr(self, name)):
                raise ChangeSetError("CS_SCHEMA_INVALID", f"materialization.{name} must be lowercase SHA-256")
        object.__setattr__(self, "closure_delta_ids", _sorted_unique(tuple(self.closure_delta_ids), "materialization.closure_delta_ids"))
        if not isinstance(self.candidate_graph, MethodGraphVersion) or self.candidate_graph.content_hash != self.candidate_graph_hash:
            raise ChangeSetError("CS_SCHEMA_INVALID", "materialization candidate graph/hash mismatch")
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(item, DiagnosticIssue) for item in diagnostics):
            raise ChangeSetError("CS_SCHEMA_INVALID", "materialization diagnostics must be DiagnosticIssue values")
        object.__setattr__(self, "diagnostics", tuple(sorted(diagnostics, key=_diagnostic_sort_key)))
        if not isinstance(self.reevaluation, ReevaluationRequirement):
            raise ChangeSetError("CS_SCHEMA_INVALID", "materialization requires reevaluation binding")

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_graph_hash": self.base_graph_hash,
            "change_set_hash": self.change_set_hash,
            "selection_hash": self.selection_hash,
            "closure_delta_ids": list(self.closure_delta_ids),
            "candidate_graph": self.candidate_graph.to_dict(),
            "candidate_graph_hash": self.candidate_graph_hash,
            "validation_context_hash": self.validation_context_hash,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "reevaluation": self.reevaluation.to_dict(),
        }


def _diagnostic_sort_key(issue: DiagnosticIssue) -> tuple[Any, ...]:
    return (
        issue.code,
        issue.edge_ids,
        issue.message,
        issue.module_ids,
        issue.safe_alternative,
        issue.severity,
    )


def _issues_digest(issues: tuple[DiagnosticIssue, ...]) -> str:
    return _hash([issue.to_dict() for issue in issues])


@dataclass(frozen=True, slots=True)
class MaterializationFailureReceipt:
    base_graph_hash: str
    change_set_hash: str
    selection_hash: str
    closure_delta_ids: tuple[str, ...]
    attempted_candidate_hash: str
    validation_context_hash: str
    issues: tuple[DiagnosticIssue, ...]
    issues_digest: str
    candidate_graph: None = field(default=None, init=False)
    reevaluation: None = field(default=None, init=False)

    def __post_init__(self) -> None:
        for name in (
            "base_graph_hash", "change_set_hash", "selection_hash",
            "attempted_candidate_hash", "validation_context_hash", "issues_digest",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ChangeSetError("CS_SCHEMA_INVALID", f"materialization_failure.{name} must be lowercase SHA-256")
        object.__setattr__(self, "closure_delta_ids", _sorted_unique(
            tuple(self.closure_delta_ids), "materialization_failure.closure_delta_ids",
        ))
        supplied_issues = tuple(self.issues)
        if not supplied_issues or not all(isinstance(issue, DiagnosticIssue) for issue in supplied_issues):
            raise ChangeSetError("CS_SCHEMA_INVALID", "materialization failure requires typed diagnostic issues")
        issues = tuple(sorted(supplied_issues, key=_diagnostic_sort_key))
        if not any(issue.severity == "error" for issue in issues):
            raise ChangeSetError("CS_SCHEMA_INVALID", "materialization failure requires at least one blocking issue")
        object.__setattr__(self, "issues", issues)
        if self.issues_digest != _issues_digest(issues):
            raise ChangeSetError("CS_SCHEMA_INVALID", "materialization failure issues_digest mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_graph_hash": self.base_graph_hash,
            "change_set_hash": self.change_set_hash,
            "selection_hash": self.selection_hash,
            "closure_delta_ids": list(self.closure_delta_ids),
            "attempted_candidate_hash": self.attempted_candidate_hash,
            "validation_context_hash": self.validation_context_hash,
            "issues": [issue.to_dict() for issue in self.issues],
            "issues_digest": self.issues_digest,
            "candidate_graph": None,
            "reevaluation": None,
        }


@dataclass(frozen=True, slots=True)
class ChangeSetStateRecord:
    change_set_hash: str
    status: ChangeSetStatus
    previous_state_hash: str | None = None
    cause_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.change_set_hash, str) or not _SHA256.fullmatch(self.change_set_hash):
            raise ChangeSetError("CS_SCHEMA_INVALID", "state change_set_hash must be lowercase SHA-256")
        object.__setattr__(self, "status", ChangeSetStatus(self.status))
        if self.previous_state_hash is not None and not _SHA256.fullmatch(self.previous_state_hash):
            raise ChangeSetError("CS_SCHEMA_INVALID", "state previous_state_hash must be lowercase SHA-256")
        if self.cause_ref is not None:
            _nonempty(self.cause_ref, "state.cause_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_set_hash": self.change_set_hash,
            "status": self.status.value,
            "previous_state_hash": self.previous_state_hash,
            "cause_ref": self.cause_ref,
        }

    @property
    def content_hash(self) -> str:
        return _hash(self.to_dict())


@dataclass(frozen=True, slots=True)
class DeltaStateRecord:
    change_set_hash: str
    delta_id: str
    status: DeltaStatus
    previous_state_hash: str | None = None
    cause_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.change_set_hash, str) or not _SHA256.fullmatch(self.change_set_hash):
            raise ChangeSetError("DELTA_SCHEMA_INVALID", "state change_set_hash must be lowercase SHA-256")
        _identifier(self.delta_id, "delta_state.delta_id")
        object.__setattr__(self, "status", DeltaStatus(self.status))
        if self.previous_state_hash is not None and not _SHA256.fullmatch(self.previous_state_hash):
            raise ChangeSetError("DELTA_SCHEMA_INVALID", "state previous_state_hash must be lowercase SHA-256")
        if self.cause_ref is not None:
            _nonempty(self.cause_ref, "delta_state.cause_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_set_hash": self.change_set_hash,
            "delta_id": self.delta_id,
            "status": self.status.value,
            "previous_state_hash": self.previous_state_hash,
            "cause_ref": self.cause_ref,
        }

    @property
    def content_hash(self) -> str:
        return _hash(self.to_dict())


def parse_external_proposal(
    payload: bytes,
    policy: ChangeSetPolicy | ChangeSetBudget | None = None,
) -> ExternalProposal:
    """Parse a bounded strict JSON proposal without importing or executing it."""
    effective = _policy(policy)
    if not isinstance(payload, bytes):
        raise ChangeSetError("CS_SCHEMA_INVALID", "external proposal payload must be bytes")
    if len(payload) > effective.budget.max_payload_bytes:
        raise ChangeSetError("CS_BUDGET_EXCEEDED", "external proposal exceeds max_payload_bytes")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except ChangeSetError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChangeSetError("CS_SCHEMA_INVALID", "external proposal must be valid UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise ChangeSetError("CS_SCHEMA_INVALID", "external proposal root must be an object")
    return ExternalProposal.from_dict(value)


external_proposal_from_json_bytes = parse_external_proposal


_CHANGESET_TRANSITIONS: dict[ChangeSetStatus, frozenset[ChangeSetStatus]] = {
    ChangeSetStatus.PROPOSED: frozenset({ChangeSetStatus.VALIDATED, ChangeSetStatus.INVALID, ChangeSetStatus.SUPERSEDED}),
    ChangeSetStatus.VALIDATED: frozenset({ChangeSetStatus.UNDER_SELECTION, ChangeSetStatus.SUPERSEDED}),
    ChangeSetStatus.UNDER_SELECTION: frozenset({ChangeSetStatus.CLOSURE_MATERIALIZED, ChangeSetStatus.SUPERSEDED}),
    ChangeSetStatus.CLOSURE_MATERIALIZED: frozenset({ChangeSetStatus.RESOLVED, ChangeSetStatus.SUPERSEDED}),
    ChangeSetStatus.INVALID: frozenset(),
    ChangeSetStatus.RESOLVED: frozenset(),
    ChangeSetStatus.SUPERSEDED: frozenset(),
}


_DELTA_TRANSITIONS: dict[DeltaStatus, frozenset[DeltaStatus]] = {
    DeltaStatus.PROPOSED: frozenset({DeltaStatus.STRUCTURALLY_VALID, DeltaStatus.INVALID, DeltaStatus.SUPERSEDED}),
    DeltaStatus.STRUCTURALLY_VALID: frozenset({
        DeltaStatus.ACCEPTED, DeltaStatus.REJECTED, DeltaStatus.DEFERRED, DeltaStatus.SUPERSEDED,
    }),
    DeltaStatus.ACCEPTED: frozenset({DeltaStatus.DEPENDENCY_CLOSED, DeltaStatus.CONFLICT_BLOCKED, DeltaStatus.SUPERSEDED}),
    DeltaStatus.DEPENDENCY_CLOSED: frozenset({DeltaStatus.CANDIDATE_MATERIALIZED, DeltaStatus.SUPERSEDED}),
    DeltaStatus.CANDIDATE_MATERIALIZED: frozenset({DeltaStatus.CANONICALIZED, DeltaStatus.SUPERSEDED}),
    DeltaStatus.INVALID: frozenset(),
    DeltaStatus.REJECTED: frozenset(),
    DeltaStatus.DEFERRED: frozenset(),
    DeltaStatus.CONFLICT_BLOCKED: frozenset(),
    DeltaStatus.CANONICALIZED: frozenset(),
    DeltaStatus.SUPERSEDED: frozenset(),
}


def validate_changeset_transition(
    current: ChangeSetStatus | str,
    target: ChangeSetStatus | str,
) -> ChangeSetStatus:
    current_status = ChangeSetStatus(current)
    target_status = ChangeSetStatus(target)
    if target_status not in _CHANGESET_TRANSITIONS[current_status]:
        raise ChangeSetError("CS_CONSISTENCY_INVALID", f"forbidden ChangeSet transition {current_status.value}->{target_status.value}")
    return target_status


def validate_delta_transition(current: DeltaStatus | str, target: DeltaStatus | str) -> DeltaStatus:
    current_status = DeltaStatus(current)
    target_status = DeltaStatus(target)
    if target_status not in _DELTA_TRANSITIONS[current_status]:
        raise ChangeSetError("DELTA_STRUCTURE_INVALID", f"forbidden delta transition {current_status.value}->{target_status.value}")
    return target_status


def _expectations(patch: GraphPatch) -> dict[tuple[ObjectKind, str], str]:
    return {(item.kind, item.object_id): item.sha256 for item in patch.expectations}


def _write_set(delta: EvolutionDelta) -> frozenset[str]:
    values = {f"module:{item}" for item in delta.patch.remove_module_ids}
    values.update(f"module:{item.module_id}" for item in delta.patch.put_modules)
    values.update(f"edge:{item}" for item in delta.patch.remove_edge_ids)
    values.update(f"edge:{item.edge_id}" for item in delta.patch.put_edges)
    if delta.patch.boundary is not None:
        values.add("graph:boundary")
    return frozenset(values)


def _read_set(delta: EvolutionDelta) -> frozenset[str]:
    return frozenset(
        f"{expectation.kind.value}:{expectation.object_id}"
        for expectation in delta.patch.expectations
    )


def _touched_set(delta: EvolutionDelta) -> frozenset[str]:
    return _read_set(delta) | _write_set(delta)


def _topological_delta_order(
    delta_by_id: Mapping[str, EvolutionDelta],
    subset: frozenset[str] | None = None,
) -> tuple[str, ...]:
    selected = frozenset(delta_by_id) if subset is None else subset
    indegree = {delta_id: 0 for delta_id in selected}
    dependents: dict[str, set[str]] = {delta_id: set() for delta_id in selected}
    for delta_id in selected:
        for dependency in delta_by_id[delta_id].depends_on:
            if dependency not in selected:
                continue
            indegree[delta_id] += 1
            dependents[dependency].add(delta_id)
    ready = [delta_id for delta_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    result: list[str] = []
    while ready:
        delta_id = heapq.heappop(ready)
        result.append(delta_id)
        for dependent in sorted(dependents[delta_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    if len(result) != len(selected):
        raise ChangeSetError("CS_DEPENDENCY_CYCLE", "delta dependencies contain a cycle", delta_ids=tuple(selected))
    return tuple(result)


def _dependency_ancestors(delta_id: str, delta_by_id: Mapping[str, EvolutionDelta]) -> frozenset[str]:
    seen: set[str] = set()
    pending = list(delta_by_id[delta_id].depends_on)
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(delta_by_id[current].depends_on)
    return frozenset(seen)


def _dependency_depths(
    order: tuple[str, ...],
    delta_by_id: Mapping[str, EvolutionDelta],
) -> dict[str, int]:
    depths: dict[str, int] = {}
    for delta_id in order:
        depths[delta_id] = 1 + max((depths[item] for item in delta_by_id[delta_id].depends_on), default=0)
    return depths


def _predecessor_object(
    *,
    kind: ObjectKind,
    object_id: str,
    delta_id: str,
    base_modules: Mapping[str, MethodModule],
    base_edges: Mapping[str, MethodEdge],
    delta_by_id: Mapping[str, EvolutionDelta],
    order_position: Mapping[str, int],
) -> MethodModule | MethodEdge | None:
    ancestors = _dependency_ancestors(delta_id, delta_by_id)
    candidates: list[tuple[int, int, MethodModule | MethodEdge | None]] = []
    for ancestor_id in ancestors:
        patch = delta_by_id[ancestor_id].patch
        removed_ids = patch.remove_module_ids if kind is ObjectKind.MODULE else patch.remove_edge_ids
        if object_id in removed_ids:
            candidates.append((order_position[ancestor_id], 0, None))
        objects: tuple[MethodModule | MethodEdge, ...] = (
            patch.put_modules if kind is ObjectKind.MODULE else patch.put_edges
        )
        for item in objects:
            item_id = item.module_id if kind is ObjectKind.MODULE else item.edge_id
            if item_id == object_id:
                candidates.append((order_position[ancestor_id], 1, item))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[-1][2]
    return base_modules.get(object_id) if kind is ObjectKind.MODULE else base_edges.get(object_id)


def _predecessor_boundary(
    *,
    delta_id: str,
    base: MethodGraphVersion,
    delta_by_id: Mapping[str, EvolutionDelta],
    order_position: Mapping[str, int],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    candidates = sorted(
        (
            order_position[ancestor_id],
            delta_by_id[ancestor_id].patch.boundary,
        )
        for ancestor_id in _dependency_ancestors(delta_id, delta_by_id)
        if delta_by_id[ancestor_id].patch.boundary is not None
    )
    if not candidates:
        return tuple(sorted(base.entry_module_ids)), tuple(sorted(base.exit_module_ids))
    boundary = candidates[-1][1]
    assert boundary is not None
    return boundary.result_entry_module_ids, boundary.result_exit_module_ids


def _incident_predecessor_edge_ids(
    *,
    retired_module_ids: frozenset[str],
    delta_id: str,
    base_edges: Mapping[str, MethodEdge],
    base_modules: Mapping[str, MethodModule],
    delta_by_id: Mapping[str, EvolutionDelta],
    order_position: Mapping[str, int],
) -> frozenset[str]:
    ancestors = _dependency_ancestors(delta_id, delta_by_id)
    candidate_edge_ids = set(base_edges)
    for ancestor_id in ancestors:
        ancestor_patch = delta_by_id[ancestor_id].patch
        candidate_edge_ids.update(ancestor_patch.remove_edge_ids)
        candidate_edge_ids.update(edge.edge_id for edge in ancestor_patch.put_edges)
    incident: set[str] = set()
    for edge_id in sorted(candidate_edge_ids):
        predecessor = _predecessor_object(
            kind=ObjectKind.EDGE,
            object_id=edge_id,
            delta_id=delta_id,
            base_modules=base_modules,
            base_edges=base_edges,
            delta_by_id=delta_by_id,
            order_position=order_position,
        )
        if isinstance(predecessor, MethodEdge) and (
            predecessor.source_module in retired_module_ids
            or predecessor.target_module in retired_module_ids
        ):
            incident.add(edge_id)
    return frozenset(incident)


def _validate_operation_shape(delta: EvolutionDelta) -> None:
    patch = delta.patch
    removed = len(patch.remove_module_ids)
    put = len(patch.put_modules)
    operation = delta.operation
    valid = {
        DeltaOperation.ADD: removed == 0 and put == 1,
        DeltaOperation.DELETE: removed == 1 and put == 0,
        # Same-ID replacement may omit remove_module_ids because the exact
        # ObjectExpectation is the compare-and-swap precondition. An explicit
        # remove-and-put remains valid only for the same module ID.
        DeltaOperation.REPLACE: removed in {0, 1} and put == 1,
        DeltaOperation.MERGE: removed >= 2 and put == 1,
        DeltaOperation.SPLIT: removed == 1 and put >= 2,
        DeltaOperation.REORDER: removed == 0 and put == 0 and bool(patch.remove_edge_ids or patch.put_edges),
    }[operation]
    if not valid:
        raise ChangeSetError(
            "DELTA_STRUCTURE_INVALID",
            f"delta {delta.delta_id} patch shape does not match {operation.value}",
            delta_ids=(delta.delta_id,),
        )
    if delta.category is not DeltaCategory.TOPOLOGY and operation is not DeltaOperation.REPLACE:
        raise ChangeSetError(
            "DELTA_STRUCTURE_INVALID",
            "non-topology deltas must use REPLACE",
            delta_ids=(delta.delta_id,),
        )
    if operation is DeltaOperation.REPLACE and removed:
        if patch.put_modules[0].module_id != patch.remove_module_ids[0]:
            raise ChangeSetError(
                "DELTA_STRUCTURE_INVALID",
                "REPLACE must preserve module_id; use merge or split for identity changes",
                delta_ids=(delta.delta_id,),
            )
    if operation in {DeltaOperation.MERGE, DeltaOperation.SPLIT}:
        put_module_ids = {module.module_id for module in patch.put_modules}
        if put_module_ids & set(patch.remove_module_ids):
            raise ChangeSetError(
                "DELTA_STRUCTURE_INVALID",
                f"{operation.value} replacement modules require new module IDs",
                delta_ids=(delta.delta_id,),
            )
    if operation is DeltaOperation.REORDER:
        if patch.boundary is not None:
            raise ChangeSetError("DELTA_STRUCTURE_INVALID", "reorder cannot change graph boundaries", delta_ids=(delta.delta_id,))
        if any(edge.kind not in ORDERING_EDGE_KINDS for edge in patch.put_edges):
            raise ChangeSetError("DELTA_STRUCTURE_INVALID", "reorder may put only ordering edges", delta_ids=(delta.delta_id,))


_CONTENT_FIELDS = frozenset({"name", "description", "content_refs"})
_RULE_FIELDS = frozenset({
    "applicability_conditions",
    "preconditions",
    "postconditions",
    "failure_modes",
    "counterexamples",
    "fallback_refs",
    "rule_refs",
})
_MEMORY_FIELDS = frozenset({"memory_refs"})
_PLANE_OWNED_FIELDS = _CONTENT_FIELDS | _RULE_FIELDS | _MEMORY_FIELDS
_PLANE_FIELDS = {
    DeltaCategory.CONTENT: _CONTENT_FIELDS,
    DeltaCategory.RULES: _RULE_FIELDS,
    DeltaCategory.MEMORY: _MEMORY_FIELDS,
}


def _validate_plane_change(delta: EvolutionDelta, before: MethodModule, after: MethodModule) -> None:
    before_dict = before.to_dict()
    after_dict = after.to_dict()
    changed = {key for key in before_dict if before_dict[key] != after_dict[key]}
    if not changed:
        raise ChangeSetError(
            "DELTA_STRUCTURE_INVALID",
            "module replacement must change the module contract",
            delta_ids=(delta.delta_id,),
        )
    if before.version_id == after.version_id:
        raise ChangeSetError(
            "DELTA_STRUCTURE_INVALID",
            "a changed module contract requires a new version_id",
            delta_ids=(delta.delta_id,),
        )
    if delta.category is DeltaCategory.TOPOLOGY:
        forbidden = changed & _PLANE_OWNED_FIELDS
        if forbidden:
            raise ChangeSetError(
                "DELTA_STRUCTURE_INVALID",
                f"topology replacement cannot change content/rules/memory plane fields: {sorted(forbidden)}",
                delta_ids=(delta.delta_id,),
            )
        return
    if delta.operation is not DeltaOperation.REPLACE:
        raise ChangeSetError(
            "DELTA_STRUCTURE_INVALID",
            "non-topology deltas must use same-ID replace",
            delta_ids=(delta.delta_id,),
        )
    if before.module_id != after.module_id:
        raise ChangeSetError("DELTA_STRUCTURE_INVALID", "non-topology replace must preserve module_id", delta_ids=(delta.delta_id,))
    allowed = _PLANE_FIELDS[delta.category] | frozenset({"version_id"})
    if not changed <= allowed:
        raise ChangeSetError(
            "DELTA_STRUCTURE_INVALID",
            f"{delta.category.value} delta changes fields outside its plane",
            delta_ids=(delta.delta_id,),
        )


def _validate_budget_counts(
    proposal: ExternalProposal,
    budget: ChangeSetBudget,
    base: MethodGraphVersion,
) -> None:
    deltas = proposal.deltas
    conflict_pairs = {
        tuple(sorted((item.delta_id, conflict)))
        for item in deltas
        for conflict in item.conflicts_with
    }
    counts = {
        "max_deltas": len(deltas),
        "max_dependencies": sum(len(item.depends_on) for item in deltas),
        "max_conflicts": len(conflict_pairs),
        "max_modules_added": sum(len(item.patch.put_modules) for item in deltas),
        "max_modules_removed": sum(len(item.patch.remove_module_ids) for item in deltas),
        "max_edges_added": sum(len(item.patch.put_edges) for item in deltas),
        "max_edges_removed": sum(len(item.patch.remove_edge_ids) for item in deltas),
        "max_touched_objects": sum(len(_touched_set(item)) for item in deltas),
    }
    exceeded = tuple(name for name, value in counts.items() if value > getattr(budget, name))
    analysis_steps = (
        len(deltas)
        + counts["max_dependencies"] * max(1, len(deltas))
        + counts["max_conflicts"] * 2
        + counts["max_touched_objects"] * max(1, len(deltas))
        + (len(base.modules) + len(base.edges)) * max(1, len(deltas))
    )
    if exceeded or analysis_steps > budget.max_analysis_steps:
        detail = ", ".join(exceeded) if exceeded else "max_analysis_steps"
        raise ChangeSetError("CS_BUDGET_EXCEEDED", f"proposal exceeds hard budget: {detail}")


def validate_changeset(
    base: MethodGraphVersion,
    proposal: ExternalProposal,
    policy: ChangeSetPolicy | ChangeSetBudget | None = None,
) -> EvolutionChangeSet:
    """Validate one external proposal and bind it to base graph and policy."""
    if not isinstance(base, MethodGraphVersion) or not isinstance(proposal, ExternalProposal):
        raise ChangeSetError("CS_SCHEMA_INVALID", "validate_changeset requires typed base graph and proposal")
    effective_policy = _policy(policy)
    budget = effective_policy.budget
    if len(proposal.canonical_bytes) > budget.max_payload_bytes:
        raise ChangeSetError("CS_BUDGET_EXCEEDED", "proposal exceeds max_payload_bytes")
    if proposal.base_graph_hash != base.content_hash:
        raise ChangeSetError("CS_BASE_MISMATCH", "proposal base graph hash is stale or incorrect")
    _validate_budget_counts(proposal, budget, base)
    if any(delta.operation not in effective_policy.allowed_operations for delta in proposal.deltas):
        raise ChangeSetError("CS_OPERATION_FORBIDDEN", "proposal contains an operation forbidden by policy")

    # Enforce absolute result ceilings before graph traversal or selection.
    # Same-ID puts are replacements, not additions, even when remove is
    # intentionally omitted in favor of an exact object expectation.
    base_module_ids = {item.module_id for item in base.modules}
    base_edge_ids = {item.edge_id for item in base.edges}
    proposal_put_module_ids = {module.module_id for delta in proposal.deltas for module in delta.patch.put_modules}
    proposal_put_edge_ids = {edge.edge_id for delta in proposal.deltas for edge in delta.patch.put_edges}
    proposal_removed_module_ids = {module_id for delta in proposal.deltas for module_id in delta.patch.remove_module_ids}
    proposal_removed_edge_ids = {edge_id for delta in proposal.deltas for edge_id in delta.patch.remove_edge_ids}
    upper_result_modules = (
        len(base_module_ids - proposal_removed_module_ids)
        + len(proposal_put_module_ids - base_module_ids)
        + len(proposal_put_module_ids & proposal_removed_module_ids)
    )
    upper_result_edges = (
        len(base_edge_ids - proposal_removed_edge_ids)
        + len(proposal_put_edge_ids - base_edge_ids)
        + len(proposal_put_edge_ids & proposal_removed_edge_ids)
    )
    if upper_result_modules > budget.max_result_modules or upper_result_edges > budget.max_result_edges:
        raise ChangeSetError("CS_BUDGET_EXCEEDED", "proposal may exceed result graph budget")

    delta_by_id = {item.delta_id: item for item in proposal.deltas}
    known = set(delta_by_id)
    for delta in proposal.deltas:
        unknown = (set(delta.depends_on) | set(delta.conflicts_with)) - known
        if unknown:
            raise ChangeSetError("DELTA_SCHEMA_INVALID", f"delta {delta.delta_id} references unknown deltas", delta_ids=(delta.delta_id,))
        for conflict in delta.conflicts_with:
            if delta.delta_id not in delta_by_id[conflict].conflicts_with:
                raise ChangeSetError(
                    "CS_CONFLICT_ASYMMETRIC",
                    f"conflict {delta.delta_id}<->{conflict} is not symmetric",
                    delta_ids=(delta.delta_id, conflict),
                )
            if conflict in delta.depends_on or delta.delta_id in delta_by_id[conflict].depends_on:
                raise ChangeSetError(
                    "CS_CONSISTENCY_INVALID",
                    "the same delta pair cannot be dependency-linked and conflicting",
                    delta_ids=(delta.delta_id, conflict),
                )

    order = _topological_delta_order(delta_by_id)
    depths = _dependency_depths(order, delta_by_id)
    if max(depths.values(), default=0) > budget.max_dependency_depth:
        raise ChangeSetError("CS_BUDGET_EXCEEDED", "proposal exceeds max_dependency_depth")
    position = {delta_id: index for index, delta_id in enumerate(order)}
    ancestors = {delta_id: _dependency_ancestors(delta_id, delta_by_id) for delta_id in order}
    base_modules = {item.module_id: item for item in base.modules}
    base_edges = {item.edge_id: item for item in base.edges}

    for left_index, left_id in enumerate(order):
        left = delta_by_id[left_id]
        left_writes = _write_set(left)
        left_reads = _read_set(left)
        for right_id in order[left_index + 1 :]:
            right = delta_by_id[right_id]
            right_writes = _write_set(right)
            right_reads = _read_set(right)
            hazard = (
                (left_writes & right_writes)
                | (left_writes & right_reads)
                | (left_reads & right_writes)
            )
            if not hazard:
                continue
            ordered = left_id in ancestors[right_id] or right_id in ancestors[left_id]
            conflicted = right_id in left.conflicts_with
            if not ordered and not conflicted:
                raise ChangeSetError(
                    "CS_WRITESET_AMBIGUOUS",
                    f"independent deltas have an undeclared read/write hazard: {sorted(hazard)}",
                    delta_ids=(left_id, right_id),
                )

    for delta_id in order:
        delta = delta_by_id[delta_id]
        _validate_operation_shape(delta)
        patch = delta.patch
        expectation_map = _expectations(patch)
        touched_deprecated_ids = {
            module_id
            for edge in base.edges
            if edge.edge_id in patch.remove_edge_ids
            for module_id in (edge.source_module, edge.target_module)
            if base_modules[module_id].deprecated
        }
        touched_deprecated_ids.update(
            module_id
            for edge in patch.put_edges
            for module_id in (edge.source_module, edge.target_module)
            if module_id in base_modules and base_modules[module_id].deprecated
        )
        touched_deprecated_ids.update(
            module.module_id
            for module in patch.put_modules
            if module.module_id in base_modules and base_modules[module.module_id].deprecated
        )
        if touched_deprecated_ids and delta.operation not in {DeltaOperation.DELETE, DeltaOperation.REPLACE}:
            raise ChangeSetError(
                "DELTA_STRUCTURE_INVALID",
                "deprecated targets may only be deleted or replaced as remediation",
                delta_ids=(delta_id,),
            )
        for module_id in patch.remove_module_ids:
            if (ObjectKind.MODULE, module_id) not in expectation_map:
                raise ChangeSetError("DELTA_STRUCTURE_INVALID", "removed modules require expected hashes", delta_ids=(delta_id,))
        for edge_id in patch.remove_edge_ids:
            if (ObjectKind.EDGE, edge_id) not in expectation_map:
                raise ChangeSetError("DELTA_STRUCTURE_INVALID", "removed edges require expected hashes", delta_ids=(delta_id,))

        for expectation in patch.expectations:
            predecessor = _predecessor_object(
                kind=expectation.kind,
                object_id=expectation.object_id,
                delta_id=delta_id,
                base_modules=base_modules,
                base_edges=base_edges,
                delta_by_id=delta_by_id,
                order_position=position,
            )
            if predecessor is None or object_content_hash(predecessor) != expectation.sha256:
                raise ChangeSetError(
                    "DELTA_PRECONDITION_MISMATCH",
                    f"delta {delta_id} expectation does not match predecessor {expectation.object_id}",
                    delta_ids=(delta_id,),
                )

        if delta.operation is DeltaOperation.REPLACE and not patch.remove_module_ids:
            replacement_id = patch.put_modules[0].module_id
            predecessor = _predecessor_object(
                kind=ObjectKind.MODULE,
                object_id=replacement_id,
                delta_id=delta_id,
                base_modules=base_modules,
                base_edges=base_edges,
                delta_by_id=delta_by_id,
                order_position=position,
            )
            if predecessor is None or (ObjectKind.MODULE, replacement_id) not in expectation_map:
                raise ChangeSetError(
                    "DELTA_STRUCTURE_INVALID",
                    "implicit REPLACE requires an existing predecessor and exact same-ID module expectation",
                    delta_ids=(delta_id,),
                )

        if delta.operation is DeltaOperation.REORDER:
            for edge_id in patch.remove_edge_ids:
                predecessor = _predecessor_object(
                    kind=ObjectKind.EDGE,
                    object_id=edge_id,
                    delta_id=delta_id,
                    base_modules=base_modules,
                    base_edges=base_edges,
                    delta_by_id=delta_by_id,
                    order_position=position,
                )
                if not isinstance(predecessor, MethodEdge) or predecessor.kind not in ORDERING_EDGE_KINDS:
                    raise ChangeSetError(
                        "DELTA_STRUCTURE_INVALID",
                        "REORDER may remove only ordering edges",
                        delta_ids=(delta_id,),
                    )

        for module_id in patch.remove_module_ids:
            predecessor = _predecessor_object(
                kind=ObjectKind.MODULE, object_id=module_id, delta_id=delta_id,
                base_modules=base_modules, base_edges=base_edges, delta_by_id=delta_by_id, order_position=position,
            )
            if predecessor is None:
                raise ChangeSetError("DELTA_STRUCTURE_INVALID", "removed module does not exist", delta_ids=(delta_id,))
            assert isinstance(predecessor, MethodModule)
            if predecessor.deprecated and delta.operation not in {DeltaOperation.DELETE, DeltaOperation.REPLACE}:
                raise ChangeSetError(
                    "DELTA_STRUCTURE_INVALID",
                    "deprecated targets may only be deleted or replaced as remediation",
                    delta_ids=(delta_id,),
                )

        for module in patch.put_modules:
            before = _predecessor_object(
                kind=ObjectKind.MODULE, object_id=module.module_id, delta_id=delta_id,
                base_modules=base_modules, base_edges=base_edges, delta_by_id=delta_by_id, order_position=position,
            )
            if before is None:
                if module.module_id in base_modules or module.module_id in patch.remove_module_ids:
                    raise ChangeSetError("DELTA_STRUCTURE_INVALID", "module add/replace predecessor is ambiguous", delta_ids=(delta_id,))
                if delta.category is not DeltaCategory.TOPOLOGY:
                    raise ChangeSetError("DELTA_STRUCTURE_INVALID", "non-topology delta cannot create a module", delta_ids=(delta_id,))
                initialized_plane_fields = {
                    field_name
                    for field_name in (_PLANE_OWNED_FIELDS - {"name", "description"})
                    if getattr(module, field_name)
                }
                if initialized_plane_fields:
                    raise ChangeSetError(
                        "DELTA_STRUCTURE_INVALID",
                        "new topology modules must leave mutable content/rules/memory plane fields empty",
                        delta_ids=(delta_id,),
                    )
            else:
                assert isinstance(before, MethodModule)
                _validate_plane_change(delta, before, module)
                if before.deprecated and module.deprecated:
                    raise ChangeSetError(
                        "DELTA_STRUCTURE_INVALID",
                        "deprecated replacement must remediate rather than preserve deprecated status",
                        delta_ids=(delta_id,),
                    )
                implicit_same_id_replace = (
                    delta.operation is DeltaOperation.REPLACE
                    and module.module_id not in patch.remove_module_ids
                    and (ObjectKind.MODULE, module.module_id) in expectation_map
                )
                if module.module_id not in patch.remove_module_ids and not implicit_same_id_replace:
                    raise ChangeSetError(
                        "DELTA_STRUCTURE_INVALID",
                        "module replacement requires explicit removal or an exact same-ID expectation",
                        delta_ids=(delta_id,),
                    )
        if delta.category is not DeltaCategory.TOPOLOGY and (
            patch.put_edges or patch.remove_edge_ids or patch.boundary is not None
        ):
            raise ChangeSetError("DELTA_STRUCTURE_INVALID", "non-topology deltas cannot change edges or boundaries", delta_ids=(delta_id,))

        retired_module_ids = frozenset(patch.remove_module_ids) - frozenset(
            module.module_id for module in patch.put_modules
        )
        if retired_module_ids:
            incident = _incident_predecessor_edge_ids(
                retired_module_ids=retired_module_ids,
                delta_id=delta_id,
                base_edges=base_edges,
                base_modules=base_modules,
                delta_by_id=delta_by_id,
                order_position=position,
            )
            if not incident <= set(patch.remove_edge_ids):
                raise ChangeSetError(
                    "DELTA_STRUCTURE_INVALID",
                    "retired modules require explicit expectation and removal of every incident edge",
                    delta_ids=(delta_id,),
                )
        if patch.boundary is not None:
            predecessor_entries, predecessor_exits = _predecessor_boundary(
                delta_id=delta_id,
                base=base,
                delta_by_id=delta_by_id,
                order_position=position,
            )
            if (
                patch.boundary.expected_entry_module_ids != predecessor_entries
                or patch.boundary.expected_exit_module_ids != predecessor_exits
            ):
                raise ChangeSetError(
                    "DELTA_PRECONDITION_MISMATCH",
                    "boundary expectation does not match its dependency predecessor",
                    delta_ids=(delta_id,),
                )

    return EvolutionChangeSet(
        change_set_id=proposal.proposal_id,
        base_graph_hash=proposal.base_graph_hash,
        provenance=proposal.provenance,
        deltas=proposal.deltas,
        effective_budget=budget,
        allowed_operations=effective_policy.allowed_operations,
        policy_hash=effective_policy.content_hash,
    )


def _selection_payload(
    *,
    change_set_hash: str,
    dispositions: tuple[DeltaDisposition, ...],
    closure_delta_ids: tuple[str, ...],
    application_order: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema": SELECTION_SCHEMA,
        "change_set_hash": change_set_hash,
        "decisions": [item.to_dict() for item in sorted(dispositions, key=lambda item: item.delta_id)],
        "closure_delta_ids": sorted(closure_delta_ids),
        "application_order": list(application_order),
    }


def resolve_selection(
    changeset: EvolutionChangeSet,
    dispositions: Mapping[str, Disposition | str],
) -> SelectionResolution:
    """Resolve exact human dispositions into a deterministic dependency closure.

    A disposition is required for every delta.  Dependencies are not implicitly
    accepted: every dependency in the accepted closure must itself have an
    explicit ``accepted`` disposition.
    """
    if not isinstance(changeset, EvolutionChangeSet) or not isinstance(dispositions, Mapping):
        raise ChangeSetError("CS_SCHEMA_INVALID", "resolve_selection requires a typed ChangeSet and disposition mapping")
    _validate_bound_policy(changeset)
    delta_by_id = {delta.delta_id: delta for delta in changeset.deltas}
    supplied = set(dispositions)
    expected = set(delta_by_id)
    if supplied != expected or any(not isinstance(delta_id, str) for delta_id in supplied):
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        raise ChangeSetError(
            "CS_DECISION_INCOMPLETE",
            f"selection decisions must be exact and complete; missing={missing}, extra={extra}",
        )
    try:
        decisions = tuple(
            DeltaDisposition(delta_id=delta_id, disposition=Disposition(dispositions[delta_id]))
            for delta_id in sorted(expected)
        )
    except (TypeError, ValueError) as exc:
        raise ChangeSetError("CS_DECISION_INCOMPLETE", "selection contains an invalid disposition") from exc

    disposition_by_id = {item.delta_id: item.disposition for item in decisions}
    accepted = {delta_id for delta_id, value in disposition_by_id.items() if value is Disposition.ACCEPTED}
    closure = set(accepted)
    pending = list(sorted(accepted))
    while pending:
        delta_id = pending.pop()
        for dependency in delta_by_id[delta_id].depends_on:
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    unaccepted_dependencies = sorted(
        delta_id for delta_id in closure if disposition_by_id[delta_id] is not Disposition.ACCEPTED
    )
    if unaccepted_dependencies:
        raise ChangeSetError(
            "DELTA_DEPENDENCY_MISSING",
            "accepted deltas require dependencies that were not explicitly accepted",
            delta_ids=tuple(unaccepted_dependencies),
        )
    conflict_pairs = {
        tuple(sorted((delta_id, conflict)))
        for delta_id in closure
        for conflict in delta_by_id[delta_id].conflicts_with
        if conflict in closure
    }
    if conflict_pairs:
        blocked = tuple(sorted({item for pair in conflict_pairs for item in pair}))
        raise ChangeSetError(
            "DELTA_CONFLICT",
            "accepted closure contains mutually conflicting deltas",
            delta_ids=blocked,
        )

    closure_ids = tuple(sorted(closure))
    application_order = _topological_delta_order(delta_by_id, frozenset(closure)) if closure else ()
    payload = _selection_payload(
        change_set_hash=changeset.content_hash,
        dispositions=decisions,
        closure_delta_ids=closure_ids,
        application_order=application_order,
    )
    return SelectionResolution(
        change_set_hash=changeset.content_hash,
        dispositions=decisions,
        closure_delta_ids=closure_ids,
        application_order=application_order,
        selection_hash=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        status=SelectionStatus.READY if closure else SelectionStatus.NO_CHANGE,
    )


def _revalidate_resolution(
    changeset: EvolutionChangeSet,
    resolution: SelectionResolution,
) -> SelectionResolution:
    if resolution.change_set_hash != changeset.content_hash:
        raise ChangeSetError("CS_CLOSURE_INVALID", "selection is bound to a different ChangeSet")
    recomputed = resolve_selection(
        changeset,
        {item.delta_id: item.disposition for item in resolution.dispositions},
    )
    if recomputed != resolution:
        raise ChangeSetError("CS_CLOSURE_INVALID", "selection closure, order, status, or digest is not canonical")
    return recomputed


def _apply_delta(
    *,
    delta: EvolutionDelta,
    modules: dict[str, MethodModule],
    edges: dict[str, MethodEdge],
    entry_module_ids: tuple[str, ...],
    exit_module_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    patch = delta.patch
    for expectation in patch.expectations:
        current = modules.get(expectation.object_id) if expectation.kind is ObjectKind.MODULE else edges.get(expectation.object_id)
        if current is None or object_content_hash(current) != expectation.sha256:
            raise ChangeSetError(
                "DELTA_PRECONDITION_MISMATCH",
                f"delta {delta.delta_id} expectation no longer matches {expectation.object_id}",
                delta_ids=(delta.delta_id,),
            )
    if patch.boundary is not None and (
        patch.boundary.expected_entry_module_ids != tuple(sorted(entry_module_ids))
        or patch.boundary.expected_exit_module_ids != tuple(sorted(exit_module_ids))
    ):
        raise ChangeSetError(
            "DELTA_PRECONDITION_MISMATCH",
            f"delta {delta.delta_id} boundary expectation no longer matches",
            delta_ids=(delta.delta_id,),
        )

    for edge_id in patch.remove_edge_ids:
        if edge_id not in edges:
            raise ChangeSetError(
                "DELTA_PRECONDITION_MISMATCH",
                f"delta {delta.delta_id} cannot remove missing edge {edge_id}",
                delta_ids=(delta.delta_id,),
            )
        del edges[edge_id]
    for module_id in patch.remove_module_ids:
        if module_id not in modules:
            raise ChangeSetError(
                "DELTA_PRECONDITION_MISMATCH",
                f"delta {delta.delta_id} cannot remove missing module {module_id}",
                delta_ids=(delta.delta_id,),
            )
        del modules[module_id]
    for module in patch.put_modules:
        if module.module_id in modules:
            expectation_hash = _expectations(patch).get((ObjectKind.MODULE, module.module_id))
            if delta.operation is not DeltaOperation.REPLACE or expectation_hash is None:
                raise ChangeSetError(
                    "DELTA_MATERIALIZATION_FAILED",
                    f"delta {delta.delta_id} would overwrite module {module.module_id} without exact replace authority",
                    delta_ids=(delta.delta_id,),
                )
        modules[module.module_id] = module
    for edge in patch.put_edges:
        if edge.edge_id in edges:
            raise ChangeSetError(
                "DELTA_MATERIALIZATION_FAILED",
                f"delta {delta.delta_id} would overwrite edge {edge.edge_id} without removal",
                delta_ids=(delta.delta_id,),
            )
        edges[edge.edge_id] = edge
    if patch.boundary is not None:
        entry_module_ids = patch.boundary.result_entry_module_ids
        exit_module_ids = patch.boundary.result_exit_module_ids
    return tuple(sorted(entry_module_ids)), tuple(sorted(exit_module_ids))


def _enforce_materialization_budget(
    *,
    changeset: EvolutionChangeSet,
    resolution: SelectionResolution,
    modules: Mapping[str, MethodModule],
    edges: Mapping[str, MethodEdge],
) -> None:
    budget = changeset.effective_budget
    delta_by_id = {item.delta_id: item for item in changeset.deltas}
    selected = tuple(delta_by_id[delta_id] for delta_id in resolution.closure_delta_ids)
    counts = {
        "max_deltas": len(selected),
        "max_dependencies": sum(len(item.depends_on) for item in selected),
        "max_conflicts": sum(len(item.conflicts_with) for item in selected) // 2,
        "max_modules_added": sum(len(item.patch.put_modules) for item in selected),
        "max_modules_removed": sum(len(item.patch.remove_module_ids) for item in selected),
        "max_edges_added": sum(len(item.patch.put_edges) for item in selected),
        "max_edges_removed": sum(len(item.patch.remove_edge_ids) for item in selected),
        "max_touched_objects": sum(len(_touched_set(item)) for item in selected),
        "max_result_modules": len(modules),
        "max_result_edges": len(edges),
    }
    exceeded = tuple(name for name, value in counts.items() if value > getattr(budget, name))
    analysis_steps = (
        len(selected)
        + counts["max_dependencies"] * max(1, len(selected))
        + counts["max_conflicts"] * 2
        + counts["max_touched_objects"] * max(1, len(selected))
        + len(modules)
        + len(edges)
    )
    if exceeded or analysis_steps > budget.max_analysis_steps:
        detail = ", ".join(exceeded) if exceeded else "max_analysis_steps"
        raise ChangeSetError("CS_BUDGET_EXCEEDED", f"materialization exceeds hard budget: {detail}")


def materialize_selection(
    base: MethodGraphVersion,
    changeset: EvolutionChangeSet,
    resolution: SelectionResolution,
    context: MaterializationContext | None = None,
) -> MaterializationReceipt | MaterializationFailureReceipt | NoChangeReceipt:
    """Purely materialize one exact selection and require fresh result evaluation."""
    if not isinstance(base, MethodGraphVersion) or not isinstance(changeset, EvolutionChangeSet) or not isinstance(resolution, SelectionResolution):
        raise ChangeSetError("CS_SCHEMA_INVALID", "materialize_selection requires typed base, ChangeSet, and selection")
    _validate_bound_policy(changeset)
    if base.content_hash != changeset.base_graph_hash:
        raise ChangeSetError("CS_BASE_MISMATCH", "ChangeSet base graph hash is stale or incorrect")
    canonical_resolution = _revalidate_resolution(changeset, resolution)
    if canonical_resolution.status is SelectionStatus.NO_CHANGE:
        disposition_by_id = {item.delta_id: item.disposition for item in canonical_resolution.dispositions}
        return NoChangeReceipt(
            base_graph_hash=base.content_hash,
            change_set_hash=changeset.content_hash,
            selection_hash=canonical_resolution.selection_hash,
            rejected_delta_ids=tuple(
                delta_id for delta_id, disposition in disposition_by_id.items() if disposition is Disposition.REJECTED
            ),
            deferred_delta_ids=tuple(
                delta_id for delta_id, disposition in disposition_by_id.items() if disposition is Disposition.DEFERRED
            ),
        )
    if not isinstance(context, MaterializationContext):
        raise ChangeSetError(
            "GRAPH_REVALIDATION_CONTEXT_MISSING",
            "non-empty materialization requires a strict validation context",
        )

    delta_by_id = {item.delta_id: item for item in changeset.deltas}
    modules = {item.module_id: item for item in base.modules}
    edges = {item.edge_id: item for item in base.edges}
    entry_module_ids = tuple(sorted(base.entry_module_ids))
    exit_module_ids = tuple(sorted(base.exit_module_ids))
    for delta_id in canonical_resolution.application_order:
        entry_module_ids, exit_module_ids = _apply_delta(
            delta=delta_by_id[delta_id],
            modules=modules,
            edges=edges,
            entry_module_ids=entry_module_ids,
            exit_module_ids=exit_module_ids,
        )
    _enforce_materialization_budget(
        changeset=changeset,
        resolution=canonical_resolution,
        modules=modules,
        edges=edges,
    )

    source_refs = tuple(sorted(set(base.lineage.source_refs) | set(changeset.provenance.source_refs)))
    try:
        candidate = replace(
            base,
            version_id=f"{base.graph_id}.candidate.{canonical_resolution.selection_hash[:24]}",
            modules=tuple(sorted(modules.values(), key=lambda item: item.module_id)),
            edges=tuple(sorted(edges.values(), key=lambda item: item.edge_id)),
            entry_module_ids=entry_module_ids,
            exit_module_ids=exit_module_ids,
            lineage=GraphLineage(
                parent_hashes=(base.content_hash,),
                supersedes_hashes=(),
                source_refs=source_refs,
                created_from_changeset=changeset.change_set_id,
                source_adapter_id=base.lineage.source_adapter_id,
                source_revision=base.lineage.source_revision,
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ChangeSetError("DELTA_MATERIALIZATION_FAILED", "selected deltas do not form a valid graph object") from exc

    diagnostics = validate_graph(
        candidate,
        task=context.task,
        known_refs=context.known_refs,
        policy=context.diagnostic_policy,
    )
    blocking = tuple(item for item in diagnostics if item.severity == "error")
    if blocking:
        ordered_issues = tuple(sorted(diagnostics, key=_diagnostic_sort_key))
        return MaterializationFailureReceipt(
            base_graph_hash=base.content_hash,
            change_set_hash=changeset.content_hash,
            selection_hash=canonical_resolution.selection_hash,
            closure_delta_ids=canonical_resolution.closure_delta_ids,
            attempted_candidate_hash=candidate.content_hash,
            validation_context_hash=context.content_hash,
            issues=ordered_issues,
            issues_digest=_issues_digest(ordered_issues),
        )

    evaluator_refs = tuple(sorted({
        ref
        for delta_id in canonical_resolution.closure_delta_ids
        for ref in delta_by_id[delta_id].evaluator_requirement_refs
    }))
    reevaluation = ReevaluationRequirement(
        base_graph_hash=base.content_hash,
        change_set_hash=changeset.content_hash,
        selection_hash=canonical_resolution.selection_hash,
        candidate_graph_hash=candidate.content_hash,
        evaluator_requirement_refs=evaluator_refs,
    )
    return MaterializationReceipt(
        base_graph_hash=base.content_hash,
        change_set_hash=changeset.content_hash,
        selection_hash=canonical_resolution.selection_hash,
        closure_delta_ids=canonical_resolution.closure_delta_ids,
        candidate_graph=candidate,
        candidate_graph_hash=candidate.content_hash,
        validation_context_hash=context.content_hash,
        diagnostics=diagnostics,
        reevaluation=reevaluation,
    )
