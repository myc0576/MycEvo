from __future__ import annotations

import ast
import builtins
import copy
import dataclasses
import hashlib
import importlib
import json
import socket
import subprocess
from pathlib import Path

import pytest

import mycevo.changesets as changesets
from mycevo.changesets import (
    BoundaryPatch,
    ChangeSetBudget,
    ChangeSetError,
    ChangeSetPolicy,
    ChangeSetStatus,
    DeltaCategory,
    DeltaDisposition,
    DeltaOperation,
    DeltaStatus,
    Disposition,
    EvolutionDelta,
    ExternalProposal,
    GraphPatch,
    MaterializationContext,
    MaterializationReceipt,
    NoChangeReceipt,
    ObjectExpectation,
    ObjectKind,
    ProposalProvenance,
    SelectionStatus,
    materialize_selection,
    object_content_hash,
    parse_external_proposal,
    resolve_selection,
    validate_changeset,
    validate_changeset_transition,
    validate_delta_transition,
)
from mycevo.diagnostics import DiagnosticPolicy, validate_graph
from mycevo.workflow_ir import (
    EdgeKind,
    GraphLineage,
    MethodEdge,
    MethodGraphVersion,
    MethodModule,
    ModuleKind,
    TaskContract,
    canonical_json_bytes,
    graph_from_json_bytes,
)


FIXTURES = Path(__file__).parent / "fixtures" / "changesets"
OPERATION_VALUES = {"add", "delete", "merge", "split", "replace", "reorder"}


def _base_graph() -> MethodGraphVersion:
    return graph_from_json_bytes((FIXTURES / "base_graph.json").read_bytes())


def _load_operation_goldens() -> dict[str, object]:
    value = json.loads((FIXTURES / "operation_goldens.json").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _module(
    module_id: str,
    *,
    version_id: str | None = None,
    description: str | None = None,
    deprecated: bool = False,
) -> MethodModule:
    return MethodModule(
        module_id=module_id,
        version_id=version_id or f"{module_id}.v1",
        kind=ModuleKind.TRANSFORM,
        name=module_id.replace("_", " ").title(),
        description=description or f"Golden module {module_id}.",
        deprecated=deprecated,
    )


def _edge(edge_id: str, source: str, target: str) -> MethodEdge:
    return MethodEdge(edge_id, EdgeKind.SEQUENCE, source, target)


def _module_expectation(graph: MethodGraphVersion, module_id: str) -> ObjectExpectation:
    module = next(module for module in graph.modules if module.module_id == module_id)
    return ObjectExpectation(ObjectKind.MODULE, module_id, object_content_hash(module))


def _edge_expectation(graph: MethodGraphVersion, edge_id: str) -> ObjectExpectation:
    edge = next(edge for edge in graph.edges if edge.edge_id == edge_id)
    return ObjectExpectation(ObjectKind.EDGE, edge_id, object_content_hash(edge))


def _provenance() -> ProposalProvenance:
    return ProposalProvenance(
        source_kind="external_agent",
        source_id="fixture.agent",
        source_refs=("fixture:changesets",),
        evidence_refs=("evidence:diagnostic",),
        confidence="medium",
        falsification_conditions=("candidate graph fails strict revalidation",),
    )


def _generous_policy(**budget_overrides: int) -> ChangeSetPolicy:
    budget = dataclasses.replace(ChangeSetBudget(), **budget_overrides)
    return ChangeSetPolicy(
        budget=budget,
        allowed_operations=tuple(DeltaOperation(value) for value in sorted(OPERATION_VALUES)),
    )


def _context(*extra_refs: str) -> MaterializationContext:
    return MaterializationContext(
        task=TaskContract("task.changeset", "Strictly revalidate a materialized candidate."),
        known_refs=frozenset(
            {
                "evidence:diagnostic",
                "evaluator.static",
                "rule.alpha",
                "memory.beta",
                *extra_refs,
            }
        ),
        diagnostic_policy=DiagnosticPolicy(require_known_refs=True),
    )


def _operation_patch(operation: DeltaOperation, base: MethodGraphVersion) -> GraphPatch:
    if operation is DeltaOperation.ADD:
        return GraphPatch(
            expectations=(_edge_expectation(base, "beta_gamma"),),
            remove_edge_ids=("beta_gamma",),
            put_modules=(_module("review"),),
            put_edges=(
                _edge("beta_review", "beta", "review"),
                _edge("review_gamma", "review", "gamma"),
            ),
        )
    if operation is DeltaOperation.DELETE:
        return GraphPatch(
            expectations=(
                _module_expectation(base, "beta"),
                _edge_expectation(base, "alpha_beta"),
                _edge_expectation(base, "beta_gamma"),
            ),
            remove_module_ids=("beta",),
            remove_edge_ids=("alpha_beta", "beta_gamma"),
            put_edges=(_edge("alpha_gamma", "alpha", "gamma"),),
        )
    if operation is DeltaOperation.MERGE:
        return GraphPatch(
            expectations=(
                _module_expectation(base, "alpha"),
                _module_expectation(base, "beta"),
                _edge_expectation(base, "input_alpha"),
                _edge_expectation(base, "alpha_beta"),
                _edge_expectation(base, "beta_gamma"),
            ),
            remove_module_ids=("alpha", "beta"),
            remove_edge_ids=("input_alpha", "alpha_beta", "beta_gamma"),
            put_modules=(_module("alpha_beta_merged"),),
            put_edges=(
                _edge("input_merged", "input", "alpha_beta_merged"),
                _edge("merged_gamma", "alpha_beta_merged", "gamma"),
            ),
        )
    if operation is DeltaOperation.SPLIT:
        return GraphPatch(
            expectations=(
                _module_expectation(base, "beta"),
                _edge_expectation(base, "alpha_beta"),
                _edge_expectation(base, "beta_gamma"),
            ),
            remove_module_ids=("beta",),
            remove_edge_ids=("alpha_beta", "beta_gamma"),
            put_modules=(_module("beta_prepare"), _module("beta_apply")),
            put_edges=(
                _edge("alpha_beta_prepare", "alpha", "beta_prepare"),
                _edge("beta_prepare_apply", "beta_prepare", "beta_apply"),
                _edge("beta_apply_gamma", "beta_apply", "gamma"),
            ),
        )
    if operation is DeltaOperation.REPLACE:
        beta = next(module for module in base.modules if module.module_id == "beta")
        return GraphPatch(
            expectations=(_module_expectation(base, "beta"),),
            remove_module_ids=("beta",),
            put_modules=(
                dataclasses.replace(
                    beta,
                    version_id="beta.v2",
                    prerequisites=("alpha",),
                    deprecated=False,
                ),
            ),
        )
    if operation is DeltaOperation.REORDER:
        return GraphPatch(
            expectations=(
                _edge_expectation(base, "alpha_beta"),
                _edge_expectation(base, "beta_gamma"),
                _edge_expectation(base, "gamma_sink"),
            ),
            remove_edge_ids=("alpha_beta", "beta_gamma", "gamma_sink"),
            put_edges=(
                _edge("alpha_gamma", "alpha", "gamma"),
                _edge("gamma_beta", "gamma", "beta"),
                _edge("beta_sink", "beta", "sink"),
            ),
        )
    raise AssertionError(f"unhandled operation: {operation}")


def _delta(
    delta_id: str,
    operation: DeltaOperation,
    base: MethodGraphVersion,
    *,
    category: DeltaCategory = DeltaCategory.TOPOLOGY,
    patch: GraphPatch | None = None,
    depends_on: tuple[str, ...] = (),
    conflicts_with: tuple[str, ...] = (),
    evaluator_requirement_refs: tuple[str, ...] = ("evaluator.static",),
) -> EvolutionDelta:
    return EvolutionDelta(
        delta_id=delta_id,
        category=category,
        operation=operation,
        patch=patch or _operation_patch(operation, base),
        rationale=f"Exercise the {operation.value} operation contract.",
        depends_on=depends_on,
        conflicts_with=conflicts_with,
        evidence_refs=("evidence:diagnostic",),
        falsification_conditions=("strict candidate revalidation fails",),
        evaluator_requirement_refs=evaluator_requirement_refs,
    )


def _proposal(
    base: MethodGraphVersion,
    deltas: tuple[EvolutionDelta, ...],
    *,
    proposal_id: str = "proposal.golden",
) -> ExternalProposal:
    return ExternalProposal(
        proposal_id=proposal_id,
        base_graph_hash=base.content_hash,
        provenance=_provenance(),
        deltas=deltas,
    )


def _validated(
    base: MethodGraphVersion,
    deltas: tuple[EvolutionDelta, ...],
    *,
    policy: ChangeSetPolicy | ChangeSetBudget | None = None,
    proposal_id: str = "proposal.golden",
):
    return validate_changeset(
        base,
        _proposal(base, deltas, proposal_id=proposal_id),
        policy or _generous_policy(),
    )


def test_operation_enum_and_base_fixture_are_frozen() -> None:
    graph = _base_graph()
    goldens = _load_operation_goldens()

    assert {item.value for item in DeltaOperation} == OPERATION_VALUES
    assert goldens["schema"] == "mycevo.changeset_operation_goldens.v1"
    assert goldens["base_graph_hash"] == graph.content_hash
    assert {case["operation"] for case in goldens["cases"]} == OPERATION_VALUES


def test_external_proposal_strict_parse_round_trip_is_canonical() -> None:
    base = _base_graph()
    proposal = _proposal(
        base,
        (_delta("delta.add", DeltaOperation.ADD, base),),
    )

    parsed = parse_external_proposal(proposal.canonical_bytes, _generous_policy())

    assert parsed.to_dict() == proposal.to_dict()
    assert parsed.canonical_bytes == proposal.canonical_bytes
    assert parsed.content_hash == proposal.content_hash


def test_external_proposal_rejects_unknown_and_duplicate_json_fields() -> None:
    base = _base_graph()
    proposal = _proposal(
        base,
        (_delta("delta.add", DeltaOperation.ADD, base),),
    )
    unknown = proposal.to_dict()
    unknown["promotion_allowed"] = True
    preview_authority = proposal.to_dict()
    preview_authority["deltas"][0]["disposition"] = "accepted"

    with pytest.raises(ChangeSetError) as unknown_error:
        parse_external_proposal(json.dumps(unknown).encode("utf-8"))
    with pytest.raises(ChangeSetError) as disposition_error:
        parse_external_proposal(json.dumps(preview_authority).encode("utf-8"))
    with pytest.raises(ChangeSetError) as duplicate_error:
        parse_external_proposal(
            b'{"schema":"mycevo.external_proposal.v1","schema":"duplicate"}'
        )

    assert unknown_error.value.code == "CS_SCHEMA_INVALID"
    assert disposition_error.value.code == "CS_SCHEMA_INVALID"
    assert duplicate_error.value.code == "CS_SCHEMA_INVALID"


def test_external_proposal_payload_budget_fails_before_json_parsing() -> None:
    payload = b"{" + (b"x" * 64)

    with pytest.raises(ChangeSetError) as error:
        parse_external_proposal(
            payload,
            ChangeSetBudget(max_payload_bytes=len(payload) - 1),
        )

    assert error.value.code == "CS_BUDGET_EXCEEDED"


def test_external_proposal_and_changeset_match_draft_202012_schemas() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    schema_dir = Path(changesets.__file__).parent / "schemas"
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in schema_dir.glob("*.schema.json")
    }
    registry = referencing.Registry().with_resources(
        [
            (schema["$id"], referencing.Resource.from_contents(schema))
            for schema in schemas.values()
        ]
    )
    base = _base_graph()
    proposal = _proposal(
        base,
        (_delta("delta.add", DeltaOperation.ADD, base),),
    )
    change_set = validate_changeset(base, proposal, _generous_policy())

    for schema_name, instance in (
        ("external-proposal.v1.schema.json", proposal.to_dict()),
        ("evolution-changeset.v1.schema.json", change_set.to_dict()),
    ):
        schema = schemas[schema_name]
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema, registry=registry)
        assert list(validator.iter_errors(instance)) == []

        unauthorized = copy.deepcopy(instance)
        unauthorized["promotion_allowed"] = True
        assert list(validator.iter_errors(unauthorized))

    operation_enum = schemas["external-proposal.v1.schema.json"]["$defs"]["delta"][
        "properties"
    ]["operation"]["enum"]
    assert set(operation_enum) == OPERATION_VALUES

    reparsed = changesets.EvolutionChangeSet.from_dict(change_set.to_dict())
    assert reparsed.canonical_bytes == change_set.canonical_bytes
    invalid_changeset = change_set.to_dict()
    invalid_changeset["evaluation_passed"] = True
    with pytest.raises(ChangeSetError) as parse_error:
        changesets.EvolutionChangeSet.from_dict(invalid_changeset)
    assert parse_error.value.code == "CS_SCHEMA_INVALID"


def _expected_policy_hash(change_set: changesets.EvolutionChangeSet) -> str:
    payload = {
        "allowed_operations": [item.value for item in change_set.allowed_operations],
        "effective_budget": change_set.effective_budget.to_dict(),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _policy_tamper(
    change_set: changesets.EvolutionChangeSet,
    dimension: str,
) -> object:
    if dimension == "budget":
        return dataclasses.replace(
            change_set.effective_budget,
            max_result_modules=1,
        )
    if dimension == "allowlist":
        return (DeltaOperation.DELETE,)
    raise AssertionError(f"unknown policy tamper dimension: {dimension}")


def _force_policy_tamper(
    change_set: changesets.EvolutionChangeSet,
    dimension: str,
) -> changesets.EvolutionChangeSet:
    field_name = "effective_budget" if dimension == "budget" else "allowed_operations"
    object.__setattr__(change_set, field_name, _policy_tamper(change_set, dimension))
    return change_set


def _rebind_resolution_to_changeset(
    resolution: changesets.SelectionResolution,
    change_set: changesets.EvolutionChangeSet,
) -> changesets.SelectionResolution:
    change_set_hash = change_set.content_hash
    payload = {
        "schema": changesets.SELECTION_SCHEMA,
        "change_set_hash": change_set_hash,
        "decisions": [item.to_dict() for item in resolution.dispositions],
        "closure_delta_ids": list(resolution.closure_delta_ids),
        "application_order": list(resolution.application_order),
    }
    return dataclasses.replace(
        resolution,
        change_set_hash=change_set_hash,
        selection_hash=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    )


def test_changeset_stores_and_authenticates_the_complete_effective_policy() -> None:
    base = _base_graph()
    delta = _delta("delta.add", DeltaOperation.ADD, base)
    policy = ChangeSetPolicy(
        budget=dataclasses.replace(ChangeSetBudget(), max_result_modules=12),
        allowed_operations=(DeltaOperation.REPLACE, DeltaOperation.ADD),
    )

    change_set = _validated(base, (delta,), policy=policy)

    assert change_set.allowed_operations == (
        DeltaOperation.ADD,
        DeltaOperation.REPLACE,
    )
    assert change_set.effective_budget == policy.budget
    assert change_set.policy_hash == _expected_policy_hash(change_set)
    assert change_set.to_dict()["allowed_operations"] == ["add", "replace"]
    assert changesets.EvolutionChangeSet.from_dict(change_set.to_dict()) == change_set


@pytest.mark.parametrize("dimension", ("budget", "allowlist"))
def test_policy_tampering_rejects_at_typed_construction_and_from_dict(
    dimension: str,
) -> None:
    base = _base_graph()
    delta = _delta("delta.add", DeltaOperation.ADD, base)
    change_set = _validated(base, (delta,))
    field_name = "effective_budget" if dimension == "budget" else "allowed_operations"

    with pytest.raises(ChangeSetError) as construction_error:
        dataclasses.replace(
            change_set,
            **{field_name: _policy_tamper(change_set, dimension)},
        )

    serialized = copy.deepcopy(change_set.to_dict())
    if dimension == "budget":
        serialized["effective_budget"]["max_result_modules"] = 1
    else:
        serialized["allowed_operations"] = ["delete"]
    with pytest.raises(ChangeSetError) as parse_error:
        changesets.EvolutionChangeSet.from_dict(serialized)

    assert construction_error.value.code == "CS_POLICY_MISMATCH"
    assert parse_error.value.code == "CS_POLICY_MISMATCH"


@pytest.mark.parametrize("dimension", ("budget", "allowlist"))
def test_policy_tampering_rejects_at_selection_resolution(
    dimension: str,
) -> None:
    base = _base_graph()
    delta = _delta("delta.add", DeltaOperation.ADD, base)
    change_set = _force_policy_tamper(_validated(base, (delta,)), dimension)

    with pytest.raises(ChangeSetError) as error:
        resolve_selection(change_set, {delta.delta_id: "accepted"})

    assert error.value.code == "CS_POLICY_MISMATCH"


@pytest.mark.parametrize("dimension", ("budget", "allowlist"))
def test_policy_tampering_rejects_at_materialization_even_with_recomputed_selection(
    dimension: str,
) -> None:
    base = _base_graph()
    delta = _delta("delta.add", DeltaOperation.ADD, base)
    change_set = _validated(base, (delta,))
    resolution = resolve_selection(change_set, {delta.delta_id: "accepted"})
    tampered = _force_policy_tamper(change_set, dimension)
    rebound = _rebind_resolution_to_changeset(resolution, tampered)

    with pytest.raises(ChangeSetError) as error:
        materialize_selection(base, tampered, rebound, _context())

    assert error.value.code == "CS_POLICY_MISMATCH"


@pytest.mark.parametrize("operation", tuple(DeltaOperation), ids=lambda item: item.value)
def test_all_six_operations_materialize_exact_golden_structure(
    operation: DeltaOperation,
) -> None:
    base = _base_graph()
    before = (base.canonical_bytes, base.content_hash, copy.deepcopy(base.to_dict()))
    delta = _delta(f"delta.{operation.value}", operation, base)
    change_set = _validated(
        base,
        (delta,),
        proposal_id=f"proposal.{operation.value}",
    )
    resolution = resolve_selection(change_set, {delta.delta_id: Disposition.ACCEPTED})

    receipt = materialize_selection(base, change_set, resolution, _context())

    assert isinstance(receipt, MaterializationReceipt)
    golden = next(
        case
        for case in _load_operation_goldens()["cases"]
        if case["operation"] == operation.value
    )
    assert [module.module_id for module in receipt.candidate_graph.modules] == golden["expected_module_ids"]
    assert [edge.edge_id for edge in receipt.candidate_graph.edges] == golden["expected_edge_ids"]
    for module_id, version_id in golden.get("expected_module_versions", {}).items():
        module = next(
            item for item in receipt.candidate_graph.modules if item.module_id == module_id
        )
        assert module.version_id == version_id
    assert receipt.base_graph_hash == base.content_hash
    assert receipt.change_set_hash == change_set.content_hash
    assert receipt.selection_hash == resolution.selection_hash
    assert resolution.selection_hash == golden["expected_selection_hash"]
    assert receipt.closure_delta_ids == (delta.delta_id,)
    assert receipt.candidate_graph.version_id == (
        f"{base.graph_id}.candidate.{resolution.selection_hash[:24]}"
    )
    assert receipt.candidate_graph.lineage.parent_hashes == (base.content_hash,)
    assert receipt.candidate_graph.lineage.created_from_changeset == change_set.change_set_id
    assert receipt.candidate_graph_hash == receipt.candidate_graph.content_hash
    assert receipt.candidate_graph_hash == golden["expected_candidate_graph_hash"]
    assert receipt.diagnostics == ()
    assert receipt.validation_context_hash == _context().content_hash
    assert receipt.reevaluation.to_dict() == {
        "base_graph_hash": base.content_hash,
        "change_set_hash": change_set.content_hash,
        "selection_hash": resolution.selection_hash,
        "candidate_graph_hash": receipt.candidate_graph_hash,
        "evaluator_requirement_refs": ["evaluator.static"],
        "requires_fresh_evaluation": True,
    }
    assert (base.canonical_bytes, base.content_hash, base.to_dict()) == before


def test_topology_replace_cannot_alias_add_without_an_exact_predecessor() -> None:
    base = _base_graph()
    alias_add = _delta(
        "delta.replace_alias_add",
        DeltaOperation.REPLACE,
        base,
        patch=GraphPatch(put_modules=(_module("new_without_predecessor"),)),
    )

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (alias_add,))

    assert error.value.code == "DELTA_STRUCTURE_INVALID"


def test_exact_expectation_guarded_implicit_same_id_replace_remains_allowed() -> None:
    base = _base_graph()
    alpha = next(module for module in base.modules if module.module_id == "alpha")
    implicit_replace = _delta(
        "delta.implicit_replace",
        DeltaOperation.REPLACE,
        base,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "alpha"),),
            put_modules=(
                dataclasses.replace(
                    alpha,
                    version_id="alpha.v2",
                    prerequisites=("input",),
                ),
            ),
        ),
    )
    change_set = _validated(base, (implicit_replace,))
    resolution = resolve_selection(
        change_set,
        {implicit_replace.delta_id: "accepted"},
    )

    receipt = materialize_selection(base, change_set, resolution, _context())

    assert isinstance(receipt, MaterializationReceipt)
    alpha_v2 = next(
        module for module in receipt.candidate_graph.modules if module.module_id == "alpha"
    )
    assert alpha_v2.version_id == "alpha.v2"
    assert alpha_v2.prerequisites == ("input",)


@pytest.mark.parametrize(
    "relation_kind",
    (EdgeKind.GOVERNED_BY, EdgeKind.READS_MEMORY),
    ids=("governed_by", "reads_memory"),
)
def test_reorder_cannot_remove_non_ordering_relation_edges(
    relation_kind: EdgeKind,
) -> None:
    base = _base_graph()
    relation = MethodEdge(
        edge_id=f"alpha_{relation_kind.value}_beta",
        kind=relation_kind,
        source_module="alpha",
        target_module="beta",
    )
    modules = base.modules
    if relation_kind is EdgeKind.READS_MEMORY:
        modules = tuple(
            dataclasses.replace(module, memory_refs=("memory.alpha",))
            if module.module_id == "alpha"
            else module
            for module in base.modules
        )
    graph = dataclasses.replace(
        base,
        modules=modules,
        edges=tuple(sorted((*base.edges, relation), key=lambda item: item.edge_id)),
    )
    reorder = _delta(
        f"delta.reorder_{relation_kind.value}",
        DeltaOperation.REORDER,
        graph,
        patch=GraphPatch(
            expectations=(_edge_expectation(graph, relation.edge_id),),
            remove_edge_ids=(relation.edge_id,),
            put_edges=(_edge("alpha_gamma_extra", "alpha", "gamma"),),
        ),
    )

    with pytest.raises(ChangeSetError) as error:
        _validated(graph, (reorder,))

    assert error.value.code == "DELTA_STRUCTURE_INVALID"


@pytest.mark.parametrize(
    "category",
    (DeltaCategory.CONTENT, DeltaCategory.RULES, DeltaCategory.MEMORY),
    ids=("content", "rules", "memory"),
)
def test_non_topology_planes_reject_delete_even_for_an_isolated_module(
    category: DeltaCategory,
) -> None:
    base = _base_graph()
    isolated = _module("isolated")
    graph = dataclasses.replace(
        base,
        modules=tuple(sorted((*base.modules, isolated), key=lambda item: item.module_id)),
    )
    delete = _delta(
        f"delta.{category.value}_delete",
        DeltaOperation.DELETE,
        graph,
        category=category,
        patch=GraphPatch(
            expectations=(_module_expectation(graph, isolated.module_id),),
            remove_module_ids=(isolated.module_id,),
        ),
    )

    with pytest.raises(ChangeSetError) as error:
        _validated(graph, (delete,))

    assert error.value.code == "DELTA_STRUCTURE_INVALID"


def test_materialization_is_deterministic_under_repeated_calls() -> None:
    base = _base_graph()
    delta = _delta("delta.reorder", DeltaOperation.REORDER, base)
    change_set = _validated(base, (delta,))
    resolution = resolve_selection(change_set, {delta.delta_id: "accepted"})

    first = materialize_selection(base, change_set, resolution, _context())
    second = materialize_selection(base, change_set, resolution, _context())

    assert isinstance(first, MaterializationReceipt)
    assert first.to_dict() == second.to_dict()


def test_empty_accepted_selection_returns_no_change_without_graph_or_evaluation() -> None:
    base = _base_graph()
    delta = _delta("delta.add", DeltaOperation.ADD, base)
    change_set = _validated(base, (delta,))
    resolution = resolve_selection(change_set, {delta.delta_id: Disposition.REJECTED})

    receipt = materialize_selection(base, change_set, resolution, _context())

    assert resolution.status is SelectionStatus.NO_CHANGE
    assert isinstance(receipt, NoChangeReceipt)
    assert receipt.to_dict() == {
        "base_graph_hash": base.content_hash,
        "change_set_hash": change_set.content_hash,
        "selection_hash": resolution.selection_hash,
        "rejected_delta_ids": [delta.delta_id],
        "deferred_delta_ids": [],
        "status": "no_change",
    }
    assert not hasattr(receipt, "candidate_graph")
    assert not hasattr(receipt, "reevaluation")


def _four_plane_deltas(base: MethodGraphVersion) -> tuple[EvolutionDelta, ...]:
    module_by_id = {module.module_id: module for module in base.modules}
    rules = _delta(
        "delta.rules",
        DeltaOperation.REPLACE,
        base,
        category=DeltaCategory.RULES,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "alpha"),),
            remove_module_ids=("alpha",),
            put_modules=(
                dataclasses.replace(
                    module_by_id["alpha"],
                    version_id="alpha.v2",
                    rule_refs=("rule.alpha",),
                ),
            ),
        ),
        evaluator_requirement_refs=("evaluator.rules",),
    )
    topology = _delta(
        "delta.topology",
        DeltaOperation.ADD,
        base,
        depends_on=("delta.rules",),
    )
    content = _delta(
        "delta.content",
        DeltaOperation.REPLACE,
        base,
        category=DeltaCategory.CONTENT,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "gamma"),),
            remove_module_ids=("gamma",),
            put_modules=(
                _module(
                    "gamma",
                    version_id="gamma.v2",
                    description="Candidate content contract for gamma.",
                ),
            ),
        ),
        conflicts_with=("delta.memory",),
        evaluator_requirement_refs=("evaluator.content",),
    )
    memory = _delta(
        "delta.memory",
        DeltaOperation.REPLACE,
        base,
        category=DeltaCategory.MEMORY,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "beta"),),
            remove_module_ids=("beta",),
            put_modules=(
                dataclasses.replace(
                    module_by_id["beta"],
                    version_id="beta.v2",
                    memory_refs=("memory.beta",),
                ),
            ),
        ),
        conflicts_with=("delta.content",),
        evaluator_requirement_refs=("evaluator.memory",),
    )
    return rules, topology, content, memory


def test_four_plane_mixed_selection_materializes_only_exact_dependency_closure() -> None:
    base = _base_graph()
    deltas = _four_plane_deltas(base)
    change_set = _validated(base, tuple(reversed(deltas)), proposal_id="proposal.mixed")
    dispositions = {
        "delta.topology": "accepted",
        "delta.rules": "accepted",
        "delta.content": "rejected",
        "delta.memory": "deferred",
    }

    resolution = resolve_selection(change_set, dispositions)
    receipt = materialize_selection(base, change_set, resolution, _context())

    assert resolution.closure_delta_ids == ("delta.rules", "delta.topology")
    assert resolution.application_order == ("delta.rules", "delta.topology")
    assert isinstance(receipt, MaterializationReceipt)
    modules = {module.module_id: module for module in receipt.candidate_graph.modules}
    assert modules["alpha"].version_id == "alpha.v2"
    assert modules["beta"].version_id == "beta.v1"
    assert modules["gamma"].version_id == "gamma.v1"
    assert "review" in modules
    assert receipt.reevaluation.evaluator_requirement_refs == (
        "evaluator.rules",
        "evaluator.static",
    )


def test_delta_and_disposition_permutations_preserve_closure_selection_and_result_hashes() -> None:
    base = _base_graph()
    deltas = _four_plane_deltas(base)
    first = _validated(base, deltas, proposal_id="proposal.permutation")
    second = _validated(base, tuple(reversed(deltas)), proposal_id="proposal.permutation")
    forward = {
        "delta.rules": "accepted",
        "delta.topology": "accepted",
        "delta.content": "rejected",
        "delta.memory": "deferred",
    }
    reverse = dict(reversed(tuple(forward.items())))

    first_resolution = resolve_selection(first, forward)
    second_resolution = resolve_selection(second, reverse)
    first_receipt = materialize_selection(base, first, first_resolution, _context())
    second_receipt = materialize_selection(base, second, second_resolution, _context())

    assert first.content_hash == second.content_hash
    assert first_resolution.to_dict() == second_resolution.to_dict()
    assert first_receipt.to_dict() == second_receipt.to_dict()


@pytest.mark.parametrize("blocked_disposition", (Disposition.REJECTED, Disposition.DEFERRED))
def test_rejected_or_deferred_dependency_blocks_accepted_dependent(
    blocked_disposition: Disposition,
) -> None:
    base = _base_graph()
    rules, topology, _, _ = _four_plane_deltas(base)
    change_set = _validated(base, (topology, rules))

    with pytest.raises(ChangeSetError) as error:
        resolve_selection(
            change_set,
            {
                topology.delta_id: Disposition.ACCEPTED,
                rules.delta_id: blocked_disposition,
            },
        )

    assert error.value.code == "DELTA_DEPENDENCY_MISSING"


def test_selection_requires_one_complete_disposition_per_delta() -> None:
    base = _base_graph()
    rules, topology, _, _ = _four_plane_deltas(base)
    change_set = _validated(base, (rules, topology))

    with pytest.raises(ChangeSetError) as error:
        resolve_selection(change_set, {topology.delta_id: Disposition.ACCEPTED})

    assert error.value.code == "CS_DECISION_INCOMPLETE"


def test_selected_symmetric_conflict_blocks_whole_selection_but_excluded_conflict_does_not() -> None:
    base = _base_graph()
    first = _delta(
        "delta.first",
        DeltaOperation.ADD,
        base,
        conflicts_with=("delta.second",),
    )
    second = _delta(
        "delta.second",
        DeltaOperation.REPLACE,
        base,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "alpha"),),
            remove_module_ids=("alpha",),
            put_modules=(_module("alpha", version_id="alpha.v2"),),
        ),
        conflicts_with=("delta.first",),
    )
    change_set = _validated(base, (first, second))

    with pytest.raises(ChangeSetError) as conflict:
        resolve_selection(
            change_set,
            {first.delta_id: "accepted", second.delta_id: "accepted"},
        )
    allowed = resolve_selection(
        change_set,
        {first.delta_id: "accepted", second.delta_id: "rejected"},
    )

    assert conflict.value.code == "DELTA_CONFLICT"
    assert allowed.closure_delta_ids == (first.delta_id,)


def test_asymmetric_conflict_and_dependency_cycle_reject_changeset_validation() -> None:
    base = _base_graph()
    asymmetric = (
        _delta(
            "delta.first",
            DeltaOperation.ADD,
            base,
            conflicts_with=("delta.second",),
        ),
        _delta(
            "delta.second",
            DeltaOperation.REPLACE,
            base,
            patch=GraphPatch(
                expectations=(_module_expectation(base, "alpha"),),
                remove_module_ids=("alpha",),
                put_modules=(_module("alpha", version_id="alpha.v2"),),
            ),
        ),
    )
    cyclic = (
        _delta(
            "delta.first",
            DeltaOperation.ADD,
            base,
            depends_on=("delta.second",),
        ),
        _delta(
            "delta.second",
            DeltaOperation.REPLACE,
            base,
            patch=GraphPatch(
                expectations=(_module_expectation(base, "alpha"),),
                remove_module_ids=("alpha",),
                put_modules=(_module("alpha", version_id="alpha.v2"),),
            ),
            depends_on=("delta.first",),
        ),
    )

    with pytest.raises(ChangeSetError) as asymmetric_error:
        _validated(base, asymmetric)
    with pytest.raises(ChangeSetError) as cycle_error:
        _validated(base, cyclic)

    assert asymmetric_error.value.code == "CS_CONFLICT_ASYMMETRIC"
    assert cycle_error.value.code == "CS_DEPENDENCY_CYCLE"


def test_undeclared_overlapping_write_sets_reject_instead_of_last_writer_wins() -> None:
    base = _base_graph()
    first = _delta(
        "delta.first",
        DeltaOperation.REPLACE,
        base,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "beta"),),
            remove_module_ids=("beta",),
            put_modules=(_module("beta", version_id="beta.v2"),),
        ),
    )
    second = _delta(
        "delta.second",
        DeltaOperation.REPLACE,
        base,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "beta"),),
            remove_module_ids=("beta",),
            put_modules=(_module("beta", version_id="beta.v3"),),
        ),
    )

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (first, second))

    assert error.value.code == "CS_WRITESET_AMBIGUOUS"


def _read_write_hazard_deltas(
    base: MethodGraphVersion,
    *,
    relation: str = "independent",
) -> tuple[EvolutionDelta, EvolutionDelta]:
    alpha = next(module for module in base.modules if module.module_id == "alpha")
    read_conflicts = ("delta.write_alpha",) if relation == "conflict" else ()
    write_conflicts = ("delta.read_alpha",) if relation == "conflict" else ()
    write_dependencies = ("delta.read_alpha",) if relation == "dependency" else ()
    reader = _delta(
        "delta.read_alpha",
        DeltaOperation.ADD,
        base,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "alpha"),),
            put_modules=(_module("independent_reader_output"),),
        ),
        conflicts_with=read_conflicts,
    )
    writer = _delta(
        "delta.write_alpha",
        DeltaOperation.REPLACE,
        base,
        category=DeltaCategory.CONTENT,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "alpha"),),
            remove_module_ids=("alpha",),
            put_modules=(
                dataclasses.replace(
                    alpha,
                    version_id="alpha.v2",
                    description="Writer changed alpha content.",
                ),
            ),
        ),
        depends_on=write_dependencies,
        conflicts_with=write_conflicts,
    )
    return reader, writer


def test_independent_read_write_hazard_rejects_as_ambiguous() -> None:
    base = _base_graph()
    reader, writer = _read_write_hazard_deltas(base)

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (reader, writer))

    assert error.value.code == "CS_WRITESET_AMBIGUOUS"
    assert error.value.delta_ids == (reader.delta_id, writer.delta_id)


@pytest.mark.parametrize("relation", ("dependency", "conflict"))
def test_dependency_order_or_symmetric_conflict_authorizes_read_write_hazard(
    relation: str,
) -> None:
    base = _base_graph()
    reader, writer = _read_write_hazard_deltas(base, relation=relation)

    change_set = _validated(base, (writer, reader))

    assert {delta.delta_id for delta in change_set.deltas} == {
        reader.delta_id,
        writer.delta_id,
    }


def test_max_touched_objects_counts_read_union_write() -> None:
    base = _base_graph()
    delta = _delta(
        "delta.read_and_write",
        DeltaOperation.ADD,
        base,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "alpha"),),
            put_modules=(_module("read_and_write_output"),),
        ),
    )

    with pytest.raises(ChangeSetError) as error:
        _validated(
            base,
            (delta,),
            policy=_generous_policy(max_touched_objects=1),
        )

    assert error.value.code == "CS_BUDGET_EXCEEDED"


def test_max_touched_objects_deduplicates_same_object_read_and_write() -> None:
    base = _base_graph()
    alpha = next(module for module in base.modules if module.module_id == "alpha")
    delta = _delta(
        "delta.read_write_same",
        DeltaOperation.REPLACE,
        base,
        category=DeltaCategory.CONTENT,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "alpha"),),
            remove_module_ids=("alpha",),
            put_modules=(
                dataclasses.replace(
                    alpha,
                    version_id="alpha.v2",
                    description="Read and write the same typed module object.",
                ),
            ),
        ),
    )

    change_set = _validated(
        base,
        (delta,),
        policy=_generous_policy(max_touched_objects=1),
    )

    assert change_set.deltas == (delta,)


@pytest.mark.parametrize(
    "budget",
    (
        ChangeSetBudget(max_deltas=1),
        ChangeSetBudget(max_dependencies=0),
        ChangeSetBudget(max_modules_added=0),
        ChangeSetBudget(max_edges_removed=0),
        ChangeSetBudget(max_result_modules=5),
        ChangeSetBudget(max_analysis_steps=1),
    ),
)
def test_hard_budgets_reject_before_selection_or_materialization(
    budget: ChangeSetBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base_graph()
    rules, topology, _, _ = _four_plane_deltas(base)
    deltas = (rules, topology)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("selection or materialization ran after preflight budget failure")

    monkeypatch.setattr(changesets, "resolve_selection", forbidden)
    if hasattr(changesets, "_apply_patch"):
        monkeypatch.setattr(changesets, "_apply_patch", forbidden)

    with pytest.raises(ChangeSetError) as error:
        _validated(base, deltas, policy=budget)

    assert error.value.code == "CS_BUDGET_EXCEEDED"


def test_operation_allowlist_rejects_before_materialization() -> None:
    base = _base_graph()
    delta = _delta("delta.delete", DeltaOperation.DELETE, base)
    policy = ChangeSetPolicy(
        budget=ChangeSetBudget(),
        allowed_operations=(DeltaOperation.ADD,),
    )

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (delta,), policy=policy)

    assert error.value.code == "CS_OPERATION_FORBIDDEN"


def test_base_and_object_hash_preconditions_fail_closed() -> None:
    base = _base_graph()
    delta = _delta("delta.replace", DeltaOperation.REPLACE, base)
    stale_proposal = dataclasses.replace(
        _proposal(base, (delta,)),
        base_graph_hash="0" * 64,
    )
    stale_patch = dataclasses.replace(
        delta.patch,
        expectations=(ObjectExpectation(ObjectKind.MODULE, "beta", "0" * 64),),
    )
    stale_delta = dataclasses.replace(delta, patch=stale_patch)

    with pytest.raises(ChangeSetError) as base_error:
        validate_changeset(base, stale_proposal, _generous_policy())
    with pytest.raises(ChangeSetError) as object_error:
        _validated(base, (stale_delta,))

    assert base_error.value.code == "CS_BASE_MISMATCH"
    assert object_error.value.code == "DELTA_PRECONDITION_MISMATCH"


def test_unknown_target_and_add_collision_reject_before_selection() -> None:
    base = _base_graph()
    unknown = _delta(
        "delta.unknown",
        DeltaOperation.DELETE,
        base,
        patch=GraphPatch(
            expectations=(ObjectExpectation(ObjectKind.MODULE, "missing", "0" * 64),),
            remove_module_ids=("missing",),
        ),
    )
    collision = _delta(
        "delta.collision",
        DeltaOperation.ADD,
        base,
        patch=GraphPatch(put_modules=(_module("beta", version_id="beta.v2"),)),
    )

    with pytest.raises(ChangeSetError) as unknown_error:
        _validated(base, (unknown,))
    with pytest.raises(ChangeSetError) as collision_error:
        _validated(base, (collision,))

    assert unknown_error.value.code == "DELTA_PRECONDITION_MISMATCH"
    assert collision_error.value.code == "DELTA_STRUCTURE_INVALID"


def test_changed_module_contract_requires_a_new_version_id() -> None:
    base = _base_graph()
    beta = next(module for module in base.modules if module.module_id == "beta")
    same_version_change = _delta(
        "delta.same_version",
        DeltaOperation.REPLACE,
        base,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "beta"),),
            remove_module_ids=("beta",),
            put_modules=(
                dataclasses.replace(beta, description="Changed without a new version identity."),
            ),
        ),
    )

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (same_version_change,))

    assert error.value.code == "DELTA_STRUCTURE_INVALID"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("name", "Topology Must Not Rename Content"),
        ("description", "Topology must not rewrite content descriptions."),
        ("content_refs", ("content.beta",)),
        ("applicability_conditions", ("task is applicable",)),
        ("preconditions", ("input is ready",)),
        ("postconditions", ("output is ready",)),
        ("failure_modes", ("input is incomplete",)),
        ("counterexamples", ("counterexample is observed",)),
        ("fallback_refs", ("fallback.beta",)),
        ("rule_refs", ("rule.beta",)),
        ("memory_refs", ("memory.beta",)),
    ),
)
def test_topology_same_id_replace_cannot_change_another_plane_contract(
    field_name: str,
    field_value: object,
) -> None:
    base = _base_graph()
    beta = next(module for module in base.modules if module.module_id == "beta")
    replacement = dataclasses.replace(
        beta,
        version_id="beta.v2",
        **{field_name: field_value},
    )
    delta = _delta(
        f"delta.topology_{field_name}",
        DeltaOperation.REPLACE,
        base,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "beta"),),
            remove_module_ids=("beta",),
            put_modules=(replacement,),
        ),
    )

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (delta,))

    assert error.value.code == "DELTA_STRUCTURE_INVALID"


@pytest.mark.parametrize(
    ("category", "field_updates", "known_refs"),
    (
        (
            DeltaCategory.CONTENT,
            {
                "name": "Beta Content V2",
                "description": "Plane-owned content contract for beta.",
                "content_refs": ("content.beta",),
            },
            ("content.beta",),
        ),
        (
            DeltaCategory.RULES,
            {
                "applicability_conditions": ("task is applicable",),
                "preconditions": ("input is ready",),
                "postconditions": ("output is ready",),
                "failure_modes": ("input is incomplete",),
                "counterexamples": ("counterexample is observed",),
                "fallback_refs": ("gamma",),
                "rule_refs": ("rule.beta",),
            },
            ("rule.beta",),
        ),
        (
            DeltaCategory.MEMORY,
            {"memory_refs": ("memory.beta",)},
            ("memory.beta",),
        ),
    ),
    ids=("content", "rules", "memory"),
)
def test_plane_specific_same_id_replace_accepts_all_and_only_owned_fields(
    category: DeltaCategory,
    field_updates: dict[str, object],
    known_refs: tuple[str, ...],
) -> None:
    base = _base_graph()
    beta = next(module for module in base.modules if module.module_id == "beta")
    replacement = dataclasses.replace(
        beta,
        version_id=f"beta.{category.value}.v2",
        **field_updates,
    )
    delta = _delta(
        f"delta.{category.value}",
        DeltaOperation.REPLACE,
        base,
        category=category,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "beta"),),
            remove_module_ids=("beta",),
            put_modules=(replacement,),
        ),
    )

    change_set = _validated(base, (delta,))
    resolution = resolve_selection(change_set, {delta.delta_id: "accepted"})
    receipt = materialize_selection(
        base,
        change_set,
        resolution,
        _context(*known_refs),
    )

    assert isinstance(receipt, MaterializationReceipt)
    materialized = next(
        module for module in receipt.candidate_graph.modules if module.module_id == "beta"
    )
    for field_name, field_value in field_updates.items():
        assert getattr(materialized, field_name) == field_value


def test_delete_requires_complete_incident_edge_removal_and_rewiring() -> None:
    base = _base_graph()
    incomplete = _delta(
        "delta.delete",
        DeltaOperation.DELETE,
        base,
        patch=GraphPatch(
            expectations=(_module_expectation(base, "beta"),),
            remove_module_ids=("beta",),
            put_edges=(_edge("alpha_gamma", "alpha", "gamma"),),
        ),
    )

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (incomplete,))

    assert error.value.code == "DELTA_STRUCTURE_INVALID"


@pytest.mark.parametrize("operation", (DeltaOperation.MERGE, DeltaOperation.SPLIT))
def test_merge_and_split_require_new_replacement_module_ids(
    operation: DeltaOperation,
) -> None:
    base = _base_graph()
    beta = next(module for module in base.modules if module.module_id == "beta")
    if operation is DeltaOperation.MERGE:
        patch = GraphPatch(
            expectations=(
                _module_expectation(base, "alpha"),
                _module_expectation(base, "beta"),
            ),
            remove_module_ids=("alpha", "beta"),
            put_modules=(dataclasses.replace(beta, version_id="beta.v2"),),
        )
    else:
        patch = GraphPatch(
            expectations=(_module_expectation(base, "beta"),),
            remove_module_ids=("beta",),
            put_modules=(
                dataclasses.replace(beta, version_id="beta.v2"),
                _module("beta_extra"),
            ),
        )
    delta = _delta(f"delta.{operation.value}", operation, base, patch=patch)

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (delta,))

    assert error.value.code == "DELTA_STRUCTURE_INVALID"


@pytest.mark.parametrize("operation", (DeltaOperation.MERGE, DeltaOperation.SPLIT))
def test_merge_and_split_require_complete_incident_edge_rewiring(
    operation: DeltaOperation,
) -> None:
    base = _base_graph()
    valid = _operation_patch(operation, base)
    incomplete = dataclasses.replace(
        valid,
        expectations=tuple(
            item for item in valid.expectations if item.object_id != "beta_gamma"
        ),
        remove_edge_ids=tuple(
            edge_id for edge_id in valid.remove_edge_ids if edge_id != "beta_gamma"
        ),
    )
    delta = _delta(f"delta.{operation.value}", operation, base, patch=incomplete)

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (delta,))

    assert error.value.code == "DELTA_STRUCTURE_INVALID"


def _deprecated_beta_graph() -> MethodGraphVersion:
    base = _base_graph()
    modules = tuple(
        dataclasses.replace(module, deprecated=True)
        if module.module_id == "beta"
        else module
        for module in base.modules
    )
    return dataclasses.replace(base, modules=modules)


@pytest.mark.parametrize("operation", (DeltaOperation.DELETE, DeltaOperation.REPLACE))
def test_delete_and_replace_are_the_only_deprecated_target_remediations(
    operation: DeltaOperation,
) -> None:
    base = _deprecated_beta_graph()
    delta = _delta(f"delta.{operation.value}", operation, base)

    change_set = _validated(base, (delta,))
    resolution = resolve_selection(change_set, {delta.delta_id: "accepted"})
    receipt = materialize_selection(base, change_set, resolution, _context())

    assert isinstance(receipt, MaterializationReceipt)
    assert not any(module.deprecated for module in receipt.candidate_graph.modules)


def test_non_remediation_operation_rejects_deprecated_target() -> None:
    base = _deprecated_beta_graph()
    merge = _delta("delta.merge", DeltaOperation.MERGE, base)

    with pytest.raises(ChangeSetError) as error:
        _validated(base, (merge,))

    assert error.value.code == "DELTA_STRUCTURE_INVALID"


def test_materialization_requires_strict_revalidation_context() -> None:
    with pytest.raises(ChangeSetError) as error:
        MaterializationContext(
            task=TaskContract("task.invalid_context", "Reject permissive revalidation."),
            known_refs=frozenset(),
            diagnostic_policy=DiagnosticPolicy(require_known_refs=False),
        )

    assert error.value.code == "GRAPH_REVALIDATION_CONTEXT_MISSING"


def test_materialization_context_hash_is_canonical_complete_and_sensitive() -> None:
    first = _context("ref.zeta", "ref.alpha")
    second = _context("ref.alpha", "ref.zeta")
    different_task = dataclasses.replace(
        first,
        task=dataclasses.replace(first.task, objective="A different strict objective."),
    )
    different_refs = dataclasses.replace(first, known_refs=first.known_refs | {"ref.extra"})
    different_policy = dataclasses.replace(
        first,
        diagnostic_policy=dataclasses.replace(
            first.diagnostic_policy,
            require_evidence_gate=True,
        ),
    )
    expected_payload = {
        "task": first.task.to_dict(),
        "known_refs": sorted(first.known_refs),
        "diagnostic_policy": dataclasses.asdict(first.diagnostic_policy),
    }

    assert first.to_dict() == expected_payload
    assert first.content_hash == hashlib.sha256(
        canonical_json_bytes(expected_payload)
    ).hexdigest()
    assert first.content_hash == second.content_hash
    assert len({
        first.content_hash,
        different_task.content_hash,
        different_refs.content_hash,
        different_policy.content_hash,
    }) == 4


def test_success_receipt_binds_the_exact_validation_context_hash() -> None:
    base = _base_graph()
    delta = _delta("delta.reorder", DeltaOperation.REORDER, base)
    change_set = _validated(base, (delta,))
    resolution = resolve_selection(change_set, {delta.delta_id: "accepted"})
    context = _context("ref.context_binding")

    receipt = materialize_selection(base, change_set, resolution, context)

    assert isinstance(receipt, MaterializationReceipt)
    assert receipt.validation_context_hash == context.content_hash
    assert receipt.to_dict()["validation_context_hash"] == context.content_hash


def _expected_orphan_candidate(
    base: MethodGraphVersion,
    change_set: changesets.EvolutionChangeSet,
    resolution: changesets.SelectionResolution,
) -> MethodGraphVersion:
    orphan = _module("orphan")
    return dataclasses.replace(
        base,
        version_id=f"{base.graph_id}.candidate.{resolution.selection_hash[:24]}",
        modules=tuple(sorted((*base.modules, orphan), key=lambda item: item.module_id)),
        lineage=GraphLineage(
            parent_hashes=(base.content_hash,),
            supersedes_hashes=(),
            source_refs=tuple(sorted(
                set(base.lineage.source_refs) | set(change_set.provenance.source_refs)
            )),
            created_from_changeset=change_set.change_set_id,
            source_adapter_id=base.lineage.source_adapter_id,
            source_revision=base.lineage.source_revision,
        ),
    )


def test_strict_revalidation_failure_returns_frozen_auditable_failure_receipt() -> None:
    base = _base_graph()
    before = (base.canonical_bytes, base.content_hash, copy.deepcopy(base.to_dict()))
    orphan = _delta(
        "delta.orphan",
        DeltaOperation.ADD,
        base,
        patch=GraphPatch(put_modules=(_module("orphan"),)),
    )
    change_set = _validated(base, (orphan,))
    resolution = resolve_selection(change_set, {orphan.delta_id: "accepted"})
    context = _context()
    expected_candidate = _expected_orphan_candidate(base, change_set, resolution)
    expected_issues = validate_graph(
        expected_candidate,
        task=context.task,
        known_refs=context.known_refs,
        policy=context.diagnostic_policy,
    )
    expected_issues_digest = hashlib.sha256(canonical_json_bytes(
        [issue.to_dict() for issue in expected_issues]
    )).hexdigest()

    receipt = materialize_selection(base, change_set, resolution, context)

    assert isinstance(receipt, changesets.MaterializationFailureReceipt)
    assert receipt.base_graph_hash == base.content_hash
    assert receipt.change_set_hash == change_set.content_hash
    assert receipt.selection_hash == resolution.selection_hash
    assert receipt.closure_delta_ids == (orphan.delta_id,)
    assert receipt.attempted_candidate_hash == expected_candidate.content_hash
    assert receipt.validation_context_hash == context.content_hash
    assert receipt.issues == expected_issues
    assert receipt.issues_digest == expected_issues_digest
    assert receipt.candidate_graph is None
    assert receipt.reevaluation is None
    assert receipt.to_dict() == {
        "base_graph_hash": base.content_hash,
        "change_set_hash": change_set.content_hash,
        "selection_hash": resolution.selection_hash,
        "closure_delta_ids": [orphan.delta_id],
        "attempted_candidate_hash": expected_candidate.content_hash,
        "validation_context_hash": context.content_hash,
        "issues": [issue.to_dict() for issue in expected_issues],
        "issues_digest": expected_issues_digest,
        "candidate_graph": None,
        "reevaluation": None,
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.issues = ()  # type: ignore[misc]
    assert (base.canonical_bytes, base.content_hash, base.to_dict()) == before


def test_invalid_inputs_still_raise_before_materialization_receipt_creation() -> None:
    base = _base_graph()
    delta = _delta("delta.add", DeltaOperation.ADD, base)
    change_set = _validated(base, (delta,))
    resolution = resolve_selection(change_set, {delta.delta_id: "accepted"})

    with pytest.raises(ChangeSetError) as missing_context:
        materialize_selection(base, change_set, resolution, None)
    with pytest.raises(ChangeSetError) as stale_selection:
        materialize_selection(
            base,
            change_set,
            dataclasses.replace(resolution, selection_hash="0" * 64),
            _context(),
        )

    assert missing_context.value.code == "GRAPH_REVALIDATION_CONTEXT_MISSING"
    assert stale_selection.value.code == "CS_CLOSURE_INVALID"


def _add_only_delta(
    base: MethodGraphVersion,
    delta_id: str,
    module_id: str,
    *,
    depends_on: tuple[str, ...] = (),
) -> EvolutionDelta:
    return _delta(
        delta_id,
        DeltaOperation.ADD,
        base,
        patch=GraphPatch(put_modules=(_module(module_id),)),
        depends_on=depends_on,
        evaluator_requirement_refs=(),
    )


def test_dependency_closure_properties_are_deterministic_for_generated_dag() -> None:
    base = _base_graph()
    deltas = tuple(
        _add_only_delta(
            base,
            f"delta.generated_{index:02d}",
            f"generated_{index:02d}",
            depends_on=(f"delta.generated_{index - 1:02d}",) if index else (),
        )
        for index in range(12)
    )
    change_set = _validated(
        base,
        tuple(reversed(deltas)),
        proposal_id="proposal.generated_dag",
    )
    dispositions = {delta.delta_id: "accepted" for delta in reversed(deltas)}

    first = resolve_selection(change_set, dispositions)
    second = resolve_selection(change_set, dict(reversed(tuple(dispositions.items()))))
    expected = tuple(delta.delta_id for delta in deltas)

    assert first == second
    assert first.closure_delta_ids == expected
    assert first.application_order == expected
    assert set(first.closure_delta_ids) >= {
        dependency
        for delta in deltas
        for dependency in delta.depends_on
    }


def test_compatible_selection_union_equals_union_of_dependency_closures() -> None:
    base = _base_graph()
    root = _add_only_delta(base, "delta.root", "module_root")
    left = _add_only_delta(
        base,
        "delta.left",
        "module_left",
        depends_on=(root.delta_id,),
    )
    right = _add_only_delta(
        base,
        "delta.right",
        "module_right",
        depends_on=(root.delta_id,),
    )
    change_set = _validated(base, (right, root, left), proposal_id="proposal.union")
    left_resolution = resolve_selection(
        change_set,
        {root.delta_id: "accepted", left.delta_id: "accepted", right.delta_id: "rejected"},
    )
    right_resolution = resolve_selection(
        change_set,
        {root.delta_id: "accepted", left.delta_id: "rejected", right.delta_id: "accepted"},
    )
    union_resolution = resolve_selection(
        change_set,
        {root.delta_id: "accepted", left.delta_id: "accepted", right.delta_id: "accepted"},
    )

    assert set(union_resolution.closure_delta_ids) == (
        set(left_resolution.closure_delta_ids) | set(right_resolution.closure_delta_ids)
    )


def test_changeset_core_is_read_only_and_exposes_no_apply_or_promotion_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base_graph()
    delta = _delta("delta.reorder", DeltaOperation.REORDER, base)
    proposal = _proposal(base, (delta,))
    before = (base.canonical_bytes, base.content_hash, copy.deepcopy(base.to_dict()))

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Phase 3 attempted filesystem, runtime, process, or network access")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(importlib, "import_module", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    change_set = validate_changeset(base, proposal, _generous_policy())
    resolution = resolve_selection(change_set, {delta.delta_id: "accepted"})
    receipt = materialize_selection(base, change_set, resolution, _context())

    assert isinstance(receipt, MaterializationReceipt)
    assert (base.canonical_bytes, base.content_hash, base.to_dict()) == before
    for forbidden_name in (
        "apply_changeset",
        "promote",
        "canonicalize",
        "move_pointer",
        "run_evaluator",
        "execute",
    ):
        assert not hasattr(changesets, forbidden_name)


def test_changeset_source_imports_only_static_standard_library_and_core_contracts() -> None:
    source = Path(changesets.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
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

    assert imported <= {
        "__future__",
        "dataclasses",
        "diagnostics",
        "enum",
        "hashlib",
        "heapq",
        "json",
        "re",
        "typing",
        "workflow_ir",
    }


@pytest.mark.parametrize(
    ("current", "target"),
    (
        (ChangeSetStatus.PROPOSED, ChangeSetStatus.VALIDATED),
        (ChangeSetStatus.PROPOSED, ChangeSetStatus.INVALID),
        (ChangeSetStatus.VALIDATED, ChangeSetStatus.UNDER_SELECTION),
        (ChangeSetStatus.UNDER_SELECTION, ChangeSetStatus.CLOSURE_MATERIALIZED),
        (ChangeSetStatus.CLOSURE_MATERIALIZED, ChangeSetStatus.RESOLVED),
        (ChangeSetStatus.PROPOSED, ChangeSetStatus.SUPERSEDED),
        (ChangeSetStatus.VALIDATED, ChangeSetStatus.SUPERSEDED),
        (ChangeSetStatus.UNDER_SELECTION, ChangeSetStatus.SUPERSEDED),
        (ChangeSetStatus.CLOSURE_MATERIALIZED, ChangeSetStatus.SUPERSEDED),
    ),
)
def test_changeset_lifecycle_allows_only_frozen_forward_transitions(
    current: ChangeSetStatus,
    target: ChangeSetStatus,
) -> None:
    assert validate_changeset_transition(current, target) is target


@pytest.mark.parametrize(
    "terminal",
    (ChangeSetStatus.INVALID, ChangeSetStatus.RESOLVED, ChangeSetStatus.SUPERSEDED),
)
@pytest.mark.parametrize("target", tuple(ChangeSetStatus))
def test_changeset_terminal_states_reject_every_transition(
    terminal: ChangeSetStatus,
    target: ChangeSetStatus,
) -> None:
    with pytest.raises(ChangeSetError, match="CS_CONSISTENCY_INVALID"):
        validate_changeset_transition(terminal, target)


@pytest.mark.parametrize(
    ("current", "target"),
    (
        (DeltaStatus.PROPOSED, DeltaStatus.STRUCTURALLY_VALID),
        (DeltaStatus.PROPOSED, DeltaStatus.INVALID),
        (DeltaStatus.STRUCTURALLY_VALID, DeltaStatus.ACCEPTED),
        (DeltaStatus.STRUCTURALLY_VALID, DeltaStatus.REJECTED),
        (DeltaStatus.STRUCTURALLY_VALID, DeltaStatus.DEFERRED),
        (DeltaStatus.ACCEPTED, DeltaStatus.DEPENDENCY_CLOSED),
        (DeltaStatus.ACCEPTED, DeltaStatus.CONFLICT_BLOCKED),
        (DeltaStatus.DEPENDENCY_CLOSED, DeltaStatus.CANDIDATE_MATERIALIZED),
        (DeltaStatus.CANDIDATE_MATERIALIZED, DeltaStatus.CANONICALIZED),
    ),
)
def test_delta_lifecycle_allows_only_frozen_forward_transitions(
    current: DeltaStatus,
    target: DeltaStatus,
) -> None:
    assert validate_delta_transition(current, target) is target


@pytest.mark.parametrize(
    "current",
    (
        DeltaStatus.PROPOSED,
        DeltaStatus.STRUCTURALLY_VALID,
        DeltaStatus.ACCEPTED,
        DeltaStatus.DEPENDENCY_CLOSED,
        DeltaStatus.CANDIDATE_MATERIALIZED,
    ),
)
def test_delta_nonterminal_states_may_be_explicitly_superseded(
    current: DeltaStatus,
) -> None:
    assert validate_delta_transition(current, DeltaStatus.SUPERSEDED) is DeltaStatus.SUPERSEDED


@pytest.mark.parametrize(
    "terminal",
    (
        DeltaStatus.INVALID,
        DeltaStatus.REJECTED,
        DeltaStatus.DEFERRED,
        DeltaStatus.CONFLICT_BLOCKED,
        DeltaStatus.CANONICALIZED,
        DeltaStatus.SUPERSEDED,
    ),
)
@pytest.mark.parametrize("target", tuple(DeltaStatus))
def test_delta_terminal_states_reject_every_transition(
    terminal: DeltaStatus,
    target: DeltaStatus,
) -> None:
    with pytest.raises(ChangeSetError, match="DELTA_STRUCTURE_INVALID"):
        validate_delta_transition(terminal, target)
