from __future__ import annotations

import ast
import builtins
import copy
import dataclasses
import importlib
import json
import socket
import subprocess
from pathlib import Path

import pytest

import mycevo.diagnostics as diagnostics
from mycevo.diagnostics import (
    DiagnosticPolicy,
    DiagnosticIssue,
    GraphDiff,
    diagnose_graph,
    diff_graphs,
    validate_graph,
)
from mycevo.workflow_ir import (
    ControlKind,
    ControlSpec,
    EdgeKind,
    EvidenceSpec,
    GraphLineage,
    MethodEdge,
    MethodGraphVersion,
    MethodModule,
    ModuleKind,
    Permission,
    PortSpec,
    SideEffect,
    SideEffectClass,
    TaskContract,
    graph_from_json_bytes,
)


ISSUE_KEYS = {
    "code",
    "severity",
    "message",
    "module_ids",
    "edge_ids",
    "safe_alternative",
}
DIFF_KEYS = {
    "base_hash",
    "target_hash",
    "added_module_ids",
    "removed_module_ids",
    "changed_module_ids",
    "reordered_module_ids",
    "added_edge_ids",
    "removed_edge_ids",
    "changed_edge_ids",
    "graph_contract_changed",
}
DIAGNOSTIC_FIXTURES = Path(__file__).parent / "fixtures" / "diagnostics"


def _module(
    module_id: str,
    *,
    kind: ModuleKind = ModuleKind.TRANSFORM,
    inputs: tuple[PortSpec, ...] = (),
    outputs: tuple[PortSpec, ...] = (),
    prerequisites: tuple[str, ...] = (),
    fallback_refs: tuple[str, ...] = (),
    memory_refs: tuple[str, ...] = (),
    required_evidence: tuple[EvidenceSpec, ...] = (),
    produced_evidence: tuple[EvidenceSpec, ...] = (),
    side_effect_class: SideEffectClass = SideEffectClass.NONE,
    permissions: tuple[Permission, ...] = (),
    side_effects: tuple[SideEffect, ...] = (),
    control: ControlSpec | None = None,
    deprecated: bool = False,
    description: str | None = None,
) -> MethodModule:
    return MethodModule(
        module_id=module_id,
        version_id=f"{module_id}.v1",
        kind=kind,
        name=module_id.replace("_", " ").title(),
        description=description or f"Static diagnostic contract for {module_id}.",
        inputs=inputs,
        outputs=outputs,
        prerequisites=prerequisites,
        fallback_refs=fallback_refs,
        memory_refs=memory_refs,
        required_evidence=required_evidence,
        produced_evidence=produced_evidence,
        side_effect_class=side_effect_class,
        permission_requirements=permissions,
        side_effects=side_effects,
        control=control,
        deprecated=deprecated,
    )


def _graph(
    modules: tuple[MethodModule, ...],
    edges: tuple[MethodEdge, ...],
    *,
    entries: tuple[str, ...],
    exits: tuple[str, ...],
    version_id: str = "graph.diagnostic.v1",
) -> MethodGraphVersion:
    return MethodGraphVersion(
        graph_id="graph.diagnostic",
        version_id=version_id,
        purpose="Exercise deterministic read-only graph diagnostics.",
        modules=modules,
        edges=edges,
        entry_module_ids=entries,
        exit_module_ids=exits,
        lineage=GraphLineage(source_refs=("fixture:diagnostics",)),
    )


def _clean_graph() -> MethodGraphVersion:
    source = _module(
        "source",
        kind=ModuleKind.INPUT,
        outputs=(PortSpec("artifact", "artifact_ref"),),
    )
    sink = _module(
        "sink",
        kind=ModuleKind.OUTPUT,
        inputs=(PortSpec("artifact", "artifact_ref"),),
    )
    edge = MethodEdge(
        "source_to_sink",
        EdgeKind.DATA,
        "source",
        "sink",
        source_port="artifact",
        target_port="artifact",
    )
    return _graph((source, sink), (edge,), entries=("source",), exits=("sink",))


def _issue_codes(issues: tuple[DiagnosticIssue, ...]) -> tuple[str, ...]:
    return tuple(issue.code for issue in issues)


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _candidate_graph(*, gated: bool) -> MethodGraphVersion:
    source = _module("source", kind=ModuleKind.INPUT)
    memory = _module(
        "candidate_memory",
        kind=ModuleKind.MEMORY,
        required_evidence=(EvidenceSpec("evaluation_result_ref"),) if gated else (),
        side_effect_class=SideEffectClass.CANDIDATE_MEMORY_WRITE,
        permissions=(Permission.CANDIDATE_WRITE, Permission.CANDIDATE_MEMORY_WRITE),
        side_effects=(SideEffect.WRITE_WORKSPACE,),
    )
    human_gate = _module(
        "human_gate",
        kind=ModuleKind.DECISION,
        side_effect_class=SideEffectClass.DECISION_RECORD_APPEND_ONLY,
        permissions=(Permission.AUDIT_APPEND, Permission.AUTHORIZED_HUMAN_DECISION),
        side_effects=(SideEffect.WRITE_WORKSPACE, SideEffect.HUMAN_INTERACTION),
    )
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    if not gated:
        return _graph(
            (source, memory, sink),
            (
                MethodEdge("to_memory", EdgeKind.SEQUENCE, "source", "candidate_memory"),
                MethodEdge(
                    "write_candidate",
                    EdgeKind.WRITES_CANDIDATE_MEMORY,
                    "candidate_memory",
                    "sink",
                ),
            ),
            entries=("source",),
            exits=("sink",),
        )

    validation = _module(
        "evidence_validation",
        kind=ModuleKind.VALIDATION,
        produced_evidence=(EvidenceSpec("evaluation_result_ref", role="gate"),),
    )
    return _graph(
        (source, validation, memory, human_gate, sink),
        (
            MethodEdge("to_validation", EdgeKind.SEQUENCE, "source", "evidence_validation"),
            MethodEdge(
                "evidence_to_memory",
                EdgeKind.EVIDENCE_FOR,
                "evidence_validation",
                "candidate_memory",
            ),
            MethodEdge(
                "write_candidate",
                EdgeKind.WRITES_CANDIDATE_MEMORY,
                "candidate_memory",
                "human_gate",
            ),
            MethodEdge("human_to_sink", EdgeKind.GOVERNED_BY, "human_gate", "sink"),
        ),
        entries=("source",),
        exits=("sink",),
    )


def _linear_graph(
    order: tuple[str, ...],
    *,
    changed_module: str | None = None,
    version_id: str = "graph.diagnostic.v1",
) -> MethodGraphVersion:
    modules = tuple(
        _module(
            module_id,
            kind=(
                ModuleKind.INPUT
                if module_id == order[0]
                else ModuleKind.OUTPUT
                if module_id == order[-1]
                else ModuleKind.TRANSFORM
            ),
            description=(
                f"Changed portable contract for {module_id}."
                if module_id == changed_module
                else None
            ),
        )
        for module_id in sorted(order)
    )
    edges = tuple(
        MethodEdge(f"edge_{index}", EdgeKind.SEQUENCE, source, target)
        for index, (source, target) in enumerate(zip(order, order[1:]), start=1)
    )
    return _graph(
        modules,
        edges,
        entries=(order[0],),
        exits=(order[-1],),
        version_id=version_id,
    )


def test_clean_graph_has_no_diagnostics() -> None:
    graph = _clean_graph()

    assert validate_graph(graph) == ()
    assert diagnose_graph(graph) == ()


def test_production_diagnostic_goldens_match_exact_issues_without_mutation() -> None:
    contexts = _load_json(DIAGNOSTIC_FIXTURES / "contexts.json")
    expectations = _load_json(DIAGNOSTIC_FIXTURES / "expected_issues.json")
    assert contexts["schema"] == "mycevo.diagnostic_contexts.v1"
    assert expectations["schema"] == "mycevo.diagnostic_expectations.v1"
    expected_by_id = {
        case["case_id"]: case["issues"]
        for case in expectations["cases"]
    }

    for context in contexts["cases"]:
        graph_path = DIAGNOSTIC_FIXTURES / context["graph_file"]
        raw_graph = graph_path.read_bytes()
        graph = graph_from_json_bytes(raw_graph)
        task = TaskContract.from_dict(context["task_contract"])
        policy = DiagnosticPolicy(**context["policy"])
        before = {
            "bytes": graph.canonical_bytes,
            "hash": graph.content_hash,
            "dict": copy.deepcopy(graph.to_dict()),
        }

        issues = diagnose_graph(
            graph,
            task=task,
            known_refs=frozenset(context["external_refs"]),
            policy=policy,
        )

        assert graph_path.read_bytes() == raw_graph
        assert [issue.to_dict() for issue in issues] == expected_by_id[context["case_id"]]
        assert graph.canonical_bytes == before["bytes"]
        assert graph.content_hash == before["hash"]
        assert graph.to_dict() == before["dict"]


def test_validate_graph_detects_unreachable_module() -> None:
    graph = _clean_graph()
    orphan = _module("orphan")
    graph = dataclasses.replace(graph, modules=(*graph.modules, orphan))

    assert "GRAPH_UNREACHABLE_MODULE" in _issue_codes(validate_graph(graph))


def test_validate_graph_detects_unbound_required_typed_input() -> None:
    source = _module(
        "source",
        kind=ModuleKind.INPUT,
        outputs=(PortSpec("artifact", "artifact_ref"),),
    )
    sink = _module(
        "sink",
        kind=ModuleKind.OUTPUT,
        inputs=(PortSpec("artifact", "artifact_ref"),),
    )
    graph = _graph(
        (source, sink),
        (MethodEdge("sequence_only", EdgeKind.SEQUENCE, "source", "sink"),),
        entries=("source",),
        exits=("sink",),
    )

    assert "GRAPH_REQUIRED_INPUT_UNBOUND" in _issue_codes(validate_graph(graph))


def test_validate_graph_detects_unsatisfied_prerequisite() -> None:
    prerequisite = _module("prerequisite", kind=ModuleKind.INPUT)
    other_entry = _module("other_entry", kind=ModuleKind.INPUT)
    step = _module("step", kind=ModuleKind.OUTPUT, prerequisites=("prerequisite",))
    graph = _graph(
        (prerequisite, other_entry, step),
        (MethodEdge("other_to_step", EdgeKind.SEQUENCE, "other_entry", "step"),),
        entries=("prerequisite", "other_entry"),
        exits=("step",),
    )

    assert "GRAPH_PREREQUISITE_UNSATISFIED" in _issue_codes(validate_graph(graph))


def test_validate_graph_detects_illegal_cycle_and_order_contradiction() -> None:
    first = _module("first", kind=ModuleKind.INPUT, prerequisites=("second",))
    second = _module("second", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (first, second),
        (
            MethodEdge("first_to_second", EdgeKind.SEQUENCE, "first", "second"),
            MethodEdge("second_to_first", EdgeKind.SEQUENCE, "second", "first"),
        ),
        entries=("first",),
        exits=("second",),
    )

    codes = _issue_codes(validate_graph(graph))
    assert "GRAPH_ILLEGAL_CYCLE" in codes
    assert "GRAPH_ORDER_CONTRADICTION" in codes


def test_validate_graph_detects_unresolved_parallel_join() -> None:
    router = _module(
        "router",
        kind=ModuleKind.INPUT,
        control=ControlSpec(ControlKind.PARALLEL, branch_labels=("left", "right")),
    )
    left = _module("left", kind=ModuleKind.OUTPUT)
    right = _module("right", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (router, left, right),
        (
            MethodEdge(
                "parallel_left",
                EdgeKind.PARALLEL,
                "router",
                "left",
                branch_label="left",
                join_key="joined",
            ),
            MethodEdge(
                "parallel_right",
                EdgeKind.PARALLEL,
                "router",
                "right",
                branch_label="right",
                join_key="joined",
            ),
        ),
        entries=("router",),
        exits=("left", "right"),
    )

    assert "GRAPH_PARALLEL_JOIN_UNRESOLVED" in _issue_codes(validate_graph(graph))


def test_validate_graph_rechecks_permission_side_effect_contract() -> None:
    unsafe = _module(
        "unsafe",
        kind=ModuleKind.INPUT,
        side_effect_class=SideEffectClass.CANDIDATE_ARTIFACT_WRITE,
        permissions=(Permission.CANDIDATE_WRITE,),
        side_effects=(SideEffect.WRITE_WORKSPACE,),
    )
    object.__setattr__(unsafe, "permission_requirements", ())
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (unsafe, sink),
        (MethodEdge("unsafe_to_sink", EdgeKind.SEQUENCE, "unsafe", "sink"),),
        entries=("unsafe",),
        exits=("sink",),
    )

    assert "GRAPH_PERMISSION_SIDE_EFFECT_MISMATCH" in _issue_codes(validate_graph(graph))


def test_diagnose_graph_detects_deprecated_and_stale_references() -> None:
    source = _module("source", kind=ModuleKind.INPUT, fallback_refs=("missing_module",))
    deprecated = _module("deprecated", kind=ModuleKind.OUTPUT, deprecated=True)
    graph = _graph(
        (source, deprecated),
        (MethodEdge("to_deprecated", EdgeKind.SEQUENCE, "source", "deprecated"),),
        entries=("source",),
        exits=("deprecated",),
    )

    codes = _issue_codes(diagnose_graph(graph))
    assert "GRAPH_DEPRECATED_MODULE_REFERENCED" in codes
    assert "GRAPH_STALE_REFERENCE" in codes


def test_diagnose_graph_requires_evidence_and_human_gates_for_candidate_memory() -> None:
    ungated_codes = _issue_codes(diagnose_graph(_candidate_graph(gated=False)))
    assert "GRAPH_EVIDENCE_GATE_MISSING" in ungated_codes
    assert "GRAPH_HUMAN_GATE_MISSING" in ungated_codes

    gated_codes = _issue_codes(diagnose_graph(_candidate_graph(gated=True)))
    assert "GRAPH_EVIDENCE_GATE_MISSING" not in gated_codes
    assert "GRAPH_HUMAN_GATE_MISSING" not in gated_codes


def test_diagnostic_policy_requires_explicit_evidence_and_human_gates() -> None:
    policy = DiagnosticPolicy(require_evidence_gate=True, require_human_gate=True)

    codes = _issue_codes(diagnose_graph(_clean_graph(), policy=policy))

    assert "GRAPH_EVIDENCE_GATE_MISSING" in codes
    assert "GRAPH_HUMAN_GATE_MISSING" in codes


def test_diagnostic_policy_requires_explicit_loop_exit() -> None:
    loop = _module(
        "loop",
        kind=ModuleKind.INPUT,
        control=ControlSpec(ControlKind.LOOP, max_iterations=2),
    )
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (loop, sink),
        (MethodEdge("repeat", EdgeKind.LOOP, "loop", "loop", max_iterations=2),),
        entries=("loop",),
        exits=("sink",),
    )

    codes = _issue_codes(
        diagnose_graph(graph, policy=DiagnosticPolicy(require_loop_exit=True))
    )

    assert "GRAPH_CONTROL_EXIT_MISSING" in codes


def _write_before_validation_graph() -> MethodGraphVersion:
    writer = _module(
        "writer",
        kind=ModuleKind.INPUT,
        side_effect_class=SideEffectClass.CANDIDATE_ARTIFACT_WRITE,
        permissions=(Permission.CANDIDATE_WRITE,),
        side_effects=(SideEffect.WRITE_WORKSPACE,),
    )
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    return _graph(
        (writer, sink),
        (MethodEdge("writer_to_sink", EdgeKind.SEQUENCE, "writer", "sink"),),
        entries=("writer",),
        exits=("sink",),
    )


def test_diagnostic_policy_detects_write_before_validation() -> None:
    policy = DiagnosticPolicy(require_validation_before_irreversible_write=True)

    codes = _issue_codes(diagnose_graph(_write_before_validation_graph(), policy=policy))

    assert "GRAPH_SIDE_EFFECT_ORDER_UNSAFE" in codes


def test_task_contract_rejects_unauthorized_graph_side_effect() -> None:
    task = TaskContract("task.read_only", "Forbid graph writes.")

    codes = _issue_codes(diagnose_graph(_write_before_validation_graph(), task=task))

    assert "GRAPH_SIDE_EFFECT_UNAUTHORIZED" in codes


def test_reference_edge_roles_do_not_create_reachability_or_execution_cycles() -> None:
    source = _module(
        "source",
        kind=ModuleKind.INPUT,
        memory_refs=("reference.memory",),
    )
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    reference_only = _module("reference_only", kind=ModuleKind.MEMORY)
    graph = _graph(
        (source, sink, reference_only),
        (
            MethodEdge("source_to_sink", EdgeKind.SEQUENCE, "source", "sink"),
            MethodEdge("read_reference", EdgeKind.READS_MEMORY, "source", "reference_only"),
            MethodEdge("reference_back", EdgeKind.GOVERNED_BY, "reference_only", "source"),
        ),
        entries=("source",),
        exits=("sink",),
    )

    codes = _issue_codes(validate_graph(graph))

    assert "GRAPH_UNREACHABLE_MODULE" in codes
    assert "GRAPH_ILLEGAL_CYCLE" not in codes


def test_illegal_cycle_attributes_only_causal_ordering_edges() -> None:
    source = _module("source", kind=ModuleKind.INPUT)
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (source, sink),
        (
            MethodEdge("source_to_sink", EdgeKind.SEQUENCE, "source", "sink"),
            MethodEdge("sink_to_source", EdgeKind.SEQUENCE, "sink", "source"),
            MethodEdge("governance_only", EdgeKind.GOVERNED_BY, "source", "sink"),
        ),
        entries=("source",),
        exits=("sink",),
    )

    cycle_issues = tuple(
        issue for issue in validate_graph(graph) if issue.code == "GRAPH_ILLEGAL_CYCLE"
    )

    assert len(cycle_issues) == 1
    assert cycle_issues[0].edge_ids == ("sink_to_source", "source_to_sink")


def test_reference_edge_roles_do_not_satisfy_prerequisite_order() -> None:
    prerequisite = _module(
        "prerequisite",
        kind=ModuleKind.INPUT,
        memory_refs=("reference.memory",),
    )
    dependent = _module(
        "dependent",
        kind=ModuleKind.OUTPUT,
        prerequisites=("prerequisite",),
    )
    graph = _graph(
        (prerequisite, dependent),
        (MethodEdge("reference_only", EdgeKind.READS_MEMORY, "prerequisite", "dependent"),),
        entries=("prerequisite", "dependent"),
        exits=("dependent",),
    )

    assert "GRAPH_PREREQUISITE_UNSATISFIED" in _issue_codes(validate_graph(graph))


def test_parallel_fork_with_common_downstream_join_is_resolved() -> None:
    router = _module(
        "router",
        kind=ModuleKind.INPUT,
        control=ControlSpec(ControlKind.PARALLEL, branch_labels=("left", "right")),
    )
    left = _module("left")
    right = _module("right")
    join = _module("join", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (router, left, right, join),
        (
            MethodEdge(
                "fork_left",
                EdgeKind.PARALLEL,
                "router",
                "left",
                branch_label="left",
                join_key="joined",
            ),
            MethodEdge(
                "fork_right",
                EdgeKind.PARALLEL,
                "router",
                "right",
                branch_label="right",
                join_key="joined",
            ),
            MethodEdge("left_to_join", EdgeKind.SEQUENCE, "left", "join"),
            MethodEdge("right_to_join", EdgeKind.SEQUENCE, "right", "join"),
        ),
        entries=("router",),
        exits=("join",),
    )

    assert "GRAPH_PARALLEL_JOIN_UNRESOLVED" not in _issue_codes(validate_graph(graph))


def test_parallel_branch_root_cannot_double_as_the_common_join() -> None:
    router = _module(
        "router",
        kind=ModuleKind.INPUT,
        control=ControlSpec(ControlKind.PARALLEL, branch_labels=("left", "right")),
    )
    left = _module("left")
    right = _module("right", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (router, left, right),
        (
            MethodEdge(
                "parallel_left",
                EdgeKind.PARALLEL,
                "router",
                "left",
                branch_label="left",
                join_key="joined",
            ),
            MethodEdge(
                "parallel_right",
                EdgeKind.PARALLEL,
                "router",
                "right",
                branch_label="right",
                join_key="joined",
            ),
            MethodEdge("left_to_right", EdgeKind.SEQUENCE, "left", "right"),
        ),
        entries=("router",),
        exits=("right",),
    )

    assert "GRAPH_PARALLEL_JOIN_UNRESOLVED" in _issue_codes(validate_graph(graph))


def test_evidence_only_paths_cannot_satisfy_parallel_common_join() -> None:
    router = _module(
        "router",
        kind=ModuleKind.INPUT,
        control=ControlSpec(ControlKind.PARALLEL, branch_labels=("left", "right")),
    )
    left = _module("left")
    right = _module("right")
    join = _module("join", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (router, left, right, join),
        (
            MethodEdge(
                "parallel_left",
                EdgeKind.PARALLEL,
                "router",
                "left",
                branch_label="left",
                join_key="joined",
            ),
            MethodEdge(
                "parallel_right",
                EdgeKind.PARALLEL,
                "router",
                "right",
                branch_label="right",
                join_key="joined",
            ),
            MethodEdge("left_evidence", EdgeKind.EVIDENCE_FOR, "left", "join"),
            MethodEdge("right_evidence", EdgeKind.EVIDENCE_FOR, "right", "join"),
        ),
        entries=("router",),
        exits=("join",),
    )

    assert "GRAPH_PARALLEL_JOIN_UNRESOLVED" in _issue_codes(validate_graph(graph))


def test_multiple_fallback_routes_are_ambiguous_without_priority_contract() -> None:
    router = _module(
        "router",
        kind=ModuleKind.INPUT,
        control=ControlSpec(
            ControlKind.CONDITION,
            branch_labels=("pass", "fallback_a", "fallback_b"),
        ),
    )
    success = _module("success", kind=ModuleKind.OUTPUT)
    fallback_a = _module("fallback_a", kind=ModuleKind.OUTPUT)
    fallback_b = _module("fallback_b", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (router, success, fallback_a, fallback_b),
        (
            MethodEdge(
                "route_pass",
                EdgeKind.CONDITION_TRUE,
                "router",
                "success",
                condition_ref="condition.pass",
                branch_label="pass",
            ),
            MethodEdge(
                "route_fallback_a",
                EdgeKind.FALLBACK,
                "router",
                "fallback_a",
                condition_ref="condition.fail_a",
                branch_label="fallback_a",
            ),
            MethodEdge(
                "route_fallback_b",
                EdgeKind.FALLBACK,
                "router",
                "fallback_b",
                condition_ref="condition.fail_b",
                branch_label="fallback_b",
            ),
        ),
        entries=("router",),
        exits=("success", "fallback_a", "fallback_b"),
    )

    assert "GRAPH_FALLBACK_AMBIGUOUS" in _issue_codes(validate_graph(graph))


def test_condition_and_fallback_exhaustion_are_reported_separately() -> None:
    generic_router = _module(
        "generic_router",
        kind=ModuleKind.INPUT,
        control=ControlSpec(ControlKind.CONDITION, branch_labels=("maybe",)),
    )
    generic_exit = _module("generic_exit", kind=ModuleKind.OUTPUT)
    generic_graph = _graph(
        (generic_router, generic_exit),
        (
            MethodEdge(
                "maybe",
                EdgeKind.CONDITIONAL,
                "generic_router",
                "generic_exit",
                condition_ref="condition.maybe",
                branch_label="maybe",
            ),
        ),
        entries=("generic_router",),
        exits=("generic_exit",),
    )
    fallback_router = _module(
        "fallback_router",
        kind=ModuleKind.INPUT,
        control=ControlSpec(
            ControlKind.CONDITION,
            branch_labels=("pass", "fallback"),
        ),
    )
    fallback_exit = _module("fallback_exit", kind=ModuleKind.OUTPUT)
    exhausted_fallback = _module("exhausted_fallback", kind=ModuleKind.OUTPUT)
    fallback_graph = _graph(
        (fallback_router, fallback_exit, exhausted_fallback),
        (
            MethodEdge(
                "pass",
                EdgeKind.CONDITION_TRUE,
                "fallback_router",
                "fallback_exit",
                condition_ref="condition.pass",
                branch_label="pass",
            ),
            MethodEdge(
                "fallback",
                EdgeKind.FALLBACK,
                "fallback_router",
                "exhausted_fallback",
                condition_ref="condition.fallback",
                branch_label="fallback",
            ),
        ),
        entries=("fallback_router",),
        exits=("fallback_exit",),
    )

    assert "GRAPH_CONDITION_EXHAUSTION_MISSING" in _issue_codes(validate_graph(generic_graph))
    assert "GRAPH_FALLBACK_EXHAUSTION_MISSING" in _issue_codes(validate_graph(fallback_graph))


def test_evidence_only_path_cannot_satisfy_fallback_exhaustion() -> None:
    router = _module(
        "router",
        kind=ModuleKind.INPUT,
        control=ControlSpec(ControlKind.FALLBACK, branch_labels=("recover",)),
    )
    recovery = _module("recovery")
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (router, recovery, sink),
        (
            MethodEdge(
                "fallback",
                EdgeKind.FALLBACK,
                "router",
                "recovery",
                condition_ref="condition.recover",
                branch_label="recover",
            ),
            MethodEdge("recovery_evidence", EdgeKind.EVIDENCE_FOR, "recovery", "sink"),
        ),
        entries=("router",),
        exits=("sink",),
    )

    assert "GRAPH_FALLBACK_EXHAUSTION_MISSING" in _issue_codes(validate_graph(graph))


def test_condition_requires_a_positive_outcome_in_addition_to_exhaustion() -> None:
    router = _module(
        "router",
        kind=ModuleKind.INPUT,
        control=ControlSpec(
            ControlKind.CONDITION,
            branch_labels=("false", "fallback"),
        ),
    )
    false_exit = _module("false_exit", kind=ModuleKind.OUTPUT)
    fallback_exit = _module("fallback_exit", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (router, false_exit, fallback_exit),
        (
            MethodEdge(
                "false",
                EdgeKind.CONDITION_FALSE,
                "router",
                "false_exit",
                condition_ref="condition.false",
                branch_label="false",
            ),
            MethodEdge(
                "fallback",
                EdgeKind.FALLBACK,
                "router",
                "fallback_exit",
                condition_ref="condition.fallback",
                branch_label="fallback",
            ),
        ),
        entries=("router",),
        exits=("false_exit", "fallback_exit"),
    )

    assert "GRAPH_CONDITION_INCOMPLETE" in _issue_codes(validate_graph(graph))


def test_self_loop_declared_as_exit_still_requires_actual_non_loop_exit() -> None:
    loop = _module(
        "loop",
        kind=ModuleKind.INPUT,
        control=ControlSpec(ControlKind.LOOP, max_iterations=2),
    )
    graph = _graph(
        (loop,),
        (MethodEdge("repeat", EdgeKind.LOOP, "loop", "loop", max_iterations=2),),
        entries=("loop",),
        exits=("loop",),
    )

    codes = _issue_codes(
        diagnose_graph(graph, policy=DiagnosticPolicy(require_loop_exit=True))
    )

    assert "GRAPH_CONTROL_EXIT_MISSING" in codes


def test_evidence_ordering_is_not_an_executable_loop_continuation() -> None:
    loop = _module(
        "loop",
        kind=ModuleKind.INPUT,
        control=ControlSpec(ControlKind.LOOP, max_iterations=2),
    )
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (loop, sink),
        (
            MethodEdge("repeat", EdgeKind.LOOP, "loop", "loop", max_iterations=2),
            MethodEdge("evidence_only_exit", EdgeKind.EVIDENCE_FOR, "loop", "sink"),
        ),
        entries=("loop",),
        exits=("sink",),
    )

    codes = _issue_codes(
        diagnose_graph(graph, policy=DiagnosticPolicy(require_loop_exit=True))
    )

    assert "GRAPH_CONTROL_EXIT_MISSING" in codes


def test_evidence_gate_requires_matching_produced_and_required_specs() -> None:
    graph = _candidate_graph(gated=True)
    modules = tuple(
        dataclasses.replace(
            module,
            produced_evidence=(EvidenceSpec("wrong_evidence_ref", role="gate"),),
        )
        if module.module_id == "evidence_validation"
        else module
        for module in graph.modules
    )
    graph = dataclasses.replace(graph, modules=modules)

    assert "GRAPH_EVIDENCE_GATE_MISSING" in _issue_codes(diagnose_graph(graph))


def test_candidate_evidence_must_be_produced_by_the_validation_module() -> None:
    source = _module(
        "source",
        kind=ModuleKind.INPUT,
        produced_evidence=(EvidenceSpec("evaluation_result_ref"),),
    )
    validation = _module("validation", kind=ModuleKind.VALIDATION)
    memory = _module(
        "candidate_memory",
        kind=ModuleKind.MEMORY,
        required_evidence=(EvidenceSpec("evaluation_result_ref"),),
        side_effect_class=SideEffectClass.CANDIDATE_MEMORY_WRITE,
        permissions=(Permission.CANDIDATE_MEMORY_WRITE,),
        side_effects=(SideEffect.WRITE_WORKSPACE,),
    )
    human = _module(
        "human",
        kind=ModuleKind.DECISION,
        side_effect_class=SideEffectClass.DECISION_RECORD_APPEND_ONLY,
        permissions=(Permission.AUDIT_APPEND, Permission.AUTHORIZED_HUMAN_DECISION),
        side_effects=(SideEffect.WRITE_WORKSPACE, SideEffect.HUMAN_INTERACTION),
    )
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (source, validation, memory, human, sink),
        (
            MethodEdge("to_validation", EdgeKind.SEQUENCE, "source", "validation"),
            MethodEdge("to_memory", EdgeKind.EVIDENCE_FOR, "validation", "candidate_memory"),
            MethodEdge(
                "to_human",
                EdgeKind.WRITES_CANDIDATE_MEMORY,
                "candidate_memory",
                "human",
            ),
            MethodEdge("human_to_sink", EdgeKind.SEQUENCE, "human", "sink"),
        ),
        entries=("source",),
        exits=("sink",),
    )

    assert "GRAPH_EVIDENCE_GATE_MISSING" in _issue_codes(diagnose_graph(graph))


def test_human_gate_must_cover_every_candidate_to_exit_path() -> None:
    source = _module("source", kind=ModuleKind.INPUT)
    memory = _module(
        "candidate_memory",
        kind=ModuleKind.MEMORY,
        required_evidence=(EvidenceSpec("evaluation_result_ref"),),
        side_effect_class=SideEffectClass.CANDIDATE_MEMORY_WRITE,
        permissions=(Permission.CANDIDATE_WRITE, Permission.CANDIDATE_MEMORY_WRITE),
        side_effects=(SideEffect.WRITE_WORKSPACE,),
    )
    human = _module(
        "human",
        kind=ModuleKind.DECISION,
        side_effect_class=SideEffectClass.DECISION_RECORD_APPEND_ONLY,
        permissions=(Permission.AUDIT_APPEND, Permission.AUTHORIZED_HUMAN_DECISION),
        side_effects=(SideEffect.WRITE_WORKSPACE, SideEffect.HUMAN_INTERACTION),
    )
    validation = _module(
        "validation",
        kind=ModuleKind.VALIDATION,
        produced_evidence=(EvidenceSpec("evaluation_result_ref"),),
    )
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    graph = _graph(
        (source, validation, memory, human, sink),
        (
            MethodEdge("to_validation", EdgeKind.SEQUENCE, "source", "validation"),
            MethodEdge("to_memory", EdgeKind.EVIDENCE_FOR, "validation", "candidate_memory"),
            MethodEdge(
                "to_human",
                EdgeKind.WRITES_CANDIDATE_MEMORY,
                "candidate_memory",
                "human",
            ),
            MethodEdge("human_to_sink", EdgeKind.SEQUENCE, "human", "sink"),
            MethodEdge("bypass_human", EdgeKind.SEQUENCE, "candidate_memory", "sink"),
        ),
        entries=("source",),
        exits=("sink",),
    )

    assert "GRAPH_HUMAN_GATE_MISSING" in _issue_codes(diagnose_graph(graph))


def test_strict_reference_policy_fails_closed_without_catalog() -> None:
    policy = DiagnosticPolicy(require_known_refs=True)

    issues = diagnose_graph(_clean_graph(), known_refs=None, policy=policy)

    assert _issue_codes(issues) == ("GRAPH_REFERENCE_CATALOG_MISSING",)


def test_strict_reference_policy_checks_module_contract_ref() -> None:
    graph = _clean_graph()
    modules = tuple(
        dataclasses.replace(module, contract_ref="contract.missing")
        if module.module_id == "source"
        else module
        for module in graph.modules
    )
    graph = dataclasses.replace(graph, modules=modules)

    issues = diagnose_graph(
        graph,
        known_refs=frozenset(),
        policy=DiagnosticPolicy(require_known_refs=True),
    )

    assert _issue_codes(issues) == ("GRAPH_STALE_REFERENCE",)
    assert issues[0].module_ids == ("source",)


def test_strict_reference_policy_checks_graph_task_contract_ref() -> None:
    graph = dataclasses.replace(
        _clean_graph(),
        task_contract_ref="task.contract.missing",
    )

    issues = diagnose_graph(
        graph,
        known_refs=frozenset(),
        policy=DiagnosticPolicy(require_known_refs=True),
    )

    assert _issue_codes(issues) == ("GRAPH_STALE_REFERENCE",)
    assert issues[0].module_ids == ()


def test_self_prerequisite_is_a_prerequisite_cycle() -> None:
    module = _module(
        "self_dependent",
        kind=ModuleKind.INPUT,
        prerequisites=("self_dependent",),
    )
    graph = _graph(
        (module,),
        (),
        entries=("self_dependent",),
        exits=("self_dependent",),
    )

    assert "GRAPH_PREREQUISITE_CYCLE" in _issue_codes(validate_graph(graph))


def test_diagnostic_ordering_and_serialization_are_deterministic() -> None:
    source = _module(
        "source",
        kind=ModuleKind.INPUT,
        fallback_refs=("missing_module",),
    )
    sink = _module(
        "sink",
        kind=ModuleKind.OUTPUT,
        inputs=(PortSpec("artifact", "artifact_ref"),),
        deprecated=True,
    )
    orphan = _module("orphan")
    edge = MethodEdge("source_to_sink", EdgeKind.SEQUENCE, "source", "sink")
    graph = _graph(
        (source, sink, orphan),
        (edge,),
        entries=("source",),
        exits=("sink",),
    )
    reordered = dataclasses.replace(
        graph,
        modules=tuple(reversed(graph.modules)),
        edges=tuple(reversed(graph.edges)),
    )

    first = diagnose_graph(graph)
    second = diagnose_graph(reordered)
    first_dicts = [issue.to_dict() for issue in first]
    serialized = [json.dumps(value, sort_keys=True, separators=(",", ":")) for value in first_dicts]
    assert all(isinstance(issue, DiagnosticIssue) for issue in first)
    assert first_dicts == [issue.to_dict() for issue in second]
    assert all(set(value) == ISSUE_KEYS for value in first_dicts)
    assert serialized == sorted(serialized)


def test_diff_graphs_reports_added_removed_and_changed_contracts() -> None:
    base = _linear_graph(("alpha", "beta", "gamma"))
    alpha = _module("alpha", kind=ModuleKind.INPUT)
    gamma = _module(
        "gamma",
        description="Changed portable contract for gamma.",
    )
    delta = _module("delta", kind=ModuleKind.OUTPUT)
    target = _graph(
        (alpha, gamma, delta),
        (
            MethodEdge("edge_1", EdgeKind.SEQUENCE, "alpha", "gamma"),
            MethodEdge("edge_3", EdgeKind.SEQUENCE, "gamma", "delta"),
        ),
        entries=("alpha",),
        exits=("delta",),
        version_id="graph.diagnostic.v2",
    )

    result = diff_graphs(base, target)

    assert isinstance(result, GraphDiff)
    assert result.base_hash == base.content_hash
    assert result.target_hash == target.content_hash
    assert result.added_module_ids == ("delta",)
    assert result.removed_module_ids == ("beta",)
    assert result.changed_module_ids == ("gamma",)
    assert result.added_edge_ids == ("edge_3",)
    assert result.removed_edge_ids == ("edge_2",)
    assert result.changed_edge_ids == ("edge_1",)
    assert set(result.to_dict()) == DIFF_KEYS


def test_diff_graphs_reports_reordered_modules_deterministically() -> None:
    base = _linear_graph(("alpha", "beta", "gamma"))
    target = _linear_graph(
        ("alpha", "gamma", "beta"),
        version_id="graph.diagnostic.v2",
    )

    result = diff_graphs(base, target)

    assert result.reordered_module_ids == ("beta", "gamma")
    assert result.changed_edge_ids == ("edge_1", "edge_2")
    assert result.to_dict() == diff_graphs(base, target).to_dict()


def test_diff_graphs_reports_graph_level_contract_changes() -> None:
    base = _clean_graph()
    target = dataclasses.replace(
        base,
        version_id="graph.diagnostic.v2",
        purpose="Changed graph-level applicability contract.",
        applicability_conditions=("new_scope",),
    )

    result = diff_graphs(base, target)

    assert result.graph_contract_changed is True
    assert result.to_dict()["graph_contract_changed"] is True
    assert set(result.to_dict()) == DIFF_KEYS


def test_diff_graphs_preserves_large_reorder_correctness() -> None:
    module_ids = tuple(f"module_{index:03d}" for index in range(256))
    base = _linear_graph(
        module_ids,
        version_id="graph.diagnostic.large.v1",
    )
    target = _linear_graph(
        tuple(reversed(module_ids)),
        version_id="graph.diagnostic.large.v2",
    )

    result = diff_graphs(base, target)

    assert result.reordered_module_ids == module_ids


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("require_known_refs", 1),
        ("max_modules", 0),
        ("max_edges", -1),
        ("max_issues", 0),
        ("max_analysis_steps", 0),
    ),
)
def test_diagnostic_policy_rejects_invalid_budget_and_strictness_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match="policy|boolean|budget|max_"):
        DiagnosticPolicy(**{field: value})


@pytest.mark.parametrize(
    ("budget_field", "budget_value"),
    (
        ("max_modules", 1),
        ("max_edges", 1),
    ),
)
def test_analysis_budget_fails_before_graph_traversal(
    budget_field: str,
    budget_value: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("analysis traversed an over-budget graph")

    monkeypatch.setattr(diagnostics, "_adjacency", forbidden)

    graph = (
        _linear_graph(("alpha", "beta", "gamma"))
        if budget_field == "max_edges"
        else _clean_graph()
    )
    policy = DiagnosticPolicy(**{budget_field: budget_value})
    issues = diagnose_graph(graph, policy=policy)

    assert _issue_codes(issues) == ("GRAPH_ANALYSIS_BUDGET_EXCEEDED",)
    assert issues[0].severity == "error"


def test_analysis_step_budget_fails_before_graph_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _clean_graph()

    def forbidden_traversal(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("analysis traversal ran after its estimated work budget was exceeded")

    monkeypatch.setattr(diagnostics, "_adjacency", forbidden_traversal)

    issues = diagnose_graph(
        graph,
        policy=DiagnosticPolicy(max_analysis_steps=1),
    )

    assert _issue_codes(issues) == ("GRAPH_ANALYSIS_BUDGET_EXCEEDED",)


def test_issue_budget_is_stably_bounded_with_terminal_budget_error() -> None:
    graph = _clean_graph()
    orphans = tuple(_module(f"orphan_{index}") for index in range(8))
    graph = dataclasses.replace(graph, modules=(*graph.modules, *orphans))

    issues = diagnose_graph(graph, policy=DiagnosticPolicy(max_issues=3))

    assert len(issues) == 3
    assert _issue_codes(issues)[-1] == "GRAPH_ANALYSIS_BUDGET_EXCEEDED"
    assert [issue.to_dict() for issue in issues] == [
        issue.to_dict()
        for issue in diagnose_graph(graph, policy=DiagnosticPolicy(max_issues=3))
    ]


def test_diagnostics_are_read_only_and_do_not_touch_runtime_or_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _clean_graph()
    target = _linear_graph(("alpha", "beta"), version_id="graph.diagnostic.v2")
    snapshots = {
        "base_bytes": base.canonical_bytes,
        "base_hash": base.content_hash,
        "base_dict": copy.deepcopy(base.to_dict()),
        "target_bytes": target.canonical_bytes,
        "target_hash": target.content_hash,
        "target_dict": copy.deepcopy(target.to_dict()),
    }

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("static diagnostics attempted runtime or file access")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(importlib, "import_module", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    validate_graph(base)
    diagnose_graph(base)
    diff_graphs(base, target)

    assert base.canonical_bytes == snapshots["base_bytes"]
    assert base.content_hash == snapshots["base_hash"]
    assert base.to_dict() == snapshots["base_dict"]
    assert target.canonical_bytes == snapshots["target_bytes"]
    assert target.content_hash == snapshots["target_hash"]
    assert target.to_dict() == snapshots["target_dict"]


def test_static_semantics_diagnostics_source_has_no_runtime_dependencies() -> None:
    source = Path(diagnostics.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 10))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imported & {
        "anthropic",
        "importlib",
        "langgraph",
        "openai",
        "os",
        "pathlib",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }

    direct_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    qualified_calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert not direct_calls & {"__import__", "compile", "eval", "exec", "open"}
    assert not qualified_calls & {
        "importlib.import_module",
        "os.system",
        "subprocess.Popen",
        "subprocess.run",
        "urllib.urlopen",
    }
