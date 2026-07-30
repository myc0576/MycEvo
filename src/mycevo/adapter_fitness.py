"""Static adapter normalization and computed schema-freeze fitness.

Adapter-owned runtime bindings, raw conditions, and domain extensions are
deliberately excluded from the portable graph and its content hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .workflow_ir import (
    ControlKind,
    ControlSpec,
    CostLevel,
    EdgeKind,
    EvidenceSpec,
    GraphLineage,
    IRValidationError,
    MethodEdge,
    MethodGraphVersion,
    MethodModule,
    ModuleKind,
    Permission,
    PortSpec,
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectClass,
    canonical_sha256,
)


REQUIRED_ADAPTER_COVERAGE = frozenset(
    {
        "candidate_memory",
        "conditional",
        "evidence_binding",
        "fallback",
        "human_gate",
        "lineage_identity",
        "loop",
        "parallel",
        "prerequisites",
        "side_effect_permissions",
        "topology",
        "typed_ports",
    }
)


@dataclass(frozen=True, slots=True)
class AdapterFreezeReport:
    adapter_ids: tuple[str, ...]
    source_kinds: tuple[str, ...]
    graph_hashes: tuple[str, ...]
    coverage: tuple[str, ...]
    blocking_gaps: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blocking_gaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_ids": list(self.adapter_ids),
            "source_kinds": list(self.source_kinds),
            "graph_hashes": list(self.graph_hashes),
            "coverage": list(self.coverage),
            "blocking_gaps": list(self.blocking_gaps),
            "ready": self.ready,
        }


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IRValidationError(f"{name} must be an object")
    return value


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _preflight_gap_codes(root: Mapping[str, Any]) -> tuple[str, ...]:
    gaps: set[str] = set()
    if root.get("schema_version") != "1.0":
        gaps.add("ADAPTER_SCHEMA_VERSION_UNSUPPORTED")
    try:
        adapter = _mapping(root.get("adapter"), "adapter")
        sanitization = _mapping(adapter.get("sanitization"), "adapter.sanitization")
        fitness = _mapping(root.get("fitness"), "fitness")
    except IRValidationError:
        return ("ADAPTER_FIXTURE_MALFORMED",)
    required_safety = {
        "synthetic_only": True,
        "contains_private_paths": False,
        "contains_private_data": False,
        "contains_secrets": False,
    }
    if any(sanitization.get(key) is not expected for key, expected in required_safety.items()):
        gaps.add("ADAPTER_FIXTURE_UNSAFE")
    if fitness.get("blocking_gaps") != []:
        gaps.add("ADAPTER_DECLARED_BLOCKING_GAPS")
    if fitness.get("schema_freeze_verdict") != "ready":
        gaps.add("ADAPTER_DECLARED_NOT_READY")
    leakage = fitness.get("domain_leakage")
    if not isinstance(leakage, Mapping) or leakage.get("forbidden_core_keys_present") != []:
        gaps.add("ADAPTER_DOMAIN_LEAKAGE_DECLARED")

    refs = adapter.get("source_refs")
    pins = adapter.get("source_pins")
    if not isinstance(refs, list) or not refs or not all(isinstance(item, str) and item for item in refs):
        gaps.add("ADAPTER_SOURCE_REFS_INVALID")
        refs = []
    if not isinstance(pins, list) or not pins:
        gaps.add("ADAPTER_SOURCE_REFS_UNPINNED")
        pins = []
    normalized_pins: list[dict[str, str]] = []
    for raw_pin in pins:
        if not isinstance(raw_pin, Mapping) or not isinstance(raw_pin.get("ref"), str) or not _sha256(raw_pin.get("sha256")):
            gaps.add("ADAPTER_SOURCE_PIN_INVALID")
            continue
        normalized_pins.append({"ref": raw_pin["ref"], "sha256": raw_pin["sha256"]})
    pin_refs = [item["ref"] for item in normalized_pins]
    if len(pin_refs) != len(set(pin_refs)) or set(pin_refs) != set(refs):
        gaps.add("ADAPTER_SOURCE_PIN_COVERAGE_INCOMPLETE")

    source_kind = adapter.get("source_kind")
    revision = adapter.get("source_revision")
    if source_kind == "public_repository":
        if not isinstance(revision, str) or len(revision) != 40 or not all(character in "0123456789abcdef" for character in revision):
            gaps.add("ADAPTER_SOURCE_REVISION_MUTABLE")
        elif any(revision not in ref for ref in refs):
            gaps.add("ADAPTER_SOURCE_REVISION_MISMATCH")
    elif source_kind == "local_public_workflow":
        if not isinstance(revision, str) or not revision.startswith("sha256:") or not _sha256(revision[7:]):
            gaps.add("ADAPTER_SOURCE_REVISION_MUTABLE")
        elif normalized_pins and revision[7:] != canonical_sha256(normalized_pins):
            gaps.add("ADAPTER_SOURCE_REVISION_MISMATCH")
    else:
        gaps.add("ADAPTER_SOURCE_KIND_UNSUPPORTED")
    return tuple(sorted(gaps))


def _side_effects(module: Mapping[str, Any]) -> tuple[SideEffect, ...]:
    permissions = {Permission(value) for value in module.get("permission_requirements", ())}
    effects: set[SideEffect] = set()
    permission_effects = {
        Permission.WORKSPACE_READ: SideEffect.READ_WORKSPACE,
        Permission.PROCESS_SPAWN: SideEffect.SPAWN_PROCESS,
        Permission.NETWORK_ACCESS: SideEffect.NETWORK,
        Permission.EXTERNAL_SERVICE_ACCESS: SideEffect.EXTERNAL_SERVICE,
        Permission.HUMAN_INTERACTION: SideEffect.HUMAN_INTERACTION,
        Permission.AUTHORIZED_HUMAN_DECISION: SideEffect.HUMAN_INTERACTION,
    }
    for permission, effect in permission_effects.items():
        if permission in permissions:
            effects.add(effect)
    if permissions & {Permission.CANDIDATE_WRITE, Permission.AUDIT_APPEND, Permission.CANDIDATE_MEMORY_WRITE}:
        effects.add(SideEffect.WRITE_WORKSPACE)
    return tuple(sorted(effects, key=lambda value: value.value))


def _evidence(values: Any) -> tuple[EvidenceSpec, ...]:
    result: list[EvidenceSpec] = []
    for raw in values or ():
        item = _mapping(raw, "evidence")
        result.append(EvidenceSpec(item["type"], item.get("role"), item.get("minimum_count", 1)))
    return tuple(result)


def _memory_refs(values: Any) -> tuple[str, ...]:
    return tuple(_mapping(item, "memory_ref")["ref"] if isinstance(item, Mapping) else item for item in values or ())


def _branch_label(edge: Mapping[str, Any]) -> str | None:
    if edge.get("branch") is not None:
        return edge["branch"]
    if edge.get("edge_type") in {"conditional", "condition_true", "condition_false", "fallback"}:
        return edge["edge_id"]
    if edge.get("edge_type") == "loop" and edge.get("condition") is not None:
        return edge["edge_id"]
    return None


def _control(module_id: str, edges: tuple[Mapping[str, Any], ...]) -> ControlSpec | None:
    outgoing = tuple(edge for edge in edges if edge.get("source_module_id") == module_id)
    controlled = tuple(edge for edge in outgoing if edge.get("edge_type") in {"conditional", "condition_true", "condition_false", "fallback", "parallel", "loop"})
    loops = tuple(edge for edge in controlled if edge.get("edge_type") == "loop")
    if loops:
        if len(loops) != 1:
            raise IRValidationError(f"module {module_id} must not declare multiple loop edges")
        labels = tuple(label for edge in controlled if (label := _branch_label(edge)) is not None)
        return ControlSpec(ControlKind.LOOP, branch_labels=labels, max_iterations=loops[0].get("max_iterations"))
    branches = tuple(edge for edge in controlled if edge.get("edge_type") in {"conditional", "condition_true", "condition_false", "fallback"})
    if branches:
        kind = ControlKind.CONDITION if any(edge.get("edge_type") != "fallback" for edge in branches) else ControlKind.FALLBACK
        return ControlSpec(kind, branch_labels=tuple(_branch_label(edge) for edge in branches))
    parallel = tuple(edge for edge in controlled if edge.get("edge_type") == "parallel")
    if parallel:
        return ControlSpec(ControlKind.PARALLEL, branch_labels=tuple(_branch_label(edge) for edge in parallel))
    return None


def _normalize_graph(root: Mapping[str, Any]) -> MethodGraphVersion:
    adapter = _mapping(root["adapter"], "adapter")
    source = _mapping(root["method_graph"], "method_graph")
    source_edges = tuple(_mapping(item, "method_graph.edge") for item in source.get("edges", ()))
    modules: list[MethodModule] = []
    for raw in source.get("modules", ()):
        item = _mapping(raw, "method_graph.module")
        required_evidence = _evidence(item.get("required_evidence", ()))
        produced_evidence = _evidence(item.get("produced_evidence", ()))
        prerequisites = tuple(
            edge["source_module_id"]
            for edge in source_edges
            if edge.get("target_module_id") == item["module_id"] and edge.get("edge_type") == "depends_on"
        )
        modules.append(
            MethodModule(
                module_id=item["module_id"],
                version_id=item["version_id"],
                kind=ModuleKind(item["portable_kind"]),
                name=str(item["purpose"])[:160],
                description=item["purpose"],
                inputs=tuple(PortSpec(port["name"], port["type"]) for raw_port in item.get("inputs", ()) for port in (_mapping(raw_port, "module.input"),)),
                outputs=tuple(PortSpec(port["name"], port["type"]) for raw_port in item.get("outputs", ()) for port in (_mapping(raw_port, "module.output"),)),
                prerequisites=prerequisites,
                evidence_refs=tuple(sorted({entry.evidence_type for entry in (*required_evidence, *produced_evidence)})),
                side_effects=_side_effects(item),
                applicability_conditions=tuple(item.get("applicability_conditions", ())),
                preconditions=tuple(item.get("preconditions", ())),
                postconditions=tuple(item.get("postconditions", ())),
                required_evidence=required_evidence,
                produced_evidence=produced_evidence,
                side_effect_class=SideEffectClass(item.get("side_effect_class", "none")),
                permission_requirements=tuple(Permission(entry) for entry in item.get("permission_requirements", ())),
                failure_modes=tuple(item.get("failure_modes", ())),
                counterexamples=tuple(item.get("counterexamples", ())),
                fallback_refs=tuple(item.get("fallback_refs", ())),
                cost=CostLevel(item.get("cost", "low")),
                risk=RiskLevel(item.get("risk", "low")),
                reversibility=Reversibility(item.get("reversibility", "clean")),
                content_refs=tuple(item.get("content_refs", ())),
                rule_refs=tuple(item.get("rule_refs", ())),
                memory_refs=_memory_refs(item.get("memory_refs", ())),
                control=_control(item["module_id"], source_edges),
                contract_ref=item["version_id"],
            )
        )
    edges = tuple(
        MethodEdge(
            edge_id=item["edge_id"],
            kind=EdgeKind(item["edge_type"]),
            source_module=item["source_module_id"],
            target_module=item["target_module_id"],
            label=item["edge_type"],
            condition_ref=f"condition.{item['edge_id']}" if item.get("condition") is not None else None,
            branch_label=_branch_label(item),
            join_key=item.get("join_key"),
            max_iterations=item.get("max_iterations"),
        )
        for item in source_edges
    )
    lineage = _mapping(source.get("lineage", {}), "method_graph.lineage")
    if lineage.get("source_adapter_id") != adapter.get("adapter_id") or lineage.get("source_revision") != adapter.get("source_revision"):
        raise IRValidationError("adapter and graph lineage identity must match")
    return MethodGraphVersion(
        graph_id=source["graph_id"],
        version_id=source["version_id"],
        purpose=source["purpose"],
        modules=tuple(modules),
        edges=edges,
        entry_module_ids=tuple(source["entry_module_ids"]),
        exit_module_ids=tuple(source["exit_module_ids"]),
        lineage=GraphLineage(
            source_refs=tuple(adapter.get("source_refs", ())),
            source_adapter_id=lineage.get("source_adapter_id"),
            source_revision=lineage.get("source_revision"),
        ),
        applicability_conditions=tuple(source.get("applicability_conditions", ())),
    )


def _coverage(graph: MethodGraphVersion) -> set[str]:
    edge_kinds = {edge.kind for edge in graph.edges}
    coverage: set[str] = set()
    if graph.modules and graph.edges:
        coverage.add("topology")
    if any(module.inputs or module.outputs for module in graph.modules):
        coverage.add("typed_ports")
    if any(module.prerequisites for module in graph.modules):
        coverage.add("prerequisites")
    if edge_kinds & {EdgeKind.CONDITIONAL, EdgeKind.CONDITION_TRUE, EdgeKind.CONDITION_FALSE}:
        coverage.add("conditional")
    if EdgeKind.FALLBACK in edge_kinds:
        coverage.add("fallback")
    if EdgeKind.LOOP in edge_kinds:
        coverage.add("loop")
    if EdgeKind.PARALLEL in edge_kinds:
        coverage.add("parallel")
    if any(module.required_evidence or module.produced_evidence for module in graph.modules):
        coverage.add("evidence_binding")
    if all(module.permission_requirements or not module.side_effects for module in graph.modules):
        coverage.add("side_effect_permissions")
    if graph.lineage.source_adapter_id and graph.lineage.source_revision and graph.lineage.source_refs:
        coverage.add("lineage_identity")
    if any(Permission.AUTHORIZED_HUMAN_DECISION in module.permission_requirements for module in graph.modules):
        coverage.add("human_gate")
    if any(module.side_effect_class is SideEffectClass.CANDIDATE_MEMORY_WRITE for module in graph.modules):
        coverage.add("candidate_memory")
    return coverage


def normalize_adapter_fixture(value: Mapping[str, Any]) -> MethodGraphVersion:
    """Normalize one sanitized, source-pinned fixture into portable static IR."""
    root = _mapping(value, "adapter fixture")
    gaps = _preflight_gap_codes(root)
    if gaps:
        raise IRValidationError(f"adapter fixture is not normalization-ready: {', '.join(gaps)}")
    return _normalize_graph(root)


def assess_adapter_schema_freeze(values: Sequence[Mapping[str, Any]]) -> AdapterFreezeReport:
    """Derive the cross-adapter freeze decision from contracts and coverage."""
    adapter_ids: list[str] = []
    source_kinds: list[str] = []
    graph_hashes: list[str] = []
    coverage: set[str] = set()
    gaps: set[str] = set()
    for raw in values:
        root = _mapping(raw, "adapter fixture")
        gaps.update(_preflight_gap_codes(root))
        try:
            adapter = _mapping(root.get("adapter"), "adapter")
            adapter_ids.append(adapter["adapter_id"])
            source_kinds.append(adapter["source_kind"])
            graph = _normalize_graph(root)
        except (KeyError, TypeError, ValueError, IRValidationError):
            gaps.add("ADAPTER_NORMALIZATION_INVALID")
            continue
        graph_hashes.append(graph.content_hash)
        coverage.update(_coverage(graph))
    if len(values) < 2:
        gaps.add("ADAPTER_SET_TOO_SMALL")
    if len(set(adapter_ids)) != len(adapter_ids) or len(set(source_kinds)) < 2:
        gaps.add("ADAPTER_SET_NOT_UNLIKE")
    for missing in REQUIRED_ADAPTER_COVERAGE - coverage:
        gaps.add(f"ADAPTER_COVERAGE_MISSING_{missing.upper()}")
    return AdapterFreezeReport(
        adapter_ids=tuple(sorted(adapter_ids)),
        source_kinds=tuple(sorted(source_kinds)),
        graph_hashes=tuple(sorted(graph_hashes)),
        coverage=tuple(sorted(coverage)),
        blocking_gaps=tuple(sorted(gaps)),
    )
