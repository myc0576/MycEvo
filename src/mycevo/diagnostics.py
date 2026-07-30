"""Deterministic, read-only diagnostics for immutable method graphs."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any, Mapping

from .workflow_ir import (
    ControlKind,
    EdgeKind,
    MethodGraphVersion,
    MethodModule,
    ModuleKind,
    Permission,
    SideEffect,
    SideEffectClass,
    TaskContract,
    check_task_fitness,
)


# Only edges that can order module execution participate in flow analysis.
# Evidence, governance, and memory edges describe relationships; treating them
# as executable routes would make dead workflows appear reachable.
EXECUTION_EDGE_KINDS = frozenset(
    {
        EdgeKind.DATA,
        EdgeKind.SEQUENCE,
        EdgeKind.CONDITIONAL,
        EdgeKind.DEPENDS_ON,
        EdgeKind.WRITES_CANDIDATE_MEMORY,
        EdgeKind.CONDITION_TRUE,
        EdgeKind.CONDITION_FALSE,
        EdgeKind.PARALLEL,
        EdgeKind.LOOP,
        EdgeKind.FALLBACK,
    }
)
EVIDENCE_EDGE_KINDS = frozenset({EdgeKind.EVIDENCE_FOR})
RELATION_EDGE_KINDS = frozenset({EdgeKind.GOVERNED_BY, EdgeKind.READS_MEMORY})
# Evidence production is an ordering dependency even though it is not an
# executable control edge. Governance and memory references have no portable
# forward execution direction and therefore remain outside topology walks.
ORDERING_EDGE_KINDS = EXECUTION_EDGE_KINDS | EVIDENCE_EDGE_KINDS


@dataclass(frozen=True, slots=True)
class DiagnosticIssue:
    code: str
    severity: str
    message: str
    module_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()
    safe_alternative: str = "Review the static contract and create a new candidate graph version."

    def __post_init__(self) -> None:
        object.__setattr__(self, "module_ids", tuple(sorted(set(self.module_ids))))
        object.__setattr__(self, "edge_ids", tuple(sorted(set(self.edge_ids))))
        if self.severity not in {"error", "warning", "info"}:
            raise ValueError("diagnostic severity must be error, warning, or info")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "module_ids": list(self.module_ids),
            "edge_ids": list(self.edge_ids),
            "safe_alternative": self.safe_alternative,
        }


@dataclass(frozen=True, slots=True)
class GraphDiff:
    base_hash: str
    target_hash: str
    graph_contract_changed: bool = False
    added_module_ids: tuple[str, ...] = ()
    removed_module_ids: tuple[str, ...] = ()
    changed_module_ids: tuple[str, ...] = ()
    reordered_module_ids: tuple[str, ...] = ()
    added_edge_ids: tuple[str, ...] = ()
    removed_edge_ids: tuple[str, ...] = ()
    changed_edge_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_hash": self.base_hash,
            "target_hash": self.target_hash,
            "graph_contract_changed": self.graph_contract_changed,
            "added_module_ids": list(self.added_module_ids),
            "removed_module_ids": list(self.removed_module_ids),
            "changed_module_ids": list(self.changed_module_ids),
            "reordered_module_ids": list(self.reordered_module_ids),
            "added_edge_ids": list(self.added_edge_ids),
            "removed_edge_ids": list(self.removed_edge_ids),
            "changed_edge_ids": list(self.changed_edge_ids),
        }


@dataclass(frozen=True, slots=True)
class DiagnosticPolicy:
    require_evidence_gate: bool = False
    require_human_gate: bool = False
    require_loop_exit: bool = False
    require_validation_before_irreversible_write: bool = False
    require_known_refs: bool = False
    max_modules: int = 10_000
    max_edges: int = 50_000
    max_issues: int = 1_000
    max_analysis_steps: int = 5_000_000

    def __post_init__(self) -> None:
        if not all(
            type(value) is bool
            for value in (
                self.require_evidence_gate,
                self.require_human_gate,
                self.require_loop_exit,
                self.require_validation_before_irreversible_write,
                self.require_known_refs,
            )
        ):
            raise ValueError("diagnostic policy fields must be booleans")
        for name, maximum in (
            ("max_modules", 1_000_000),
            ("max_edges", 5_000_000),
            ("max_issues", 100_000),
            ("max_analysis_steps", 1_000_000_000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"diagnostic policy {name} must be an integer from 1 to {maximum}")


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "error",
    modules: tuple[str, ...] = (),
    edges: tuple[str, ...] = (),
    safe_alternative: str,
) -> DiagnosticIssue:
    return DiagnosticIssue(code, severity, message, modules, edges, safe_alternative)


def _issue_sort_key(issue: DiagnosticIssue) -> tuple[Any, ...]:
    return (
        issue.code,
        issue.edge_ids,
        issue.message,
        issue.module_ids,
        issue.safe_alternative,
        issue.severity,
    )


class _IssueCollector:
    """Keep only the deterministic lowest issues up to a fixed memory bound."""

    __slots__ = ("items", "limit", "overflowed")

    def __init__(self, limit: int) -> None:
        self.items: list[DiagnosticIssue] = []
        self.limit = limit
        self.overflowed = False

    def add(self, issue: DiagnosticIssue) -> None:
        self.items.append(issue)
        self.items.sort(key=_issue_sort_key)
        if len(self.items) > self.limit:
            self.items.pop()
            self.overflowed = True

    def finish(self) -> tuple[DiagnosticIssue, ...]:
        if not self.overflowed:
            return tuple(self.items)
        budget = _analysis_budget_issue(
            "Diagnostic issue output exceeded the configured max_issues limit; analysis is incomplete.",
        )
        kept = self.items[: max(0, self.limit - 1)]
        return tuple((*kept, budget))


def _analysis_budget_issue(message: str) -> DiagnosticIssue:
    return _issue(
        "GRAPH_ANALYSIS_BUDGET_EXCEEDED",
        message,
        safe_alternative="Increase the explicit diagnostic budget or reduce the candidate graph before analysis.",
    )


def _adjacency(
    graph: MethodGraphVersion,
    *,
    edge_kinds: frozenset[EdgeKind] = ORDERING_EDGE_KINDS,
    omitted_kinds: frozenset[EdgeKind] = frozenset(),
) -> dict[str, tuple[str, ...]]:
    values: dict[str, set[str]] = {module.module_id: set() for module in graph.modules}
    for edge in graph.edges:
        if edge.kind not in edge_kinds or edge.kind in omitted_kinds:
            continue
        values[edge.source_module].add(edge.target_module)
    return {module_id: tuple(sorted(targets)) for module_id, targets in values.items()}


def _reverse(adjacency: Mapping[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    values: dict[str, set[str]] = {module_id: set() for module_id in adjacency}
    for source, targets in adjacency.items():
        for target in targets:
            values[target].add(source)
    return {module_id: tuple(sorted(sources)) for module_id, sources in values.items()}


def _reachable(starts: tuple[str, ...], adjacency: Mapping[str, tuple[str, ...]]) -> set[str]:
    seen: set[str] = set()
    pending = list(sorted(starts, reverse=True))
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for target in reversed(adjacency.get(current, ())):
            if target not in seen:
                pending.append(target)
    return seen


def _has_path(source: str, target: str, adjacency: Mapping[str, tuple[str, ...]]) -> bool:
    return target in _reachable((source,), adjacency)


def _strong_components(adjacency: Mapping[str, tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    visited: set[str] = set()
    finish_order: list[str] = []
    for root in sorted(adjacency):
        if root in visited:
            continue
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                finish_order.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            for target in reversed(adjacency[node]):
                if target not in visited:
                    stack.append((target, False))

    reverse = _reverse(adjacency)
    assigned: set[str] = set()
    result: list[tuple[str, ...]] = []
    for root in reversed(finish_order):
        if root in assigned:
            continue
        component: list[str] = []
        pending = [root]
        assigned.add(root)
        while pending:
            node = pending.pop()
            component.append(node)
            for source in reversed(reverse[node]):
                if source not in assigned:
                    assigned.add(source)
                    pending.append(source)
        result.append(tuple(sorted(component)))
    return tuple(sorted(result))


def _safety_consistent(module: MethodModule) -> bool:
    permissions = set(module.permission_requirements)
    effects = set(module.side_effects)
    write_permissions = {
        Permission.CANDIDATE_WRITE,
        Permission.AUDIT_APPEND,
        Permission.CANDIDATE_MEMORY_WRITE,
    }
    if bool(permissions & write_permissions) != (SideEffect.WRITE_WORKSPACE in effects):
        return False
    pairs = (
        (Permission.WORKSPACE_READ, SideEffect.READ_WORKSPACE),
        (Permission.PROCESS_SPAWN, SideEffect.SPAWN_PROCESS),
        (Permission.NETWORK_ACCESS, SideEffect.NETWORK),
        (Permission.EXTERNAL_SERVICE_ACCESS, SideEffect.EXTERNAL_SERVICE),
    )
    if any((permission in permissions) != (effect in effects) for permission, effect in pairs):
        return False
    human_permission = bool(permissions & {Permission.HUMAN_INTERACTION, Permission.AUTHORIZED_HUMAN_DECISION})
    if human_permission != (SideEffect.HUMAN_INTERACTION in effects):
        return False
    required_by_class = {
        SideEffectClass.NONE: frozenset(),
        SideEffectClass.CANDIDATE_ARTIFACT_WRITE: frozenset({Permission.CANDIDATE_WRITE}),
        SideEffectClass.AUDIT_APPEND_ONLY: frozenset({Permission.AUDIT_APPEND}),
        SideEffectClass.CANDIDATE_MEMORY_WRITE: frozenset({Permission.CANDIDATE_MEMORY_WRITE}),
        SideEffectClass.DECISION_RECORD_APPEND_ONLY: frozenset({Permission.AUDIT_APPEND, Permission.AUTHORIZED_HUMAN_DECISION}),
    }
    required = required_by_class[module.side_effect_class]
    if not required <= permissions:
        return False
    return module.side_effect_class is not SideEffectClass.NONE or not permissions & write_permissions


def _evidence_requirements_satisfied(
    module: MethodModule,
    producers: tuple[MethodModule, ...],
) -> bool:
    if not module.required_evidence:
        return False
    for required in module.required_evidence:
        available = sum(
            produced.minimum_count
            for producer in producers
            for produced in producer.produced_evidence
            if produced.evidence_type == required.evidence_type
            and (required.role is None or produced.role == required.role)
        )
        if available < required.minimum_count:
            return False
    return True


def _topological_order(graph: MethodGraphVersion) -> tuple[str, ...]:
    adjacency: dict[str, set[str]] = {module.module_id: set() for module in graph.modules}
    indegree = {module.module_id: 0 for module in graph.modules}
    for edge in graph.edges:
        if (
            edge.kind not in ORDERING_EDGE_KINDS
            or edge.kind in {EdgeKind.LOOP, EdgeKind.FALLBACK}
            or edge.target_module in adjacency[edge.source_module]
        ):
            continue
        adjacency[edge.source_module].add(edge.target_module)
        indegree[edge.target_module] += 1
    ready = [module_id for module_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    result: list[str] = []
    while ready:
        current = heapq.heappop(ready)
        result.append(current)
        for target in sorted(adjacency[current]):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, target)
    result.extend(sorted(set(indegree) - set(result)))
    return tuple(result)


def _reordered_modules(base: MethodGraphVersion, target: MethodGraphVersion) -> tuple[str, ...]:
    common = set(module.module_id for module in base.modules) & set(module.module_id for module in target.modules)
    base_order = [module_id for module_id in _topological_order(base) if module_id in common]
    target_order = [module_id for module_id in _topological_order(target) if module_id in common]
    target_position = {module_id: index for index, module_id in enumerate(target_order)}
    positions = [target_position[module_id] for module_id in base_order]
    reordered: set[str] = set()
    prefix_max: list[int] = []
    current_max = -1
    for position in positions:
        prefix_max.append(current_max)
        current_max = max(current_max, position)
    suffix_min = len(positions)
    for index in range(len(positions) - 1, -1, -1):
        position = positions[index]
        if prefix_max[index] > position or suffix_min < position:
            reordered.add(base_order[index])
        suffix_min = min(suffix_min, position)
    return tuple(sorted(reordered))


def _analysis_step_estimate(
    graph: MethodGraphVersion,
    *,
    task: TaskContract | None,
    known_refs: frozenset[str] | None,
    policy: DiagnosticPolicy,
) -> int:
    """Return a conservative deterministic upper estimate before graph walks."""
    nested_items = (
        len(graph.entry_module_ids)
        + len(graph.exit_module_ids)
        + len(graph.applicability_conditions)
        + (graph.task_contract_ref is not None)
    )
    prerequisite_sources: set[str] = set()
    candidate_count = 0
    write_count = 0
    for module in graph.modules:
        nested_items += sum(
            len(values)
            for values in (
                module.inputs,
                module.outputs,
                module.prerequisites,
                module.evidence_refs,
                module.side_effects,
                module.applicability_conditions,
                module.preconditions,
                module.postconditions,
                module.required_evidence,
                module.produced_evidence,
                module.permission_requirements,
                module.failure_modes,
                module.counterexamples,
                module.fallback_refs,
                module.content_refs,
                module.rule_refs,
                module.memory_refs,
            )
        )
        if module.control is not None:
            nested_items += len(module.control.branch_labels) + 1
        nested_items += module.contract_ref is not None
        prerequisite_sources.update(module.prerequisites)
        candidate_count += module.side_effect_class is SideEffectClass.CANDIDATE_MEMORY_WRITE
        write_count += SideEffect.WRITE_WORKSPACE in module.side_effects

    ordering_edges = 0
    parallel_branches = 0
    fallback_branches = 0
    loop_continuations = 0
    for edge in graph.edges:
        if edge.kind in ORDERING_EDGE_KINDS:
            ordering_edges += 1
        parallel_branches += edge.kind is EdgeKind.PARALLEL
        fallback_branches += edge.kind is EdgeKind.FALLBACK
        loop_continuations += edge.kind in EXECUTION_EDGE_KINDS and edge.kind is not EdgeKind.LOOP

    if task is not None:
        nested_items += (
            len(task.required_inputs)
            + len(task.required_outputs)
            + len(task.allowed_side_effects)
            + len(task.constraints)
        )
    if known_refs is not None:
        nested_items += len(known_refs)

    walk_span = len(graph.modules) + ordering_edges
    repeated_walks = (
        6
        + len(prerequisite_sources)
        + parallel_branches
        + fallback_branches
        + 2 * candidate_count
        + loop_continuations
        + write_count
    )
    potential_retained_issues = min(
        policy.max_issues,
        4 * (len(graph.modules) + len(graph.edges) + nested_items),
    )
    collector_work = potential_retained_issues * potential_retained_issues
    return nested_items + len(graph.edges) + repeated_walks * walk_span + collector_work


def validate_graph(
    graph: MethodGraphVersion,
    *,
    task: TaskContract | None = None,
    known_refs: frozenset[str] | None = None,
    policy: DiagnosticPolicy | None = None,
) -> tuple[DiagnosticIssue, ...]:
    """Return deterministic static issues without mutating or executing the graph."""
    effective_policy = policy or DiagnosticPolicy()
    if len(graph.modules) > effective_policy.max_modules or len(graph.edges) > effective_policy.max_edges:
        return (_analysis_budget_issue(
            "Graph size exceeds the configured diagnostic module or edge budget; analysis is incomplete.",
        ),)
    estimated_steps = _analysis_step_estimate(
        graph,
        task=task,
        known_refs=known_refs,
        policy=effective_policy,
    )
    if estimated_steps > effective_policy.max_analysis_steps:
        return (_analysis_budget_issue(
            f"Static analysis estimate {estimated_steps} exceeds max_analysis_steps "
            f"{effective_policy.max_analysis_steps}; analysis is incomplete.",
        ),)

    issues = _IssueCollector(effective_policy.max_issues)
    module_by_id = {module.module_id: module for module in graph.modules}
    outgoing_by_module: dict[str, list[Any]] = {module_id: [] for module_id in module_by_id}
    incoming_data_by_target: dict[tuple[str, str | None], list[Any]] = {}
    referenced_module_ids: set[str] = set()
    for edge in graph.edges:
        outgoing_by_module[edge.source_module].append(edge)
        referenced_module_ids.update((edge.source_module, edge.target_module))
        if edge.kind is EdgeKind.DATA:
            incoming_data_by_target.setdefault((edge.target_module, edge.target_port), []).append(edge)
    known_ref_set = set(known_refs) if known_refs is not None else None
    adjacency = _adjacency(graph)
    reverse = _reverse(adjacency)
    evidence_adjacency = _adjacency(graph, edge_kinds=ORDERING_EDGE_KINDS)
    evidence_reverse = _reverse(evidence_adjacency)
    reachable = _reachable(graph.entry_module_ids, adjacency)
    can_exit = _reachable(graph.exit_module_ids, reverse)

    for module in sorted(graph.modules, key=lambda value: value.module_id):
        if module.module_id not in reachable:
            issues.add(_issue(
                "GRAPH_UNREACHABLE_MODULE",
                f"Module {module.module_id} is unreachable from every graph entry.",
                modules=(module.module_id,),
                safe_alternative="Connect the module through an explicit typed edge or remove it in a candidate version.",
            ))
        elif module.module_id not in can_exit:
            issues.add(_issue(
                "GRAPH_MODULE_CANNOT_REACH_EXIT",
                f"Module {module.module_id} cannot reach a declared graph exit.",
                modules=(module.module_id,),
                safe_alternative="Add an explicit fail-closed route to an exit or remove the dead branch.",
            ))

        for port in module.inputs:
            bindings = tuple(incoming_data_by_target.get((module.module_id, port.name), ()))
            if port.required and module.module_id not in graph.entry_module_ids and not bindings:
                issues.add(_issue(
                    "GRAPH_REQUIRED_INPUT_UNBOUND",
                    f"Required input {module.module_id}.{port.name} has no DATA binding.",
                    modules=(module.module_id,),
                    safe_alternative="Bind the input with a compatible DATA edge or make it an explicit entry contract.",
                ))
            if not port.multiple and len(bindings) > 1:
                issues.add(_issue(
                    "GRAPH_INPUT_MULTIPLE_BINDINGS",
                    f"Single input {module.module_id}.{port.name} has multiple DATA bindings.",
                    modules=(module.module_id,),
                    edges=tuple(edge.edge_id for edge in bindings),
                    safe_alternative="Select one producer or mark the input contract as multiple in a new module version.",
                ))

        if not _safety_consistent(module):
            issues.add(_issue(
                "GRAPH_PERMISSION_SIDE_EFFECT_MISMATCH",
                f"Module {module.module_id} has contradictory permissions, effects, or side-effect class.",
                modules=(module.module_id,),
                safe_alternative="Create a corrected module contract whose permissions and declared effects agree.",
            ))
        if module.deprecated and (module.module_id in reachable or module.module_id in referenced_module_ids):
            issues.add(_issue(
                "GRAPH_DEPRECATED_MODULE_REFERENCED",
                f"Deprecated module {module.module_id} remains active or referenced.",
                modules=(module.module_id,),
                safe_alternative="Replace the deprecated module through a reviewed candidate graph version.",
            ))
        for reference in module.fallback_refs:
            if reference not in module_by_id:
                issues.add(_issue(
                    "GRAPH_STALE_REFERENCE",
                    f"Module {module.module_id} references missing fallback module {reference}.",
                    modules=(module.module_id,),
                    safe_alternative="Point the fallback to an existing module version or remove the stale reference.",
                ))
        if known_ref_set is not None:
            contract_refs = (
                (module.contract_ref,)
                if effective_policy.require_known_refs and module.contract_ref is not None
                else ()
            )
            external_refs = (
                *module.evidence_refs,
                *module.content_refs,
                *module.rule_refs,
                *module.memory_refs,
                *contract_refs,
            )
            for reference in sorted(set(external_refs) - known_ref_set):
                issues.add(_issue(
                    "GRAPH_STALE_REFERENCE",
                    f"Module {module.module_id} references unknown external contract {reference}.",
                    modules=(module.module_id,),
                    safe_alternative="Resolve the reference against an immutable supplied catalog before evaluation.",
                ))

    if (
        effective_policy.require_known_refs
        and known_ref_set is not None
        and graph.task_contract_ref is not None
        and graph.task_contract_ref not in known_ref_set
    ):
        issues.add(_issue(
            "GRAPH_STALE_REFERENCE",
            f"Graph {graph.graph_id} references unknown task contract {graph.task_contract_ref}.",
            safe_alternative="Resolve the task contract against the immutable supplied catalog before evaluation.",
        ))

    for exit_id in graph.exit_module_ids:
        if exit_id not in reachable:
            issues.add(_issue(
                "GRAPH_EXIT_UNREACHABLE",
                f"Exit module {exit_id} is unreachable from every graph entry.",
                modules=(exit_id,),
                safe_alternative="Connect the exit through an explicit route or declare the actual reachable exit.",
            ))

    without_loops = _adjacency(graph, omitted_kinds=frozenset({EdgeKind.LOOP}))
    components = _strong_components(without_loops)
    component_index = {
        module_id: index
        for index, component in enumerate(components)
        for module_id in component
    }
    causal_edges_by_component: dict[int, list[str]] = {}
    for edge in graph.edges:
        source_component = component_index[edge.source_module]
        if (
            edge.kind in ORDERING_EDGE_KINDS
            and edge.kind is not EdgeKind.LOOP
            and source_component == component_index[edge.target_module]
        ):
            causal_edges_by_component.setdefault(source_component, []).append(edge.edge_id)
    for index, component in enumerate(components):
        has_self_edge = len(component) == 1 and component[0] in without_loops[component[0]]
        if len(component) > 1 or has_self_edge:
            cycle_edges = tuple(sorted(causal_edges_by_component.get(index, ())))
            issues.add(_issue(
                "GRAPH_ILLEGAL_CYCLE",
                f"Modules {', '.join(component)} form a cycle without an explicit bounded LOOP edge.",
                modules=component,
                edges=cycle_edges,
                safe_alternative="Replace the back edge with an explicit bounded loop contract or break the cycle.",
            ))

    prerequisite_adjacency = {module.module_id: tuple(sorted(module.prerequisites)) for module in graph.modules}
    prerequisite_reverse = _reverse(prerequisite_adjacency)
    for component in _strong_components(prerequisite_reverse):
        has_self_prerequisite = len(component) == 1 and component[0] in prerequisite_adjacency[component[0]]
        if len(component) > 1 or has_self_prerequisite:
            issues.add(_issue(
                "GRAPH_PREREQUISITE_CYCLE",
                f"Modules {', '.join(component)} contain a prerequisite cycle.",
                modules=component,
                safe_alternative="Break the prerequisite cycle in a new candidate graph.",
            ))
    reachability_by_source: dict[str, set[str]] = {}

    def has_path(source: str, target: str) -> bool:
        if source not in reachability_by_source:
            reachability_by_source[source] = _reachable((source,), adjacency)
        return target in reachability_by_source[source]

    for module in sorted(graph.modules, key=lambda value: value.module_id):
        for prerequisite in module.prerequisites:
            if not has_path(prerequisite, module.module_id):
                issues.add(_issue(
                    "GRAPH_PREREQUISITE_UNSATISFIED",
                    f"Prerequisite {prerequisite} is not upstream of {module.module_id}.",
                    modules=(prerequisite, module.module_id),
                    safe_alternative="Add an explicit prerequisite-to-dependent route or remove the declaration.",
                ))
            if prerequisite != module.module_id and has_path(module.module_id, prerequisite):
                issues.add(_issue(
                    "GRAPH_ORDER_CONTRADICTION",
                    f"Module {module.module_id} can run upstream of its prerequisite {prerequisite}.",
                    modules=(prerequisite, module.module_id),
                    safe_alternative="Reorder the graph so every prerequisite is strictly upstream.",
                ))

    for module in sorted(graph.modules, key=lambda value: value.module_id):
        outgoing = tuple(outgoing_by_module[module.module_id])
        if module.control is not None and module.control.kind is ControlKind.PARALLEL:
            parallel = tuple(edge for edge in outgoing if edge.kind is EdgeKind.PARALLEL)
            join_adjacency = _adjacency(
                graph,
                edge_kinds=EXECUTION_EDGE_KINDS,
                omitted_kinds=frozenset({EdgeKind.LOOP, EdgeKind.FALLBACK}),
            )
            branch_descendants = tuple(
                _reachable((edge.target_module,), join_adjacency)
                for edge in sorted(parallel, key=lambda value: value.edge_id)
            )
            branch_roots = {edge.target_module for edge in parallel}
            common_join = set.intersection(*branch_descendants) if branch_descendants else set()
            common_join -= branch_roots
            branch_root_overlap = any(
                other_root in descendants
                for edge, descendants in zip(sorted(parallel, key=lambda value: value.edge_id), branch_descendants)
                for other_root in branch_roots - {edge.target_module}
            )
            if branch_root_overlap or not common_join:
                issues.add(_issue(
                    "GRAPH_PARALLEL_JOIN_UNRESOLVED",
                    f"Parallel branches from {module.module_id} have no downstream common join.",
                    modules=(module.module_id, *(edge.target_module for edge in parallel)),
                    edges=tuple(edge.edge_id for edge in parallel),
                    safe_alternative="Route all branches to one explicit join module with the same join key.",
                ))
        if module.control is not None and module.control.kind is ControlKind.CONDITION:
            branches = tuple(edge for edge in outgoing if edge.kind in {EdgeKind.CONDITIONAL, EdgeKind.CONDITION_TRUE, EdgeKind.CONDITION_FALSE, EdgeKind.FALLBACK})
            has_positive = any(edge.kind in {EdgeKind.CONDITIONAL, EdgeKind.CONDITION_TRUE} for edge in branches)
            if not has_positive:
                issues.add(_issue(
                    "GRAPH_CONDITION_INCOMPLETE",
                    f"Conditional module {module.module_id} has no explicit positive outcome.",
                    modules=(module.module_id,),
                    edges=tuple(edge.edge_id for edge in branches),
                    safe_alternative="Add explicit success and fail-closed branches.",
                ))
            has_exhaustion = any(edge.kind in {EdgeKind.FALLBACK, EdgeKind.CONDITION_FALSE} for edge in branches)
            if not has_exhaustion:
                issues.add(_issue(
                    "GRAPH_CONDITION_EXHAUSTION_MISSING",
                    f"Conditional module {module.module_id} has no explicit false or fallback exhaustion branch.",
                    modules=(module.module_id,),
                    edges=tuple(edge.edge_id for edge in branches),
                    safe_alternative="Add an explicit false or fail-closed fallback branch.",
                ))
        fallback_edges = tuple(edge for edge in outgoing if edge.kind is EdgeKind.FALLBACK)
        if len(fallback_edges) > 1:
            issues.add(_issue(
                "GRAPH_FALLBACK_AMBIGUOUS",
                f"Module {module.module_id} declares multiple unordered fallback branches.",
                modules=(module.module_id, *(edge.target_module for edge in fallback_edges)),
                edges=tuple(edge.edge_id for edge in fallback_edges),
                safe_alternative="Replace unordered fallbacks with one explicit fail-closed fallback or a typed condition contract.",
            ))
        if fallback_edges:
            fallback_flow = _adjacency(
                graph,
                edge_kinds=EXECUTION_EDGE_KINDS,
                omitted_kinds=frozenset({EdgeKind.FALLBACK, EdgeKind.LOOP}),
            )
            fallback_reaches_exit = any(
                _reachable((edge.target_module,), fallback_flow) & set(graph.exit_module_ids)
                for edge in fallback_edges
            )
            if not fallback_reaches_exit:
                issues.add(_issue(
                    "GRAPH_FALLBACK_EXHAUSTION_MISSING",
                    f"Fallback path from {module.module_id} has no non-fallback route to an exit.",
                    modules=(module.module_id, *(edge.target_module for edge in fallback_edges)),
                    edges=tuple(edge.edge_id for edge in fallback_edges),
                    safe_alternative="Add a bounded fail-closed continuation from the fallback target to a declared exit.",
                ))

    candidate_modules = tuple(module for module in graph.modules if module.side_effect_class is SideEffectClass.CANDIDATE_MEMORY_WRITE)
    validation_ids = {module.module_id for module in graph.modules if module.kind is ModuleKind.VALIDATION}
    human_ids = {
        module.module_id
        for module in graph.modules
        if module.kind is ModuleKind.DECISION
        and module.side_effect_class is SideEffectClass.DECISION_RECORD_APPEND_ONLY
        and Permission.AUTHORIZED_HUMAN_DECISION in module.permission_requirements
    }
    for module in sorted(candidate_modules, key=lambda value: value.module_id):
        ancestors = _reachable((module.module_id,), evidence_reverse) - {module.module_id}
        producers = tuple(
            module_by_id[module_id]
            for module_id in sorted(ancestors)
            if module_by_id[module_id].kind is ModuleKind.VALIDATION
        )
        if not ancestors & validation_ids or not _evidence_requirements_satisfied(module, producers):
            issues.add(_issue(
                "GRAPH_EVIDENCE_GATE_MISSING",
                f"Candidate-memory module {module.module_id} lacks an upstream validation with bound required evidence.",
                modules=(module.module_id,),
                safe_alternative="Bind admissible evidence through an explicit validation module before candidate writeback.",
            ))
        without_human_gates = {
            source: tuple(target for target in targets if target not in human_ids)
            if source not in human_ids
            else ()
            for source, targets in adjacency.items()
        }
        ungoverned_exits = _reachable((module.module_id,), without_human_gates) & set(graph.exit_module_ids)
        if ungoverned_exits:
            issues.add(_issue(
                "GRAPH_HUMAN_GATE_MISSING",
                f"Candidate-memory module {module.module_id} has a path to exit that bypasses every authorized human decision gate.",
                modules=(module.module_id,),
                safe_alternative="Add an explicit authorized human accept, reject, or defer decision after the candidate.",
            ))

    if effective_policy.require_known_refs and known_refs is None:
        issues.add(_issue(
            "GRAPH_REFERENCE_CATALOG_MISSING",
            "Strict reference validation requires an explicit known_refs catalog.",
            safe_alternative="Supply an immutable reference catalog snapshot before treating diagnosis as complete.",
        ))

    if policy is not None:
        active_ids = reachable & can_exit
        evidence_gate_ids = {
            module.module_id
            for module in graph.modules
            if module.module_id in active_ids
            and module.kind is ModuleKind.VALIDATION
            and (module.required_evidence or module.produced_evidence)
        }
        active_human_ids = human_ids & active_ids
        if effective_policy.require_evidence_gate and not evidence_gate_ids:
            issues.add(_issue(
                "GRAPH_EVIDENCE_GATE_MISSING",
                "The diagnostic policy requires an explicit evidence-binding validation gate.",
                safe_alternative="Insert a validation module with explicit required or produced evidence contracts.",
            ))
        if effective_policy.require_human_gate and not active_human_ids:
            issues.add(_issue(
                "GRAPH_HUMAN_GATE_MISSING",
                "The diagnostic policy requires an authorized human decision gate.",
                safe_alternative="Insert an append-only authorized human accept, reject, or defer decision module.",
            ))
        if effective_policy.require_loop_exit:
            non_loop_adjacency = _adjacency(
                graph,
                edge_kinds=EXECUTION_EDGE_KINDS,
                omitted_kinds=frozenset({EdgeKind.LOOP}),
            )
            for module in sorted(graph.modules, key=lambda value: value.module_id):
                if module.control is None or module.control.kind is not ControlKind.LOOP:
                    continue
                continuations = tuple(
                    edge
                    for edge in graph.edges
                    if edge.source_module == module.module_id
                    and edge.kind in EXECUTION_EDGE_KINDS
                    and edge.kind is not EdgeKind.LOOP
                )
                has_exit_route = any(
                    _reachable((edge.target_module,), non_loop_adjacency) & set(graph.exit_module_ids)
                    for edge in continuations
                )
                if not has_exit_route:
                    loop_edges = tuple(
                        edge.edge_id
                        for edge in graph.edges
                        if edge.source_module == module.module_id and edge.kind is EdgeKind.LOOP
                    )
                    issues.add(_issue(
                        "GRAPH_CONTROL_EXIT_MISSING",
                        f"Bounded loop module {module.module_id} has no explicit non-loop route to an exit.",
                        modules=(module.module_id,),
                        edges=loop_edges,
                        safe_alternative="Add an explicit exhaustion or success route from the loop to a reachable exit.",
                    ))
        if effective_policy.require_validation_before_irreversible_write:
            for module in sorted(graph.modules, key=lambda value: value.module_id):
                if SideEffect.WRITE_WORKSPACE not in module.side_effects:
                    continue
                ancestors = _reachable((module.module_id,), reverse) - {module.module_id}
                if not ancestors & validation_ids:
                    issues.add(_issue(
                        "GRAPH_SIDE_EFFECT_ORDER_UNSAFE",
                        f"Write-capable module {module.module_id} has no upstream validation boundary.",
                        modules=(module.module_id,),
                        safe_alternative="Move the write after an explicit validation gate in a new candidate graph.",
                    ))

    if task is not None:
        for gap in check_task_fitness(graph, task):
            code = (
                "GRAPH_SIDE_EFFECT_UNAUTHORIZED"
                if gap == "ADAPTER_SIDE_EFFECT_UNAUTHORIZED"
                else f"GRAPH_TASK_{gap.removeprefix('ADAPTER_')}"
            )
            issues.add(_issue(
                code,
                f"Task contract rejected the graph with {gap}.",
                safe_alternative="Use a graph whose budgets, ports, and side effects satisfy the supplied task contract.",
            ))

    return issues.finish()


def diagnose_graph(
    graph: MethodGraphVersion,
    *,
    task: TaskContract | None = None,
    known_refs: frozenset[str] | None = None,
    policy: DiagnosticPolicy | None = None,
) -> tuple[DiagnosticIssue, ...]:
    """Return the deterministic validation/diagnosis issue set."""
    return validate_graph(graph, task=task, known_refs=known_refs, policy=policy)


def diff_graphs(base: MethodGraphVersion, target: MethodGraphVersion) -> GraphDiff:
    """Return an exact, hash-bound structural diff without proposing changes."""
    base_modules = {module.module_id: module for module in base.modules}
    target_modules = {module.module_id: module for module in target.modules}
    base_edges = {edge.edge_id: edge for edge in base.edges}
    target_edges = {edge.edge_id: edge for edge in target.edges}
    common_modules = set(base_modules) & set(target_modules)
    common_edges = set(base_edges) & set(target_edges)
    changed_modules = {
        module_id
        for module_id in common_modules
        if (
            base_modules[module_id].to_dict() != target_modules[module_id].to_dict()
            or (module_id in base.entry_module_ids) != (module_id in target.entry_module_ids)
            or (module_id in base.exit_module_ids) != (module_id in target.exit_module_ids)
        )
    }
    base_graph_contract = base.to_dict()
    target_graph_contract = target.to_dict()
    for contract in (base_graph_contract, target_graph_contract):
        contract.pop("modules")
        contract.pop("edges")
    return GraphDiff(
        base_hash=base.content_hash,
        target_hash=target.content_hash,
        graph_contract_changed=base_graph_contract != target_graph_contract,
        added_module_ids=tuple(sorted(set(target_modules) - set(base_modules))),
        removed_module_ids=tuple(sorted(set(base_modules) - set(target_modules))),
        changed_module_ids=tuple(sorted(changed_modules)),
        reordered_module_ids=_reordered_modules(base, target),
        added_edge_ids=tuple(sorted(set(target_edges) - set(base_edges))),
        removed_edge_ids=tuple(sorted(set(base_edges) - set(target_edges))),
        changed_edge_ids=tuple(sorted(
            edge_id
            for edge_id in common_edges
            if base_edges[edge_id].to_dict() != target_edges[edge_id].to_dict()
        )),
    )
