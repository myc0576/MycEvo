"""Immutable, runtime-neutral method-graph intermediate representation.

The IR describes workflow structure and permission boundaries. It deliberately
contains no execution hooks, model clients, network adapters, or private
workspace dependencies.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Mapping


SCHEMA_VERSION = 1
GRAPH_SCHEMA = "mycevo.method_graph.v1"
MODULE_SCHEMA = "mycevo.method_module.v1"
TASK_SCHEMA = "mycevo.task_contract.v1"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class IRValidationError(ValueError):
    """Raised when an IR object violates a portable static contract."""


class ModuleKind(str, Enum):
    INPUT = "input"
    TRANSFORM = "transform"
    DECISION = "decision"
    VALIDATION = "validation"
    MEMORY = "memory"
    OUTPUT = "output"
    ADAPTER = "adapter"


class EdgeKind(str, Enum):
    DATA = "data"
    SEQUENCE = "sequence"
    CONDITIONAL = "conditional"
    DEPENDS_ON = "depends_on"
    EVIDENCE_FOR = "evidence_for"
    GOVERNED_BY = "governed_by"
    READS_MEMORY = "reads_memory"
    WRITES_CANDIDATE_MEMORY = "writes_candidate_memory"
    CONDITION_TRUE = "condition_true"
    CONDITION_FALSE = "condition_false"
    PARALLEL = "parallel"
    LOOP = "loop"
    FALLBACK = "fallback"


class ControlKind(str, Enum):
    CONDITION = "condition"
    PARALLEL = "parallel"
    LOOP = "loop"
    FALLBACK = "fallback"


class SideEffect(str, Enum):
    READ_WORKSPACE = "read_workspace"
    WRITE_WORKSPACE = "write_workspace"
    SPAWN_PROCESS = "spawn_process"
    NETWORK = "network"
    EXTERNAL_SERVICE = "external_service"
    HUMAN_INTERACTION = "human_interaction"


class SideEffectClass(str, Enum):
    NONE = "none"
    CANDIDATE_ARTIFACT_WRITE = "candidate_artifact_write"
    AUDIT_APPEND_ONLY = "audit_append_only"
    CANDIDATE_MEMORY_WRITE = "candidate_memory_write"
    DECISION_RECORD_APPEND_ONLY = "decision_record_append_only"


class Permission(str, Enum):
    WORKSPACE_READ = "workspace_read"
    CANDIDATE_WRITE = "candidate_write"
    AUDIT_APPEND = "audit_append"
    CANDIDATE_MEMORY_WRITE = "candidate_memory_write"
    PROCESS_SPAWN = "process_spawn"
    NETWORK_ACCESS = "network_access"
    EXTERNAL_SERVICE_ACCESS = "external_service_access"
    HUMAN_INTERACTION = "human_interaction"
    AUTHORIZED_HUMAN_DECISION = "authorized_human_decision"


class CostLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Reversibility(str, Enum):
    CLEAN = "clean"
    APPEND_COMPENSATING_RECORD = "append_compensating_record"
    REVIEW_REQUIRED = "review_required"


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise IRValidationError(f"{field_name} must match {_IDENTIFIER.pattern}: {value!r}")
    return value


def _nonempty(value: str, field_name: str, *, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise IRValidationError(f"{field_name} must be non-empty and at most {maximum} characters")
    return value


def _unique(values: tuple[str, ...], field_name: str) -> None:
    try:
        unique_count = len(set(values))
    except TypeError as exc:
        raise IRValidationError(f"{field_name} must contain hashable scalar values") from exc
    if len(values) != unique_count:
        raise IRValidationError(f"{field_name} must contain unique values")


def _typed_tuple(values: tuple[Any, ...], expected: type[Any], field_name: str) -> None:
    if not all(isinstance(value, expected) for value in values):
        raise IRValidationError(f"{field_name} must contain only {expected.__name__} values")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IRValidationError(f"canonical JSON contains duplicate object key: {key!r}")
        result[key] = value
    return result


def _strict_keys(value: Mapping[str, Any], allowed: set[str], object_name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise IRValidationError(f"{object_name} contains unsupported fields: {sorted(unknown)}")


def _normalize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _normalize(value.to_dict())
    if is_dataclass(value):
        return _normalize({item.name: getattr(value, item.name) for item in fields(value)})
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise IRValidationError("canonical JSON object keys must be strings")
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise IRValidationError("floating-point values are forbidden in canonical IR; use integer units or strings")
    raise IRValidationError(f"unsupported canonical IR value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for supported immutable values."""
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class PortSpec:
    name: str
    data_type: str
    required: bool = True
    multiple: bool = False

    def __post_init__(self) -> None:
        _identifier(self.name, "port.name")
        _identifier(self.data_type, "port.data_type")
        if type(self.required) is not bool or type(self.multiple) is not bool:
            raise IRValidationError("port.required and port.multiple must be booleans")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "data_type": self.data_type, "required": self.required, "multiple": self.multiple}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PortSpec":
        _strict_keys(value, {"name", "data_type", "required", "multiple"}, "PortSpec")
        return cls(
            name=value["name"],
            data_type=value["data_type"],
            required=value.get("required", True),
            multiple=value.get("multiple", False),
        )


@dataclass(frozen=True, slots=True)
class ControlSpec:
    kind: ControlKind
    branch_labels: tuple[str, ...] = ()
    max_iterations: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ControlKind(self.kind))
        object.__setattr__(self, "branch_labels", tuple(self.branch_labels))
        for label in self.branch_labels:
            _identifier(label, "control.branch_labels")
        _unique(self.branch_labels, "control.branch_labels")
        if self.kind is ControlKind.LOOP:
            if type(self.max_iterations) is not int or not 1 <= self.max_iterations <= 10_000:
                raise IRValidationError("loop controls require max_iterations from 1 to 10000")
        elif self.max_iterations is not None:
            raise IRValidationError("max_iterations is valid only for loop controls")
        if self.kind in {ControlKind.CONDITION, ControlKind.FALLBACK} and not self.branch_labels:
            raise IRValidationError(f"{self.kind.value} controls require branch_labels")
        if self.kind is ControlKind.PARALLEL and len(self.branch_labels) < 2:
            raise IRValidationError("parallel controls require at least two branch_labels")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "branch_labels": list(self.branch_labels),
            "max_iterations": self.max_iterations,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ControlSpec":
        _strict_keys(value, {"kind", "branch_labels", "max_iterations"}, "ControlSpec")
        return cls(
            kind=ControlKind(value["kind"]),
            branch_labels=tuple(value.get("branch_labels", ())),
            max_iterations=value.get("max_iterations"),
        )


@dataclass(frozen=True, slots=True)
class EvidenceSpec:
    evidence_type: str
    role: str | None = None
    minimum_count: int = 1

    def __post_init__(self) -> None:
        _identifier(self.evidence_type, "evidence.evidence_type")
        if self.role is not None:
            _identifier(self.role, "evidence.role")
        if type(self.minimum_count) is not int or not 0 <= self.minimum_count <= 100_000:
            raise IRValidationError("evidence.minimum_count must be an integer from 0 to 100000")

    def to_dict(self) -> dict[str, Any]:
        return {"evidence_type": self.evidence_type, "role": self.role, "minimum_count": self.minimum_count}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceSpec":
        _strict_keys(value, {"evidence_type", "role", "minimum_count"}, "EvidenceSpec")
        return cls(
            evidence_type=value["evidence_type"],
            role=value.get("role"),
            minimum_count=value.get("minimum_count", 1),
        )


@dataclass(frozen=True, slots=True)
class MethodModule:
    module_id: str
    version_id: str
    kind: ModuleKind
    name: str
    description: str
    inputs: tuple[PortSpec, ...] = ()
    outputs: tuple[PortSpec, ...] = ()
    prerequisites: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    side_effects: tuple[SideEffect, ...] = ()
    applicability_conditions: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    required_evidence: tuple[EvidenceSpec, ...] = ()
    produced_evidence: tuple[EvidenceSpec, ...] = ()
    side_effect_class: SideEffectClass = SideEffectClass.NONE
    permission_requirements: tuple[Permission, ...] = ()
    failure_modes: tuple[str, ...] = ()
    counterexamples: tuple[str, ...] = ()
    fallback_refs: tuple[str, ...] = ()
    cost: CostLevel = CostLevel.LOW
    risk: RiskLevel = RiskLevel.LOW
    reversibility: Reversibility = Reversibility.CLEAN
    content_refs: tuple[str, ...] = ()
    rule_refs: tuple[str, ...] = ()
    memory_refs: tuple[str, ...] = ()
    control: ControlSpec | None = None
    contract_ref: str | None = None
    deprecated: bool = False
    schema: str = field(default=MODULE_SCHEMA, init=False)

    def __post_init__(self) -> None:
        _identifier(self.module_id, "module_id")
        _identifier(self.version_id, "module.version_id")
        object.__setattr__(self, "kind", ModuleKind(self.kind))
        _nonempty(self.name, "module.name", maximum=160)
        _nonempty(self.description, "module.description")
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "outputs", tuple(self.outputs))
        object.__setattr__(self, "prerequisites", tuple(self.prerequisites))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "side_effects", tuple(SideEffect(item) for item in self.side_effects))
        _typed_tuple(self.inputs, PortSpec, "module.inputs")
        _typed_tuple(self.outputs, PortSpec, "module.outputs")
        for name in (
            "applicability_conditions", "preconditions", "postconditions", "failure_modes", "counterexamples",
            "fallback_refs", "content_refs", "rule_refs", "memory_refs",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
            _unique(getattr(self, name), f"module.{name}")
        object.__setattr__(self, "required_evidence", tuple(self.required_evidence))
        object.__setattr__(self, "produced_evidence", tuple(self.produced_evidence))
        _typed_tuple(self.required_evidence, EvidenceSpec, "module.required_evidence")
        _typed_tuple(self.produced_evidence, EvidenceSpec, "module.produced_evidence")
        object.__setattr__(self, "side_effect_class", SideEffectClass(self.side_effect_class))
        object.__setattr__(self, "permission_requirements", tuple(Permission(item) for item in self.permission_requirements))
        object.__setattr__(self, "cost", CostLevel(self.cost))
        object.__setattr__(self, "risk", RiskLevel(self.risk))
        object.__setattr__(self, "reversibility", Reversibility(self.reversibility))
        _unique(tuple(port.name for port in self.inputs), "module.inputs")
        _unique(tuple(port.name for port in self.outputs), "module.outputs")
        for item in self.prerequisites:
            _identifier(item, "module.prerequisites")
        _unique(self.prerequisites, "module.prerequisites")
        for item in self.evidence_refs:
            _nonempty(item, "module.evidence_refs", maximum=512)
        _unique(self.evidence_refs, "module.evidence_refs")
        _unique(tuple(item.value for item in self.side_effects), "module.side_effects")
        _unique(tuple(item.value for item in self.permission_requirements), "module.permission_requirements")
        for name in ("applicability_conditions", "preconditions", "postconditions", "failure_modes", "counterexamples"):
            for item in getattr(self, name):
                _nonempty(item, f"module.{name}")
        for name in ("fallback_refs", "content_refs", "rule_refs", "memory_refs"):
            for item in getattr(self, name):
                _identifier(item, f"module.{name}")
        if self.contract_ref is not None:
            _nonempty(self.contract_ref, "module.contract_ref", maximum=512)
        if type(self.deprecated) is not bool:
            raise IRValidationError("module.deprecated must be a boolean")
        self._validate_safety_contract()

    def _validate_safety_contract(self) -> None:
        permissions = set(self.permission_requirements)
        effects = set(self.side_effects)
        permission_effects = {
            Permission.WORKSPACE_READ: SideEffect.READ_WORKSPACE,
            Permission.PROCESS_SPAWN: SideEffect.SPAWN_PROCESS,
            Permission.NETWORK_ACCESS: SideEffect.NETWORK,
            Permission.EXTERNAL_SERVICE_ACCESS: SideEffect.EXTERNAL_SERVICE,
            Permission.HUMAN_INTERACTION: SideEffect.HUMAN_INTERACTION,
            Permission.AUTHORIZED_HUMAN_DECISION: SideEffect.HUMAN_INTERACTION,
        }
        for permission, effect in permission_effects.items():
            if (permission in permissions) != (effect in effects and (
                permission is not Permission.HUMAN_INTERACTION
                or Permission.AUTHORIZED_HUMAN_DECISION not in permissions
            )):
                if permission is Permission.HUMAN_INTERACTION and Permission.AUTHORIZED_HUMAN_DECISION in permissions:
                    continue
                raise IRValidationError(f"module safety contract mismatch for {permission.value}/{effect.value}")
        write_permissions = {
            Permission.CANDIDATE_WRITE,
            Permission.AUDIT_APPEND,
            Permission.CANDIDATE_MEMORY_WRITE,
        }
        if bool(permissions & write_permissions) != (SideEffect.WRITE_WORKSPACE in effects):
            raise IRValidationError("module write permissions and write_workspace side effect must agree")
        required_by_class = {
            SideEffectClass.NONE: frozenset(),
            SideEffectClass.CANDIDATE_ARTIFACT_WRITE: frozenset({Permission.CANDIDATE_WRITE}),
            SideEffectClass.AUDIT_APPEND_ONLY: frozenset({Permission.AUDIT_APPEND}),
            SideEffectClass.CANDIDATE_MEMORY_WRITE: frozenset({Permission.CANDIDATE_MEMORY_WRITE}),
            SideEffectClass.DECISION_RECORD_APPEND_ONLY: frozenset({Permission.AUDIT_APPEND, Permission.AUTHORIZED_HUMAN_DECISION}),
        }
        required = required_by_class[self.side_effect_class]
        if not required <= permissions:
            raise IRValidationError(f"module {self.side_effect_class.value} lacks required permissions")
        if self.side_effect_class is SideEffectClass.NONE and permissions & write_permissions:
            raise IRValidationError("module side_effect_class none cannot declare write permissions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "module_id": self.module_id,
            "version_id": self.version_id,
            "kind": self.kind.value,
            "name": self.name,
            "description": self.description,
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "prerequisites": list(self.prerequisites),
            "evidence_refs": list(self.evidence_refs),
            "side_effects": [item.value for item in self.side_effects],
            "applicability_conditions": list(self.applicability_conditions),
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "required_evidence": [item.to_dict() for item in self.required_evidence],
            "produced_evidence": [item.to_dict() for item in self.produced_evidence],
            "side_effect_class": self.side_effect_class.value,
            "permission_requirements": [item.value for item in self.permission_requirements],
            "failure_modes": list(self.failure_modes),
            "counterexamples": list(self.counterexamples),
            "fallback_refs": list(self.fallback_refs),
            "cost": self.cost.value,
            "risk": self.risk.value,
            "reversibility": self.reversibility.value,
            "content_refs": list(self.content_refs),
            "rule_refs": list(self.rule_refs),
            "memory_refs": list(self.memory_refs),
            "control": self.control.to_dict() if self.control else None,
            "contract_ref": self.contract_ref,
            "deprecated": self.deprecated,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MethodModule":
        allowed = {
            "schema", "module_id", "version_id", "kind", "name", "description", "inputs", "outputs",
            "prerequisites", "evidence_refs", "side_effects", "applicability_conditions", "preconditions",
            "postconditions", "required_evidence", "produced_evidence", "side_effect_class",
            "permission_requirements", "failure_modes", "counterexamples", "fallback_refs", "cost", "risk",
            "reversibility", "content_refs", "rule_refs", "memory_refs", "control", "contract_ref", "deprecated",
        }
        _strict_keys(value, allowed, "MethodModule")
        if value.get("schema") != MODULE_SCHEMA:
            raise IRValidationError("unsupported MethodModule schema")
        return cls(
            module_id=value["module_id"],
            version_id=value["version_id"],
            kind=ModuleKind(value["kind"]),
            name=value["name"],
            description=value["description"],
            inputs=tuple(PortSpec.from_dict(item) for item in value.get("inputs", ())),
            outputs=tuple(PortSpec.from_dict(item) for item in value.get("outputs", ())),
            prerequisites=tuple(value.get("prerequisites", ())),
            evidence_refs=tuple(value.get("evidence_refs", ())),
            side_effects=tuple(SideEffect(item) for item in value.get("side_effects", ())),
            applicability_conditions=tuple(value.get("applicability_conditions", ())),
            preconditions=tuple(value.get("preconditions", ())),
            postconditions=tuple(value.get("postconditions", ())),
            required_evidence=tuple(EvidenceSpec.from_dict(item) for item in value.get("required_evidence", ())),
            produced_evidence=tuple(EvidenceSpec.from_dict(item) for item in value.get("produced_evidence", ())),
            side_effect_class=SideEffectClass(value.get("side_effect_class", "none")),
            permission_requirements=tuple(Permission(item) for item in value.get("permission_requirements", ())),
            failure_modes=tuple(value.get("failure_modes", ())),
            counterexamples=tuple(value.get("counterexamples", ())),
            fallback_refs=tuple(value.get("fallback_refs", ())),
            cost=CostLevel(value.get("cost", "low")),
            risk=RiskLevel(value.get("risk", "low")),
            reversibility=Reversibility(value.get("reversibility", "clean")),
            content_refs=tuple(value.get("content_refs", ())),
            rule_refs=tuple(value.get("rule_refs", ())),
            memory_refs=tuple(value.get("memory_refs", ())),
            control=ControlSpec.from_dict(value["control"]) if value.get("control") else None,
            contract_ref=value.get("contract_ref"),
            deprecated=value.get("deprecated", False),
        )


@dataclass(frozen=True, slots=True)
class MethodEdge:
    edge_id: str
    kind: EdgeKind
    source_module: str
    target_module: str
    source_port: str | None = None
    target_port: str | None = None
    label: str | None = None
    condition_ref: str | None = None
    branch_label: str | None = None
    join_key: str | None = None
    max_iterations: int | None = None

    def __post_init__(self) -> None:
        _identifier(self.edge_id, "edge_id")
        object.__setattr__(self, "kind", EdgeKind(self.kind))
        _identifier(self.source_module, "edge.source_module")
        _identifier(self.target_module, "edge.target_module")
        if self.kind is EdgeKind.DATA:
            if not self.source_port or not self.target_port:
                raise IRValidationError("data edges require source_port and target_port")
        elif self.source_port is not None or self.target_port is not None:
            raise IRValidationError("ports are valid only on data edges")
        if self.source_port:
            _identifier(self.source_port, "edge.source_port")
        if self.target_port:
            _identifier(self.target_port, "edge.target_port")
        if self.source_module == self.target_module and self.kind is not EdgeKind.LOOP:
            raise IRValidationError("only loop edges may target their own source module")
        if self.label is not None:
            _identifier(self.label, "edge.label")
        for name in ("condition_ref", "branch_label", "join_key"):
            value = getattr(self, name)
            if value is not None:
                _identifier(value, f"edge.{name}")
        if self.kind is EdgeKind.LOOP:
            if type(self.max_iterations) is not int or not 1 <= self.max_iterations <= 10_000:
                raise IRValidationError("loop edges require max_iterations from 1 to 10000")
        elif self.max_iterations is not None:
            raise IRValidationError("max_iterations is valid only for loop edges")
        if self.kind in {EdgeKind.CONDITIONAL, EdgeKind.CONDITION_TRUE, EdgeKind.CONDITION_FALSE, EdgeKind.FALLBACK}:
            if not self.condition_ref or not self.branch_label:
                raise IRValidationError(f"{self.kind.value} edges require condition_ref and branch_label")
        if self.kind is EdgeKind.PARALLEL and (not self.branch_label or not self.join_key):
            raise IRValidationError("parallel edges require branch_label and join_key")

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "kind": self.kind.value,
            "source_module": self.source_module,
            "target_module": self.target_module,
            "source_port": self.source_port,
            "target_port": self.target_port,
            "label": self.label,
            "condition_ref": self.condition_ref,
            "branch_label": self.branch_label,
            "join_key": self.join_key,
            "max_iterations": self.max_iterations,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MethodEdge":
        _strict_keys(
            value,
            {"edge_id", "kind", "source_module", "target_module", "source_port", "target_port", "label", "condition_ref", "branch_label", "join_key", "max_iterations"},
            "MethodEdge",
        )
        return cls(
            edge_id=value["edge_id"],
            kind=EdgeKind(value["kind"]),
            source_module=value["source_module"],
            target_module=value["target_module"],
            source_port=value.get("source_port"),
            target_port=value.get("target_port"),
            label=value.get("label"),
            condition_ref=value.get("condition_ref"),
            branch_label=value.get("branch_label"),
            join_key=value.get("join_key"),
            max_iterations=value.get("max_iterations"),
        )


@dataclass(frozen=True, slots=True)
class GraphLineage:
    parent_hashes: tuple[str, ...] = ()
    supersedes_hashes: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    created_from_changeset: str | None = None
    source_adapter_id: str | None = None
    source_revision: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parent_hashes", tuple(self.parent_hashes))
        object.__setattr__(self, "supersedes_hashes", tuple(self.supersedes_hashes))
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        for value in (*self.parent_hashes, *self.supersedes_hashes):
            if not _SHA256.fullmatch(value):
                raise IRValidationError("lineage hashes must be lowercase SHA-256 values")
        _unique(self.parent_hashes, "lineage.parent_hashes")
        _unique(self.supersedes_hashes, "lineage.supersedes_hashes")
        for value in self.source_refs:
            _nonempty(value, "lineage.source_refs", maximum=512)
        _unique(self.source_refs, "lineage.source_refs")
        if self.created_from_changeset is not None:
            _identifier(self.created_from_changeset, "lineage.created_from_changeset")
        if self.source_adapter_id is not None:
            _identifier(self.source_adapter_id, "lineage.source_adapter_id")
        if self.source_revision is not None:
            _nonempty(self.source_revision, "lineage.source_revision", maximum=256)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_hashes": sorted(self.parent_hashes),
            "supersedes_hashes": sorted(self.supersedes_hashes),
            "source_refs": sorted(self.source_refs),
            "created_from_changeset": self.created_from_changeset,
            "source_adapter_id": self.source_adapter_id,
            "source_revision": self.source_revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GraphLineage":
        _strict_keys(value, {"parent_hashes", "supersedes_hashes", "source_refs", "created_from_changeset", "source_adapter_id", "source_revision"}, "GraphLineage")
        return cls(
            parent_hashes=tuple(value.get("parent_hashes", ())),
            supersedes_hashes=tuple(value.get("supersedes_hashes", ())),
            source_refs=tuple(value.get("source_refs", ())),
            created_from_changeset=value.get("created_from_changeset"),
            source_adapter_id=value.get("source_adapter_id"),
            source_revision=value.get("source_revision"),
        )


@dataclass(frozen=True, slots=True)
class TaskContract:
    task_id: str
    objective: str
    required_inputs: tuple[PortSpec, ...] = ()
    required_outputs: tuple[PortSpec, ...] = ()
    constraints: tuple[str, ...] = ()
    allowed_side_effects: tuple[SideEffect, ...] = ()
    max_modules: int = 128
    max_edges: int = 512
    schema: str = field(default=TASK_SCHEMA, init=False)

    def __post_init__(self) -> None:
        _identifier(self.task_id, "task_id")
        _nonempty(self.objective, "task.objective")
        object.__setattr__(self, "required_inputs", tuple(self.required_inputs))
        object.__setattr__(self, "required_outputs", tuple(self.required_outputs))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "allowed_side_effects", tuple(SideEffect(item) for item in self.allowed_side_effects))
        _typed_tuple(self.required_inputs, PortSpec, "task.required_inputs")
        _typed_tuple(self.required_outputs, PortSpec, "task.required_outputs")
        if type(self.max_modules) is not int or not 1 <= self.max_modules <= 10_000:
            raise IRValidationError("max_modules must be an integer from 1 to 10000")
        if type(self.max_edges) is not int or not 0 <= self.max_edges <= 100_000:
            raise IRValidationError("max_edges must be an integer from 0 to 100000")
        _unique(tuple(item.name for item in self.required_inputs), "task.required_inputs")
        _unique(tuple(item.name for item in self.required_outputs), "task.required_outputs")
        for item in self.constraints:
            _nonempty(item, "task.constraints")
        _unique(self.constraints, "task.constraints")
        _unique(tuple(item.value for item in self.allowed_side_effects), "task.allowed_side_effects")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "objective": self.objective,
            "required_inputs": [item.to_dict() for item in self.required_inputs],
            "required_outputs": [item.to_dict() for item in self.required_outputs],
            "constraints": list(self.constraints),
            "allowed_side_effects": sorted(item.value for item in self.allowed_side_effects),
            "max_modules": self.max_modules,
            "max_edges": self.max_edges,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskContract":
        allowed = {
            "schema", "task_id", "objective", "required_inputs", "required_outputs", "constraints",
            "allowed_side_effects", "max_modules", "max_edges",
        }
        _strict_keys(value, allowed, "TaskContract")
        if value.get("schema") != TASK_SCHEMA:
            raise IRValidationError("unsupported TaskContract schema")
        return cls(
            task_id=value["task_id"],
            objective=value["objective"],
            required_inputs=tuple(PortSpec.from_dict(item) for item in value.get("required_inputs", ())),
            required_outputs=tuple(PortSpec.from_dict(item) for item in value.get("required_outputs", ())),
            constraints=tuple(value.get("constraints", ())),
            allowed_side_effects=tuple(SideEffect(item) for item in value.get("allowed_side_effects", ())),
            max_modules=value.get("max_modules", 128),
            max_edges=value.get("max_edges", 512),
        )


@dataclass(frozen=True, slots=True)
class MethodGraphVersion:
    graph_id: str
    version_id: str
    purpose: str
    modules: tuple[MethodModule, ...]
    edges: tuple[MethodEdge, ...]
    entry_module_ids: tuple[str, ...]
    exit_module_ids: tuple[str, ...]
    lineage: GraphLineage = field(default_factory=GraphLineage)
    task_contract_ref: str | None = None
    applicability_conditions: tuple[str, ...] = ()
    schema: str = field(default=GRAPH_SCHEMA, init=False)

    def __post_init__(self) -> None:
        _identifier(self.graph_id, "graph_id")
        _identifier(self.version_id, "graph.version_id")
        _nonempty(self.purpose, "graph.purpose")
        object.__setattr__(self, "modules", tuple(self.modules))
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(self, "entry_module_ids", tuple(self.entry_module_ids))
        object.__setattr__(self, "exit_module_ids", tuple(self.exit_module_ids))
        object.__setattr__(self, "applicability_conditions", tuple(self.applicability_conditions))
        _typed_tuple(self.modules, MethodModule, "graph.modules")
        _typed_tuple(self.edges, MethodEdge, "graph.edges")
        if not isinstance(self.lineage, GraphLineage):
            raise IRValidationError("graph.lineage must be a GraphLineage")
        _unique(self.applicability_conditions, "graph.applicability_conditions")
        for item in self.applicability_conditions:
            _nonempty(item, "graph.applicability_conditions")
        module_ids = tuple(item.module_id for item in self.modules)
        edge_ids = tuple(item.edge_id for item in self.edges)
        _unique(module_ids, "graph.modules")
        _unique(edge_ids, "graph.edges")
        for item in self.entry_module_ids:
            _identifier(item, "graph.entry_module_ids")
        for item in self.exit_module_ids:
            _identifier(item, "graph.exit_module_ids")
        _unique(self.entry_module_ids, "graph.entry_module_ids")
        _unique(self.exit_module_ids, "graph.exit_module_ids")
        if not self.modules:
            raise IRValidationError("graphs require at least one module")
        known = set(module_ids)
        if not self.entry_module_ids or not set(self.entry_module_ids) <= known:
            raise IRValidationError("entry_module_ids must be non-empty references to graph modules")
        if not self.exit_module_ids or not set(self.exit_module_ids) <= known:
            raise IRValidationError("exit_module_ids must be non-empty references to graph modules")
        module_by_id = {item.module_id: item for item in self.modules}
        for module in self.modules:
            if not set(module.prerequisites) <= known:
                raise IRValidationError(f"module {module.module_id} references unknown prerequisites")
        for edge in self.edges:
            if edge.source_module not in known or edge.target_module not in known:
                raise IRValidationError(f"edge {edge.edge_id} references an unknown module")
            if edge.kind is EdgeKind.DATA:
                source_ports = {item.name: item for item in module_by_id[edge.source_module].outputs}
                target_ports = {item.name: item for item in module_by_id[edge.target_module].inputs}
                if edge.source_port not in source_ports or edge.target_port not in target_ports:
                    raise IRValidationError(f"edge {edge.edge_id} references an unknown port")
                if source_ports[edge.source_port].data_type != target_ports[edge.target_port].data_type:
                    raise IRValidationError(f"edge {edge.edge_id} connects incompatible port types")
            if edge.kind is EdgeKind.WRITES_CANDIDATE_MEMORY:
                source = module_by_id[edge.source_module]
                if (
                    source.side_effect_class is not SideEffectClass.CANDIDATE_MEMORY_WRITE
                    or Permission.CANDIDATE_MEMORY_WRITE not in source.permission_requirements
                    or SideEffect.WRITE_WORKSPACE not in source.side_effects
                ):
                    raise IRValidationError(f"edge {edge.edge_id} requires a candidate-memory source contract")
            if edge.kind is EdgeKind.READS_MEMORY and not module_by_id[edge.source_module].memory_refs:
                raise IRValidationError(f"edge {edge.edge_id} requires source memory_refs")
        self._validate_control_contracts(module_by_id)
        if self.task_contract_ref is not None:
            _nonempty(self.task_contract_ref, "graph.task_contract_ref", maximum=512)

    def _validate_control_contracts(self, module_by_id: Mapping[str, MethodModule]) -> None:
        controlled_kinds = {
            EdgeKind.CONDITIONAL,
            EdgeKind.CONDITION_TRUE,
            EdgeKind.CONDITION_FALSE,
            EdgeKind.PARALLEL,
            EdgeKind.LOOP,
            EdgeKind.FALLBACK,
        }
        for module_id, module in module_by_id.items():
            outgoing = tuple(edge for edge in self.edges if edge.source_module == module_id)
            controlled = tuple(edge for edge in outgoing if edge.kind in controlled_kinds)
            if module.control is None:
                if controlled:
                    raise IRValidationError(f"module {module_id} has control edges without a control contract")
                continue
            if module.control.kind is ControlKind.LOOP:
                loops = tuple(edge for edge in controlled if edge.kind is EdgeKind.LOOP)
                if not loops or any(edge.max_iterations != module.control.max_iterations for edge in loops):
                    raise IRValidationError(f"module {module_id} loop bounds do not match its control contract")
                if any(edge.kind is EdgeKind.PARALLEL for edge in controlled):
                    raise IRValidationError(f"module {module_id} loop control cannot also declare parallel edges")
                exits = tuple(edge for edge in controlled if edge.kind is not EdgeKind.LOOP)
                if exits or module.control.branch_labels:
                    labels = tuple(edge.branch_label for edge in controlled)
                    if None in labels or len(labels) != len(set(labels)) or set(labels) != set(module.control.branch_labels):
                        raise IRValidationError(f"module {module_id} loop branch labels do not match its control edges")
            elif module.control.kind in {ControlKind.CONDITION, ControlKind.FALLBACK}:
                allowed = (
                    {EdgeKind.FALLBACK}
                    if module.control.kind is ControlKind.FALLBACK
                    else {EdgeKind.CONDITIONAL, EdgeKind.CONDITION_TRUE, EdgeKind.CONDITION_FALSE, EdgeKind.FALLBACK}
                )
                branches = tuple(edge for edge in controlled if edge.kind in allowed)
                if len(branches) != len(controlled):
                    raise IRValidationError(f"module {module_id} mixes incompatible control edge kinds")
                actual_labels = tuple(edge.branch_label for edge in branches)
                if not branches or None in actual_labels or set(actual_labels) != set(module.control.branch_labels):
                    raise IRValidationError(f"module {module_id} branch labels do not match its control edges")
                if len(actual_labels) != len(set(actual_labels)):
                    raise IRValidationError(f"module {module_id} control branches must be unique")
            elif module.control.kind is ControlKind.PARALLEL:
                parallel = tuple(edge for edge in controlled if edge.kind is EdgeKind.PARALLEL)
                if len(parallel) != len(controlled):
                    raise IRValidationError(f"module {module_id} mixes incompatible control edge kinds")
                labels = tuple(edge.branch_label for edge in parallel)
                joins = {edge.join_key for edge in parallel}
                if len(parallel) < 2 or None in labels or set(labels) != set(module.control.branch_labels):
                    raise IRValidationError(f"module {module_id} parallel branches do not match its control contract")
                if len(labels) != len(set(labels)) or len(joins) != 1:
                    raise IRValidationError(f"module {module_id} parallel branches require unique labels and one join_key")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "graph_id": self.graph_id,
            "version_id": self.version_id,
            "purpose": self.purpose,
            "modules": [item.to_dict() for item in sorted(self.modules, key=lambda item: item.module_id)],
            "edges": [item.to_dict() for item in sorted(self.edges, key=lambda item: item.edge_id)],
            "entry_module_ids": sorted(self.entry_module_ids),
            "exit_module_ids": sorted(self.exit_module_ids),
            "lineage": self.lineage.to_dict(),
            "task_contract_ref": self.task_contract_ref,
            "applicability_conditions": list(self.applicability_conditions),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MethodGraphVersion":
        allowed = {
            "schema", "graph_id", "version_id", "purpose", "modules", "edges", "entry_module_ids", "exit_module_ids",
            "lineage", "task_contract_ref", "applicability_conditions",
        }
        _strict_keys(value, allowed, "MethodGraphVersion")
        if value.get("schema") != GRAPH_SCHEMA:
            raise IRValidationError("unsupported MethodGraphVersion schema")
        return cls(
            graph_id=value["graph_id"],
            version_id=value["version_id"],
            purpose=value["purpose"],
            modules=tuple(MethodModule.from_dict(item) for item in value["modules"]),
            edges=tuple(MethodEdge.from_dict(item) for item in value.get("edges", ())),
            entry_module_ids=tuple(value["entry_module_ids"]),
            exit_module_ids=tuple(value["exit_module_ids"]),
            lineage=GraphLineage.from_dict(value.get("lineage", {})),
            task_contract_ref=value.get("task_contract_ref"),
            applicability_conditions=tuple(value.get("applicability_conditions", ())),
        )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


def graph_from_json_bytes(payload: bytes) -> MethodGraphVersion:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IRValidationError("graph payload must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise IRValidationError("graph payload root must be an object")
    return MethodGraphVersion.from_dict(value)


def check_task_fitness(graph: MethodGraphVersion, task: TaskContract) -> tuple[str, ...]:
    """Return stable blocking gap codes without executing or importing adapters."""
    gaps: list[str] = []
    if len(graph.modules) > task.max_modules:
        gaps.append("ADAPTER_MODULE_BUDGET_EXCEEDED")
    if len(graph.edges) > task.max_edges:
        gaps.append("ADAPTER_EDGE_BUDGET_EXCEEDED")
    actual_effects = {effect for module in graph.modules for effect in module.side_effects}
    if not actual_effects <= set(task.allowed_side_effects):
        gaps.append("ADAPTER_SIDE_EFFECT_UNAUTHORIZED")
    entry_inputs = {port.data_type for module in graph.modules if module.module_id in graph.entry_module_ids for port in module.inputs}
    required_inputs = {port.data_type for port in task.required_inputs if port.required}
    if not required_inputs <= entry_inputs:
        gaps.append("ADAPTER_REQUIRED_INPUT_UNBOUND")
    exit_outputs = {port.data_type for module in graph.modules if module.module_id in graph.exit_module_ids for port in module.outputs}
    required_outputs = {port.data_type for port in task.required_outputs if port.required}
    if not required_outputs <= exit_outputs:
        gaps.append("ADAPTER_REQUIRED_OUTPUT_UNBOUND")
    return tuple(sorted(set(gaps)))
