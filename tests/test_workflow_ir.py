from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

import mycevo.adapter_fitness as adapter_fitness
import mycevo.workflow_ir as workflow_ir
from mycevo.adapter_fitness import assess_adapter_schema_freeze, normalize_adapter_fixture
from mycevo.workflow_ir import (
    ControlKind,
    ControlSpec,
    EdgeKind,
    GraphLineage,
    IRValidationError,
    MethodEdge,
    MethodGraphVersion,
    MethodModule,
    ModuleKind,
    Permission,
    PortSpec,
    SideEffect,
    SideEffectClass,
    TaskContract,
    canonical_json_bytes,
    canonical_sha256,
    check_task_fitness,
    graph_from_json_bytes,
)


FIXTURES = Path(__file__).parent / "fixtures" / "adapters"
PROJECT_ROOT = Path(__file__).parent.parent
SCHEMA_DIR = PROJECT_ROOT / "src" / "mycevo" / "schemas"
SCHEMA_FILES = {
    "module": SCHEMA_DIR / "module-contract.v1.schema.json",
    "graph": SCHEMA_DIR / "method-graph.v1.schema.json",
    "task": SCHEMA_DIR / "task-contract.v1.schema.json",
}
ADAPTER_FIXTURES = (
    FIXTURES / "researchloop_paper_lifecycle_v1.yaml",
    FIXTURES / "smartmoney_read_only_review_v1.yaml",
)

GRAPH_KEYS = {
    "schema",
    "graph_id",
    "version_id",
    "purpose",
    "modules",
    "edges",
    "entry_module_ids",
    "exit_module_ids",
    "lineage",
    "task_contract_ref",
    "applicability_conditions",
}
MODULE_KEYS = {
    "schema",
    "module_id",
    "version_id",
    "kind",
    "name",
    "description",
    "inputs",
    "outputs",
    "prerequisites",
    "evidence_refs",
    "side_effects",
    "applicability_conditions",
    "preconditions",
    "postconditions",
    "required_evidence",
    "produced_evidence",
    "side_effect_class",
    "permission_requirements",
    "failure_modes",
    "counterexamples",
    "fallback_refs",
    "cost",
    "risk",
    "reversibility",
    "content_refs",
    "rule_refs",
    "memory_refs",
    "control",
    "contract_ref",
    "deprecated",
}
EDGE_KEYS = {
    "edge_id",
    "kind",
    "source_module",
    "target_module",
    "source_port",
    "target_port",
    "label",
    "condition_ref",
    "branch_label",
    "join_key",
    "max_iterations",
}
TASK_KEYS = {
    "schema",
    "task_id",
    "objective",
    "required_inputs",
    "required_outputs",
    "constraints",
    "allowed_side_effects",
    "max_modules",
    "max_edges",
}


def _module(
    module_id: str,
    *,
    kind: ModuleKind = ModuleKind.TRANSFORM,
    inputs: tuple[PortSpec, ...] = (),
    outputs: tuple[PortSpec, ...] = (),
    side_effects: tuple[SideEffect, ...] = (),
    permission_requirements: tuple[Permission, ...] = (),
    control: ControlSpec | None = None,
) -> MethodModule:
    return MethodModule(
        module_id=module_id,
        version_id=f"{module_id}.v1",
        kind=kind,
        name=module_id.replace("_", " ").title(),
        description=f"Static contract for {module_id}.",
        inputs=inputs,
        outputs=outputs,
        side_effects=side_effects,
        permission_requirements=permission_requirements,
        control=control,
    )


def _sample_graph(*, reversed_order: bool = False) -> MethodGraphVersion:
    source = _module(
        "source",
        kind=ModuleKind.INPUT,
        inputs=(PortSpec("request", "task_ref"),),
        outputs=(PortSpec("artifact", "artifact_ref"),),
        side_effects=(SideEffect.READ_WORKSPACE,),
        permission_requirements=(Permission.WORKSPACE_READ,),
    )
    sink = _module(
        "sink",
        kind=ModuleKind.OUTPUT,
        inputs=(PortSpec("artifact", "artifact_ref"),),
        outputs=(PortSpec("result", "result_ref"),),
    )
    edge = MethodEdge(
        edge_id="source_to_sink",
        kind=EdgeKind.DATA,
        source_module="source",
        target_module="sink",
        source_port="artifact",
        target_port="artifact",
    )
    modules = (source, sink) if reversed_order else (sink, source)
    return MethodGraphVersion(
        graph_id="graph.sample",
        version_id="graph.sample.v1",
        purpose="Exercise the portable static method-graph contract.",
        modules=modules,
        edges=(edge,),
        entry_module_ids=("source",),
        exit_module_ids=("sink",),
        lineage=GraphLineage(
            parent_hashes=("a" * 64,),
            supersedes_hashes=("b" * 64,),
            source_refs=("fixture:sample",),
            created_from_changeset="changeset.sample",
        ),
    )


def _control_graph(
    control: ControlSpec | None,
    edges: tuple[MethodEdge, ...],
) -> MethodGraphVersion:
    module_ids = sorted(
        {module_id for edge in edges for module_id in (edge.source_module, edge.target_module)}
    )
    modules = tuple(
        _module(module_id, control=control if module_id == "router" else None)
        for module_id in module_ids
    )
    exits = tuple(module_id for module_id in module_ids if module_id != "router") or ("router",)
    return MethodGraphVersion(
        graph_id="graph.control",
        version_id="graph.control.v1",
        purpose="Validate graph-level control invariants.",
        modules=modules,
        edges=edges,
        entry_module_ids=("router",),
        exit_module_ids=exits,
    )


def _load_adapter_fixture(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_json_schema(name: str) -> dict[str, Any]:
    value = json.loads(SCHEMA_FILES[name].read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _adapter_memory_refs(values: list[dict[str, Any] | str]) -> tuple[str, ...]:
    return tuple(item["ref"] if isinstance(item, dict) else item for item in values)


@pytest.mark.parametrize("schema_name", tuple(SCHEMA_FILES))
def test_packaged_json_schema_files_are_parseable(schema_name: str) -> None:
    schema = _load_json_schema(schema_name)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert isinstance(schema["properties"], dict)
    assert set(schema["required"]) == set(schema["properties"])


def test_schema_required_fields_match_python_to_dict_surfaces() -> None:
    examples = {
        "module": _module("schema_module"),
        "graph": _sample_graph(),
        "task": TaskContract("task.schema", "Verify the task schema surface."),
    }
    expected_keys = {
        "module": MODULE_KEYS,
        "graph": GRAPH_KEYS,
        "task": TASK_KEYS,
    }

    for schema_name, example in examples.items():
        schema_required = set(_load_json_schema(schema_name)["required"])
        rendered_keys = set(example.to_dict())
        assert schema_required == rendered_keys == expected_keys[schema_name]


def test_schema_enums_match_python_edge_and_module_kinds() -> None:
    module_schema = _load_json_schema("module")
    graph_schema = _load_json_schema("graph")

    assert module_schema["properties"]["kind"]["enum"] == [item.value for item in ModuleKind]
    assert module_schema["properties"]["permission_requirements"]["items"]["enum"] == [
        item.value for item in Permission
    ]
    assert module_schema["properties"]["side_effects"]["items"]["enum"] == [
        item.value for item in SideEffect
    ]
    assert module_schema["properties"]["side_effect_class"]["enum"] == [
        item.value for item in SideEffectClass
    ]
    assert graph_schema["$defs"]["edge"]["properties"]["kind"]["enum"] == [
        item.value for item in EdgeKind
    ]


@pytest.mark.parametrize(("field", "value"), (("required", 1), ("required", "yes"), ("multiple", 0), ("multiple", None)))
def test_port_boolean_fields_reject_non_booleans(field: str, value: object) -> None:
    values = {"name": "artifact", "data_type": "artifact_ref", field: value}
    with pytest.raises(IRValidationError, match="bool"):
        PortSpec(**values)


@pytest.mark.parametrize("value", (0, 1, "false", None))
def test_module_deprecated_rejects_non_booleans(value: object) -> None:
    with pytest.raises(IRValidationError, match="deprecated|bool"):
        dataclasses.replace(_module("deprecated_contract"), deprecated=value)


@pytest.mark.parametrize("value", ("", "x" * 4001))
def test_task_constraints_reject_empty_or_overlong_text(value: str) -> None:
    with pytest.raises(IRValidationError, match="constraints|non-empty|4000"):
        TaskContract("task.constraints", "Validate constraints.", constraints=(value,))


@pytest.mark.parametrize("value", ("", "x" * 513))
def test_lineage_source_refs_reject_empty_or_overlong_text(value: str) -> None:
    with pytest.raises(IRValidationError, match="source_refs|non-empty|512"):
        GraphLineage(source_refs=(value,))


@pytest.mark.parametrize("value", ("", "x" * 513))
def test_module_evidence_refs_reject_empty_or_overlong_text(value: str) -> None:
    with pytest.raises(IRValidationError, match="evidence_refs|non-empty|512"):
        dataclasses.replace(_module("evidence_contract"), evidence_refs=(value,))


def test_sample_contracts_validate_with_draft_202012_and_registry() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    schemas = {name: _load_json_schema(name) for name in SCHEMA_FILES}
    registry = referencing.Registry().with_resources(
        [
            (schema["$id"], referencing.Resource.from_contents(schema))
            for schema in schemas.values()
        ]
    )
    instances = {
        "module": _module("schema_validated").to_dict(),
        "graph": _sample_graph().to_dict(),
        "task": TaskContract(
            "task.schema_validated",
            "Validate a representative task contract.",
            constraints=("Static validation only.",),
        ).to_dict(),
    }

    for name, instance in instances.items():
        jsonschema.Draft202012Validator.check_schema(schemas[name])
        validator = jsonschema.Draft202012Validator(schemas[name], registry=registry)
        assert list(validator.iter_errors(instance)) == []


def test_pyproject_packages_json_schemas() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section_match = re.search(
        r"(?ms)^\[tool\.setuptools\.package-data\]\s*$\n(?P<body>.*?)(?=^\[|\Z)",
        pyproject,
    )
    assert section_match is not None
    package_match = re.search(
        r"(?ms)^mycevo\s*=\s*\[(?P<items>.*?)\]\s*$",
        section_match.group("body"),
    )
    assert package_match is not None
    package_patterns = json.loads(f"[{package_match.group('items')}]")

    assert "schemas/*.json" in package_patterns


def test_canonical_json_bytes_and_hash_are_stable() -> None:
    graph = _sample_graph()
    reordered = _sample_graph(reversed_order=True)

    assert graph.canonical_bytes == reordered.canonical_bytes
    assert graph.content_hash == reordered.content_hash
    assert graph.content_hash == hashlib.sha256(graph.canonical_bytes).hexdigest()
    assert canonical_sha256(graph) == graph.content_hash
    assert canonical_json_bytes({"z": 1, "a": "é"}) == b'{"a":"\xc3\xa9","z":1}'
    assert b"\n" not in graph.canonical_bytes


def test_canonical_yaml_equivalent_identity() -> None:
    graph = _sample_graph()
    presentation = yaml.safe_dump(graph.to_dict(), allow_unicode=True, sort_keys=False)
    loaded = yaml.safe_load(presentation)
    loaded["modules"] = list(reversed(loaded["modules"]))
    loaded["lineage"]["source_refs"] = list(reversed(loaded["lineage"]["source_refs"]))

    equivalent = MethodGraphVersion.from_dict(loaded)

    assert equivalent.canonical_bytes == graph.canonical_bytes
    assert equivalent.content_hash == graph.content_hash


def test_canonical_round_trip_is_immutable() -> None:
    graph = _sample_graph()
    restored = graph_from_json_bytes(graph.canonical_bytes)

    assert restored == graph
    assert restored.canonical_bytes == graph.canonical_bytes
    with pytest.raises(dataclasses.FrozenInstanceError):
        restored.graph_id = "graph.changed"  # type: ignore[misc]
    mutable_view = restored.to_dict()
    mutable_view["modules"][0]["name"] = "changed"
    assert restored.canonical_bytes == graph.canonical_bytes


@pytest.mark.parametrize("duplicate_depth", ("root", "module", "port"))
def test_graph_json_rejects_duplicate_keys_at_any_depth(duplicate_depth: str) -> None:
    payload = _sample_graph().canonical_bytes
    if duplicate_depth == "root":
        payload = b'{"graph_id":"shadow",' + payload[1:]
    elif duplicate_depth == "module":
        payload = payload.replace(b'"modules":[{', b'"modules":[{"name":"shadow",', 1)
    else:
        payload = payload.replace(b'"inputs":[{', b'"inputs":[{"name":"shadow",', 1)

    with pytest.raises(IRValidationError, match="duplicate"):
        graph_from_json_bytes(payload)


@pytest.mark.parametrize("value", [1.5, float("nan"), {"bad": object()}, {1: "non-string-key"}])
def test_canonical_rejects_ambiguous_values(value: object) -> None:
    with pytest.raises(IRValidationError):
        canonical_json_bytes(value)


def test_typed_data_edges_validate_ports_and_types() -> None:
    producer = _module("producer", outputs=(PortSpec("out", "artifact_ref"),))
    consumer = _module("consumer", inputs=(PortSpec("in", "artifact_ref"),))
    valid = MethodEdge("data_edge", EdgeKind.DATA, "producer", "consumer", "out", "in")
    MethodGraphVersion(
        graph_id="graph.typed",
        version_id="graph.typed.v1",
        purpose="Validate typed data edges.",
        modules=(producer, consumer),
        edges=(valid,),
        entry_module_ids=("producer",),
        exit_module_ids=("consumer",),
    )

    with pytest.raises(IRValidationError, match="source_port and target_port"):
        MethodEdge("missing_ports", EdgeKind.DATA, "producer", "consumer")
    with pytest.raises(IRValidationError, match="unknown port"):
        MethodGraphVersion(
            graph_id="graph.bad_port",
            version_id="graph.bad_port.v1",
            purpose="Reject an unknown data port.",
            modules=(producer, consumer),
            edges=(MethodEdge("bad_port", EdgeKind.DATA, "producer", "consumer", "missing", "in"),),
            entry_module_ids=("producer",),
            exit_module_ids=("consumer",),
        )

    mismatched = _module("mismatched", inputs=(PortSpec("in", "other_ref"),))
    mismatch_edge = MethodEdge("mismatch", EdgeKind.DATA, "producer", "mismatched", "out", "in")
    with pytest.raises(IRValidationError, match="type"):
        MethodGraphVersion(
            graph_id="graph.bad_type",
            version_id="graph.bad_type.v1",
            purpose="Reject a data type mismatch.",
            modules=(producer, mismatched),
            edges=(mismatch_edge,),
            entry_module_ids=("producer",),
            exit_module_ids=("mismatched",),
        )


def test_typed_edges_reject_unknown_endpoints_ports_and_illegal_self_edges() -> None:
    module = _module("only")
    with pytest.raises(IRValidationError, match="unknown module"):
        MethodGraphVersion(
            graph_id="graph.unknown",
            version_id="graph.unknown.v1",
            purpose="Reject an unknown module endpoint.",
            modules=(module,),
            edges=(MethodEdge("missing", EdgeKind.SEQUENCE, "only", "absent"),),
            entry_module_ids=("only",),
            exit_module_ids=("only",),
        )
    with pytest.raises(IRValidationError, match="ports are valid only"):
        MethodEdge("control_ports", EdgeKind.SEQUENCE, "only", "other", "out", "in")
    with pytest.raises(IRValidationError, match="only loop edges"):
        MethodEdge("self_control", EdgeKind.SEQUENCE, "only", "only")


def test_lineage_constraints_and_canonical_order() -> None:
    lineage = GraphLineage(parent_hashes=("b" * 64, "a" * 64), source_refs=("z", "a"))
    assert lineage.to_dict()["parent_hashes"] == ["a" * 64, "b" * 64]
    assert lineage.to_dict()["source_refs"] == ["a", "z"]
    with pytest.raises(IRValidationError, match="lowercase SHA-256"):
        GraphLineage(parent_hashes=("A" * 64,))
    with pytest.raises(IRValidationError, match="unique"):
        GraphLineage(parent_hashes=("a" * 64, "a" * 64))


@pytest.mark.parametrize(
    ("factory", "payload"),
    [
        (MethodModule.from_dict, {**_module("module").to_dict(), "schema": "mycevo.method_module.v2"}),
        (MethodGraphVersion.from_dict, {**_sample_graph().to_dict(), "schema": "mycevo.method_graph.v2"}),
        (
            TaskContract.from_dict,
            {**TaskContract("task.sample", "Test schema rejection.").to_dict(), "schema": "mycevo.task_contract.v2"},
        ),
    ],
)
def test_version_rejection(factory: Any, payload: dict[str, Any]) -> None:
    with pytest.raises(IRValidationError, match="unsupported"):
        factory(payload)


@pytest.mark.parametrize(
    ("factory", "payload"),
    [
        (MethodModule.from_dict, {key: value for key, value in _module("module").to_dict().items() if key != "schema"}),
        (MethodGraphVersion.from_dict, {key: value for key, value in _sample_graph().to_dict().items() if key != "schema"}),
        (
            TaskContract.from_dict,
            {key: value for key, value in TaskContract("task.sample", "Require explicit schema.").to_dict().items() if key != "schema"},
        ),
    ],
)
def test_missing_schema_version_rejects(factory: Any, payload: dict[str, Any]) -> None:
    with pytest.raises(IRValidationError, match="schema"):
        factory(payload)


@pytest.mark.parametrize("field", ["status", "eligible", "canonical_referenced", "promotion_allowed"])
def test_lifecycle_status_isolation(field: str) -> None:
    graph = _sample_graph().to_dict()
    graph[field] = "candidate"
    with pytest.raises(IRValidationError, match="unsupported fields"):
        MethodGraphVersion.from_dict(graph)

    module = _module("isolated").to_dict()
    module[field] = "validated"
    with pytest.raises(IRValidationError, match="unsupported fields"):
        MethodModule.from_dict(module)


@pytest.mark.parametrize("domain_field", ["ticker", "symbol", "venue", "paper_title", "broker", "position"])
def test_domain_specific_fields_reject_from_core_schema(domain_field: str) -> None:
    graph = _sample_graph().to_dict()
    graph[domain_field] = "domain payload"
    with pytest.raises(IRValidationError, match="unsupported fields"):
        MethodGraphVersion.from_dict(graph)

    module = _module("portable").to_dict()
    module[domain_field] = "domain payload"
    with pytest.raises(IRValidationError, match="unsupported fields"):
        MethodModule.from_dict(module)


@pytest.mark.parametrize(
    "control",
    [
        ControlSpec(ControlKind.CONDITION, branch_labels=("pass", "fail")),
        ControlSpec(ControlKind.PARALLEL, branch_labels=("left", "right")),
        ControlSpec(ControlKind.LOOP, max_iterations=3),
        ControlSpec(ControlKind.FALLBACK, branch_labels=("primary", "fallback")),
    ],
)
def test_static_semantics_bounded_control_forms_round_trip(control: ControlSpec) -> None:
    assert ControlSpec.from_dict(control.to_dict()) == control


def test_static_semantics_reject_unbounded_or_mistyped_controls() -> None:
    with pytest.raises(IRValidationError, match="max_iterations"):
        ControlSpec(ControlKind.LOOP)
    with pytest.raises(IRValidationError, match="1 to 10000"):
        ControlSpec(ControlKind.LOOP, max_iterations=10_001)
    with pytest.raises(IRValidationError, match="branch_labels"):
        ControlSpec(ControlKind.CONDITION)
    with pytest.raises(IRValidationError, match="only for loop"):
        ControlSpec(ControlKind.PARALLEL, branch_labels=("left", "right"), max_iterations=2)


def test_static_semantics_loop_edges_are_intrinsically_bounded() -> None:
    repeat = _module("repeat", control=ControlSpec(ControlKind.LOOP, max_iterations=2))
    with pytest.raises(IRValidationError, match="max_iterations"):
        MethodEdge("repeat_loop", EdgeKind.LOOP, "repeat", "repeat")

    edge = MethodEdge("repeat_loop", EdgeKind.LOOP, "repeat", "repeat", max_iterations=2)
    MethodGraphVersion(
        graph_id="graph.bounded",
        version_id="graph.bounded.v1",
        purpose="Represent a statically bounded loop.",
        modules=(repeat,),
        edges=(edge,),
        entry_module_ids=("repeat",),
        exit_module_ids=("repeat",),
    )


def test_graph_loop_control_and_edge_bounds_must_match() -> None:
    valid_edge = MethodEdge(
        "repeat_loop",
        EdgeKind.LOOP,
        "router",
        "router",
        max_iterations=2,
    )
    _control_graph(ControlSpec(ControlKind.LOOP, max_iterations=2), (valid_edge,))

    with pytest.raises(IRValidationError, match="loop.*max_iterations|bound"):
        _control_graph(ControlSpec(ControlKind.LOOP, max_iterations=3), (valid_edge,))


def test_graph_condition_branch_labels_must_match_outgoing_edges_exactly() -> None:
    valid_edges = (
        MethodEdge(
            "route_pass",
            EdgeKind.CONDITIONAL,
            "router",
            "accepted",
            condition_ref="condition.pass",
            branch_label="pass",
        ),
        MethodEdge(
            "route_fail",
            EdgeKind.FALLBACK,
            "router",
            "rejected",
            condition_ref="condition.fail",
            branch_label="fail",
        ),
    )
    _control_graph(
        ControlSpec(ControlKind.CONDITION, branch_labels=("pass", "fail")),
        valid_edges,
    )

    mismatched_edges = (
        valid_edges[0],
        dataclasses.replace(valid_edges[1], branch_label="review"),
    )
    with pytest.raises(IRValidationError, match="branch_labels|branch label"):
        _control_graph(
            ControlSpec(ControlKind.CONDITION, branch_labels=("pass", "fail")),
            mismatched_edges,
        )

    with pytest.raises(IRValidationError, match="branch_label|branch label"):
        missing_label = (dataclasses.replace(valid_edges[0], branch_label=None),)
        _control_graph(
            ControlSpec(ControlKind.CONDITION, branch_labels=("pass",)),
            missing_label,
        )


def test_graph_parallel_control_requires_distinct_branches_and_one_join_key() -> None:
    valid_edges = (
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
    )
    _control_graph(
        ControlSpec(ControlKind.PARALLEL, branch_labels=("left", "right")),
        valid_edges,
    )

    invalid_cases = (
        (valid_edges[0],),
        (valid_edges[0], dataclasses.replace(valid_edges[1], branch_label="left")),
        (valid_edges[0], dataclasses.replace(valid_edges[1], join_key="other_join")),
    )
    for invalid_edges in invalid_cases:
        with pytest.raises(IRValidationError, match="parallel|branch|join_key"):
            _control_graph(
                ControlSpec(ControlKind.PARALLEL, branch_labels=("left", "right")),
                invalid_edges,
            )


def test_graph_control_edges_require_matching_module_control() -> None:
    edge = MethodEdge(
        "route_pass",
        EdgeKind.CONDITIONAL,
        "router",
        "accepted",
        condition_ref="condition.pass",
        branch_label="pass",
    )
    with pytest.raises(IRValidationError, match="control"):
        _control_graph(None, (edge,))

    with pytest.raises(IRValidationError, match="control|kind"):
        _control_graph(
            ControlSpec(ControlKind.PARALLEL, branch_labels=("pass", "other")),
            (edge,),
        )


def test_static_semantics_never_execute_import_process_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("static IR attempted a runtime action")

    import importlib
    import socket
    import subprocess

    monkeypatch.setattr(importlib, "import_module", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    graph = _sample_graph()
    restored = graph_from_json_bytes(graph.canonical_bytes)
    task = TaskContract(
        "task.static",
        "Validate static behavior.",
        required_inputs=(PortSpec("request", "task_ref"),),
        required_outputs=(PortSpec("result", "result_ref"),),
        allowed_side_effects=(SideEffect.READ_WORKSPACE,),
    )
    assert check_task_fitness(restored, task) == ()


def test_static_semantics_source_has_no_dynamic_runtime_or_private_dependency() -> None:
    sources = (
        (workflow_ir, {"__future__", "dataclasses", "enum", "hashlib", "json", "re", "typing"}),
        (adapter_fitness, {"__future__", "dataclasses", "typing", "workflow_ir"}),
    )
    for module, allowed_imports in sources:
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
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
        assert imported <= allowed_imports

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
        assert not direct_calls & {"eval", "exec", "compile", "__import__"}
        assert not qualified_calls & {
            "importlib.import_module",
            "os.system",
            "subprocess.Popen",
            "subprocess.run",
            "urllib.urlopen",
        }
        assert not imported & {"openai", "anthropic", "langgraph", "requests", "socket", "subprocess"}


def test_task_fitness_returns_stable_budget_type_and_side_effect_gaps() -> None:
    graph = _sample_graph()
    constrained = TaskContract(
        "task.constrained",
        "Exercise every static task fitness gate.",
        required_inputs=(PortSpec("missing", "missing_input"),),
        required_outputs=(PortSpec("missing", "missing_output"),),
        allowed_side_effects=(),
        max_modules=1,
        max_edges=0,
    )
    assert check_task_fitness(graph, constrained) == (
        "ADAPTER_EDGE_BUDGET_EXCEEDED",
        "ADAPTER_MODULE_BUDGET_EXCEEDED",
        "ADAPTER_REQUIRED_INPUT_UNBOUND",
        "ADAPTER_REQUIRED_OUTPUT_UNBOUND",
        "ADAPTER_SIDE_EFFECT_UNAUTHORIZED",
    )


@pytest.mark.parametrize(
    ("side_effect_class", "permissions", "side_effects"),
    (
        (SideEffectClass.CANDIDATE_ARTIFACT_WRITE, (), (SideEffect.WRITE_WORKSPACE,)),
        (
            SideEffectClass.CANDIDATE_ARTIFACT_WRITE,
            (Permission.CANDIDATE_WRITE,),
            (),
        ),
        (SideEffectClass.AUDIT_APPEND_ONLY, (), (SideEffect.WRITE_WORKSPACE,)),
        (SideEffectClass.CANDIDATE_MEMORY_WRITE, (), (SideEffect.WRITE_WORKSPACE,)),
        (
            SideEffectClass.DECISION_RECORD_APPEND_ONLY,
            (),
            (SideEffect.WRITE_WORKSPACE, SideEffect.HUMAN_INTERACTION),
        ),
        (
            SideEffectClass.NONE,
            (Permission.CANDIDATE_WRITE,),
            (SideEffect.WRITE_WORKSPACE,),
        ),
    ),
)
def test_module_permission_side_effect_class_matrix_rejects_contradictions(
    side_effect_class: SideEffectClass,
    permissions: tuple[Permission, ...],
    side_effects: tuple[SideEffect, ...],
) -> None:
    with pytest.raises(IRValidationError, match="permission|side.effect|class|safety contract"):
        dataclasses.replace(
            _module("governed"),
            side_effect_class=side_effect_class,
            permission_requirements=permissions,
            side_effects=side_effects,
        )


def test_candidate_memory_edge_without_governed_source_cannot_pass_fitness() -> None:
    ungoverned = _module("memory_writer", kind=ModuleKind.MEMORY)
    sink = _module("sink", kind=ModuleKind.OUTPUT)
    edge = MethodEdge(
        "write_memory",
        EdgeKind.WRITES_CANDIDATE_MEMORY,
        "memory_writer",
        "sink",
    )
    with pytest.raises(IRValidationError, match="candidate.memory|permission|side.effect"):
        MethodGraphVersion(
            graph_id="graph.ungoverned_memory",
            version_id="graph.ungoverned_memory.v1",
            purpose="Reject an ungoverned candidate-memory write edge.",
            modules=(ungoverned, sink),
            edges=(edge,),
            entry_module_ids=("memory_writer",),
            exit_module_ids=("sink",),
        )

    governed = dataclasses.replace(
        ungoverned,
        side_effect_class=SideEffectClass.CANDIDATE_MEMORY_WRITE,
        permission_requirements=(Permission.CANDIDATE_WRITE, Permission.CANDIDATE_MEMORY_WRITE),
        side_effects=(SideEffect.WRITE_WORKSPACE,),
    )
    graph = MethodGraphVersion(
        graph_id="graph.governed_memory",
        version_id="graph.governed_memory.v1",
        purpose="Expose candidate-memory writes to task fitness.",
        modules=(governed, sink),
        edges=(edge,),
        entry_module_ids=("memory_writer",),
        exit_module_ids=("sink",),
    )
    task = TaskContract("task.read_only", "Forbid workspace writes.")

    assert "ADAPTER_SIDE_EFFECT_UNAUTHORIZED" in check_task_fitness(graph, task)


@pytest.mark.parametrize("fixture_path", ADAPTER_FIXTURES, ids=lambda path: path.stem)
def test_adapter_fitness_two_unlike_sanitized_fixtures(fixture_path: Path) -> None:
    fixture = _load_adapter_fixture(fixture_path)
    fitness = fixture["fitness"]
    sanitization = fixture["adapter"]["sanitization"]
    assert fixture["schema_version"] == "1.0"
    assert sanitization["synthetic_only"] is True
    assert sanitization["contains_private_paths"] is False
    assert sanitization["contains_private_data"] is False
    assert sanitization["contains_secrets"] is False
    assert fitness["blocking_gaps"] == []
    assert fitness["schema_freeze_verdict"] == "ready"
    assert fitness["domain_leakage"]["forbidden_core_keys_present"] == []

    graph = normalize_adapter_fixture(fixture)
    restored = graph_from_json_bytes(graph.canonical_bytes)
    entry_inputs = tuple(port for module in graph.modules if module.module_id in graph.entry_module_ids for port in module.inputs)
    exit_outputs = tuple(port for module in graph.modules if module.module_id in graph.exit_module_ids for port in module.outputs)
    effects = tuple(sorted({effect for module in graph.modules for effect in module.side_effects}, key=lambda item: item.value))
    task = TaskContract(
        task_id=f"task.{fixture['adapter']['adapter_id']}",
        objective=fixture["method_graph"]["purpose"],
        required_inputs=entry_inputs,
        required_outputs=exit_outputs,
        allowed_side_effects=effects,
        max_modules=len(graph.modules),
        max_edges=len(graph.edges),
    )

    assert restored.to_dict() == graph.to_dict()
    assert restored.canonical_bytes == graph.canonical_bytes
    assert check_task_fitness(graph, task) == ()
    assert set(graph.to_dict()) == GRAPH_KEYS
    assert all(set(module) == MODULE_KEYS for module in graph.to_dict()["modules"])
    assert all(set(edge) == EDGE_KEYS for edge in graph.to_dict()["edges"])
    assert all("adapter_extensions" not in module for module in graph.to_dict()["modules"])
    assert graph.version_id == fixture["method_graph"]["version_id"]
    assert graph.purpose == fixture["method_graph"]["purpose"]
    assert graph.lineage.source_adapter_id == fixture["adapter"]["adapter_id"]
    assert graph.lineage.source_revision == fixture["adapter"]["source_revision"]

    source_modules = {item["module_id"]: item for item in fixture["method_graph"]["modules"]}
    for module in graph.modules:
        source_module = source_modules[module.module_id]
        assert module.version_id == source_module["version_id"]
        assert module.applicability_conditions == tuple(source_module["applicability_conditions"])
        assert module.preconditions == tuple(source_module["preconditions"])
        assert module.postconditions == tuple(source_module["postconditions"])
        assert module.side_effect_class.value == source_module["side_effect_class"]
        assert [item.value for item in module.permission_requirements] == source_module["permission_requirements"]
        assert module.failure_modes == tuple(source_module["failure_modes"])
        assert module.counterexamples == tuple(source_module["counterexamples"])
        assert module.fallback_refs == tuple(source_module["fallback_refs"])
        assert module.cost.value == source_module["cost"]
        assert module.risk.value == source_module["risk"]
        assert module.reversibility.value == source_module["reversibility"]
        assert module.content_refs == tuple(source_module["content_refs"])
        assert module.rule_refs == tuple(source_module["rule_refs"])
        assert module.memory_refs == _adapter_memory_refs(source_module["memory_refs"])
        assert [item.to_dict() for item in module.required_evidence] == [
            {
                "evidence_type": item["type"],
                "role": item.get("role"),
                "minimum_count": item.get("minimum_count", 1),
            }
            for item in source_module["required_evidence"]
        ]
        assert [item.to_dict() for item in module.produced_evidence] == [
            {
                "evidence_type": item["type"],
                "role": item.get("role"),
                "minimum_count": item.get("minimum_count", 1),
            }
            for item in source_module["produced_evidence"]
        ]

    source_edges = {item["edge_id"]: item for item in fixture["method_graph"]["edges"]}
    for edge in graph.edges:
        source_edge = source_edges[edge.edge_id]
        assert edge.kind.value == source_edge["edge_type"]
        if edge.kind in {EdgeKind.CONDITIONAL, EdgeKind.FALLBACK}:
            assert edge.condition_ref == f"condition.{edge.edge_id}"
        if edge.kind is EdgeKind.PARALLEL:
            assert edge.branch_label == source_edge["branch"]
            assert edge.join_key == source_edge["join_key"]
        if edge.kind is EdgeKind.LOOP:
            assert edge.max_iterations == source_edge["max_iterations"]


def test_adapter_fitness_fixtures_are_unlike_and_zero_blocking_gap() -> None:
    fixtures = [_load_adapter_fixture(path) for path in ADAPTER_FIXTURES]
    assert len({item["adapter"]["adapter_id"] for item in fixtures}) == 2
    assert len({item["adapter"]["source_kind"] for item in fixtures}) == 2
    assert all(not item["fitness"]["blocking_gaps"] for item in fixtures)


def test_computed_adapter_schema_freeze_report_has_zero_gaps() -> None:
    fixtures = [_load_adapter_fixture(path) for path in ADAPTER_FIXTURES]
    report = assess_adapter_schema_freeze(fixtures)

    assert report.ready is True
    assert report.blocking_gaps == ()
    assert report.adapter_ids == tuple(
        sorted(item["adapter"]["adapter_id"] for item in fixtures)
    )
    assert report.source_kinds == tuple(
        sorted(item["adapter"]["source_kind"] for item in fixtures)
    )
    assert len(report.graph_hashes) == 2
    assert all(len(value) == 64 for value in report.graph_hashes)


@pytest.mark.parametrize(
    ("mutation", "expected_gap"),
    (
        ("delete_pin", "ADAPTER_SOURCE_PIN_COVERAGE_INCOMPLETE"),
        ("change_pin", "ADAPTER_SOURCE_REVISION_MISMATCH"),
    ),
)
def test_computed_schema_freeze_detects_source_pin_drift(
    mutation: str,
    expected_gap: str,
) -> None:
    fixtures = [_load_adapter_fixture(path) for path in ADAPTER_FIXTURES]
    if mutation == "delete_pin":
        fixtures[1]["adapter"]["source_pins"].pop()
    else:
        fixtures[0]["adapter"]["source_pins"][0]["sha256"] = "0" * 64

    report = assess_adapter_schema_freeze(fixtures)

    assert report.ready is False
    assert expected_gap in report.blocking_gaps


@pytest.mark.parametrize("edge_type", ("conditional", "fallback", "loop", "parallel"))
def test_computed_schema_freeze_detects_missing_control_coverage(edge_type: str) -> None:
    fixtures = [_load_adapter_fixture(path) for path in ADAPTER_FIXTURES]
    for fixture in fixtures:
        fixture["method_graph"]["edges"] = [
            edge
            for edge in fixture["method_graph"]["edges"]
            if edge["edge_type"] != edge_type
        ]

    report = assess_adapter_schema_freeze(fixtures)

    assert report.ready is False
    assert f"ADAPTER_COVERAGE_MISSING_{edge_type.upper()}" in report.blocking_gaps


def test_computed_schema_freeze_requires_distinct_adapter_ids() -> None:
    fixtures = [_load_adapter_fixture(path) for path in ADAPTER_FIXTURES]
    duplicate_id = fixtures[0]["adapter"]["adapter_id"]
    fixtures[1]["adapter"]["adapter_id"] = duplicate_id
    fixtures[1]["method_graph"]["lineage"]["source_adapter_id"] = duplicate_id

    report = assess_adapter_schema_freeze(fixtures)

    assert report.ready is False
    assert "ADAPTER_SET_NOT_UNLIKE" in report.blocking_gaps


def test_computed_schema_freeze_requires_distinct_source_kinds() -> None:
    fixtures = [_load_adapter_fixture(path) for path in ADAPTER_FIXTURES]
    second = fixtures[1]
    normalized_pins = [
        {"ref": item["ref"], "sha256": item["sha256"]}
        for item in second["adapter"]["source_pins"]
    ]
    local_revision = f"sha256:{canonical_sha256(normalized_pins)}"
    second["adapter"]["source_kind"] = fixtures[0]["adapter"]["source_kind"]
    second["adapter"]["source_revision"] = local_revision
    second["method_graph"]["lineage"]["source_revision"] = local_revision

    report = assess_adapter_schema_freeze(fixtures)

    assert report.ready is False
    assert "ADAPTER_SET_NOT_UNLIKE" in report.blocking_gaps


def test_computed_schema_freeze_detects_blocking_topology_defect() -> None:
    fixtures = [_load_adapter_fixture(path) for path in ADAPTER_FIXTURES]
    fixtures[0]["method_graph"]["edges"].append(
        {
            "edge_id": "blocking_topology_defect",
            "source_module_id": fixtures[0]["method_graph"]["entry_module_ids"][0],
            "target_module_id": "missing_module",
            "edge_type": "sequence",
        }
    )

    report = assess_adapter_schema_freeze(fixtures)

    assert report.ready is False
    assert "ADAPTER_NORMALIZATION_INVALID" in report.blocking_gaps


@pytest.mark.parametrize("fixture_path", ADAPTER_FIXTURES, ids=lambda path: path.stem)
@pytest.mark.parametrize(
    "excluded_surface",
    ("runtime_bindings", "adapter_extensions", "raw_condition"),
)
def test_adapter_only_surfaces_do_not_change_portable_identity(
    fixture_path: Path,
    excluded_surface: str,
) -> None:
    fixture = _load_adapter_fixture(fixture_path)
    baseline = normalize_adapter_fixture(fixture)
    mutated = copy.deepcopy(fixture)

    if excluded_surface == "runtime_bindings":
        for module in mutated["method_graph"]["modules"]:
            module["runtime_bindings"] = ["runtime.private_adapter.v999"]
    elif excluded_surface == "adapter_extensions":
        for module in mutated["method_graph"]["modules"]:
            module["adapter_extensions"] = {
                "domain_only_payload": "must_not_enter_portable_identity",
                "revision": 999,
            }
    else:
        conditioned_edges = [
            edge for edge in mutated["method_graph"]["edges"] if "condition" in edge
        ]
        assert conditioned_edges
        for edge in conditioned_edges:
            edge["condition"] = "__import__('os').system('must-not-run')"

    normalized = normalize_adapter_fixture(mutated)

    assert normalized.canonical_bytes == baseline.canonical_bytes
    assert normalized.content_hash == baseline.content_hash


@pytest.mark.parametrize("fixture_path", ADAPTER_FIXTURES, ids=lambda path: path.stem)
@pytest.mark.parametrize("portable_surface", ("portable_kind", "purpose", "port_type"))
def test_portable_adapter_contract_changes_portable_identity(
    fixture_path: Path,
    portable_surface: str,
) -> None:
    fixture = _load_adapter_fixture(fixture_path)
    baseline = normalize_adapter_fixture(fixture)
    mutated = copy.deepcopy(fixture)
    first_module = mutated["method_graph"]["modules"][0]

    if portable_surface == "portable_kind":
        first_module["portable_kind"] = (
            "adapter" if first_module["portable_kind"] != "adapter" else "transform"
        )
    elif portable_surface == "purpose":
        first_module["purpose"] += " Portable contract revision."
    else:
        assert first_module["inputs"]
        first_module["inputs"][0]["type"] = "revised_input_contract"

    normalized = normalize_adapter_fixture(mutated)

    assert normalized != baseline
    assert normalized.canonical_bytes != baseline.canonical_bytes
    assert normalized.content_hash != baseline.content_hash


@pytest.mark.parametrize(
    ("path", "unsafe_value", "error"),
    (
        (("fitness", "blocking_gaps"), ["adapter.unresolved_gap"], "ADAPTER_DECLARED_BLOCKING_GAPS"),
        (("fitness", "schema_freeze_verdict"), "review", "ADAPTER_DECLARED_NOT_READY"),
        (("adapter", "sanitization", "synthetic_only"), False, "ADAPTER_FIXTURE_UNSAFE"),
        (("adapter", "sanitization", "contains_private_paths"), True, "ADAPTER_FIXTURE_UNSAFE"),
        (("adapter", "sanitization", "contains_private_data"), True, "ADAPTER_FIXTURE_UNSAFE"),
        (("adapter", "sanitization", "contains_secrets"), True, "ADAPTER_FIXTURE_UNSAFE"),
    ),
    ids=(
        "blocking-gap",
        "non-ready-verdict",
        "non-synthetic",
        "private-path",
        "private-data",
        "secret",
    ),
)
@pytest.mark.parametrize("fixture_path", ADAPTER_FIXTURES, ids=lambda path: path.stem)
def test_production_normalizer_fails_closed_on_unsafe_fixture_metadata(
    fixture_path: Path,
    path: tuple[str, ...],
    unsafe_value: object,
    error: str,
) -> None:
    mutated = copy.deepcopy(_load_adapter_fixture(fixture_path))
    target: dict[str, Any] = mutated
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = unsafe_value

    with pytest.raises(IRValidationError, match=error):
        normalize_adapter_fixture(mutated)


def test_no_model_runtime_key_or_private_registry_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _sample_graph()
    baseline = graph.canonical_bytes
    adapter_hashes = tuple(
        normalize_adapter_fixture(_load_adapter_fixture(path)).content_hash
        for path in ADAPTER_FIXTURES
    )
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "MYCEVO_ROOT",
        "RESEARCHLOOP_ROOT",
    ):
        monkeypatch.setenv(key, f"private-{key.lower()}")

    assert graph.canonical_bytes == baseline
    rendered = baseline.decode("utf-8").lower()
    assert "api_key" not in rendered
    assert "private-" not in rendered
    assert "registry/" not in rendered
    assert tuple(
        normalize_adapter_fixture(_load_adapter_fixture(path)).content_hash
        for path in ADAPTER_FIXTURES
    ) == adapter_hashes
