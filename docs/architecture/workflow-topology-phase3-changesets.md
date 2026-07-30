# Phase 3 bounded ChangeSets and exact selection

Date: 2026-07-15
Scope: G004 contract boundary for external proposals, validated ChangeSets, exact partial selection, deterministic candidate materialization, structural revalidation, and re-evaluation binding.

## Outcome

Phase 3 accepts a data-only external proposal and can derive one immutable, policy-bound `EvolutionChangeSet`. It can resolve a complete set of per-delta preview dispositions, materialize the exact dependency-closed result, revalidate that result, and emit a fresh re-evaluation requirement bound to its hash.

It does not execute a workflow node, run an evaluator, authenticate a human actor, apply a candidate to canonical state, write a pointer, or promote memory. `accepted`, `rejected`, and `deferred` are selection-preview inputs in this phase; they are not authorization or promotion decisions.

The public Draft 2020-12 schemas are:

- `src/mycevo/schemas/external-proposal.v1.schema.json`
- `src/mycevo/schemas/evolution-changeset.v1.schema.json`

Every object is closed. Runtime bindings, commands, evaluator results, actor/session claims, lifecycle claims, apply flags, promotion fields, and canonical-pointer fields are rejected as unknown input.

## Candidate-only external proposal

`ExternalProposal` serializes exactly:

```text
schema
proposal_id
base_graph_hash
provenance
deltas
```

`schema` is `mycevo.external_proposal.v1`; `base_graph_hash` is a lowercase SHA-256 of the immutable base graph. Provenance contains portable `source_kind` and `source_id` identifiers, source and evidence references, `low | medium | high` confidence, and falsification conditions. Provenance can justify inspection of a proposal, but it grants no authority.

External JSON is accepted only as bounded UTF-8 bytes with duplicate-key rejection. Payload size is checked before decoding. The parser imports or executes nothing named by the proposal.

## Four linked delta planes and six operations

Each proposal contains one or more `EvolutionDelta` values. A delta belongs to exactly one plane:

| Plane | Portable responsibility |
| --- | --- |
| `topology` | module and typed-edge composition |
| `content` | versioned module content references or contracts |
| `rules` | versioned rule references and governing constraints |
| `memory` | candidate-memory references and writeback contracts |

The planes remain members of one causally linked ChangeSet, while dependencies, conflicts, and preview dispositions remain explicit per delta. Domain-specific proposal fields do not enter the core.

The allowed operation vocabulary is exactly:

```text
add
delete
merge
split
replace
reorder
```

Each delta also carries a rationale, dependencies, symmetric conflicts, evidence references, falsification conditions, and evaluator requirement references. Unknown or self dependencies/conflicts, a pair declared as both dependency and conflict, asymmetric conflict declarations, and dependency cycles fail validation.

## Typed `GraphPatch`

All six operations use the same closed, declarative patch surface:

- `expectations`: `(module | edge, object_id, sha256)` preconditions bound to complete canonical object hashes;
- `remove_module_ids` and `remove_edge_ids`;
- complete typed `put_modules` and `put_edges` objects;
- optional `boundary`, containing expected and result entry/exit module ID sets.

Boundary results must preserve at least one entry and one exit. Expectations prevent a proposal from silently editing an object other than the exact object it inspected. Removing or replacing a touched object requires the matching expectation. Put objects are complete immutable contracts, not JSON merge fragments or executable instructions.

Expectations are also the delta's typed read set: `module:<id>` or `edge:<id>`. Removes, puts, and a boundary change form its typed write set. The touched-object budget counts the per-delta union of reads and writes, so reading and then replacing one object consumes one touched-object slot rather than two. For independent deltas, write/write and write/read hazards in either direction require an explicit dependency order or a symmetric conflict. This prevents an apparently disjoint selection from validating against a predecessor that another selected delta silently changes.

The operation name records intent; the typed patch records the exact deterministic transform. Operation-specific consistency validation rejects patches that cannot express the declared add, delete, merge, split, replace, or reorder intent. Deprecated modules may be remediated by `delete` or `replace`; unrelated operations may not preserve or introduce a deprecated active contract as a shortcut.

| Operation | Module-shape contract |
| --- | --- |
| `add` | add exactly one module and remove none |
| `delete` | remove exactly one module and add none; remove every incident edge explicitly |
| `merge` | remove at least two modules and add exactly one |
| `split` | remove exactly one module and add at least two |
| `replace` | replace exactly one module while preserving `module_id`; the explicit form removes and puts the same ID, while the implicit CAS form may omit removal only when an exact object expectation guards that same-ID predecessor. One-to-one identity-changing replacement is forbidden and must be redesigned as `merge` or `split` |
| `reorder` | add/remove no modules, change at least one ordering edge, and leave graph boundaries unchanged |

Every same-ID module replacement that changes the contract must also assign a new `version_id`. Apart from that shared version field, same-ID field ownership is disjoint:

| Plane | Fields that a same-ID replacement may change |
| --- | --- |
| `topology` | `kind`, `inputs`, `outputs`, `prerequisites`, `evidence_refs`, `side_effects`, `required_evidence`, `produced_evidence`, `side_effect_class`, `permission_requirements`, `cost`, `risk`, `reversibility`, `control`, `contract_ref`, `deprecated` |
| `content` | `name`, `description`, `content_refs` |
| `rules` | `applicability_conditions`, `preconditions`, `postconditions`, `failure_modes`, `counterexamples`, `fallback_refs`, `rule_refs` |
| `memory` | `memory_refs` |

Non-topology deltas must use same-ID `replace`; they cannot create a module or change a field owned by another plane. A same-ID topology replacement must preserve every content-, rules-, and memory-owned field. A newly created topology module supplies its required `name` and `description`, but initializes `content_refs`, all rules-owned fields, and `memory_refs` to their empty/default values. Topology operations may still change graph membership, edges, and boundaries through the explicit patch surface.

## Validated `EvolutionChangeSet`

A validated ChangeSet serializes exactly:

```text
schema
change_set_id
base_graph_hash
provenance
deltas
effective_budget
allowed_operations
policy_hash
```

`schema` is `mycevo.evolution_changeset.v1`. Validation rechecks the proposal base hash, operation allowlist, patch expectations, cross-delta dependencies/conflicts, typed read/write hazards, and all authority budgets. Both the sorted `allowed_operations` array and `effective_budget` are copied into the immutable object so later selection and materialization cannot silently use a wider policy.

`policy_hash` is exactly SHA-256 over canonical JSON:

```json
{
  "allowed_operations": ["...sorted operation values..."],
  "effective_budget": {"...": "the complete effective budget"}
}
```

Materialization recomputes this digest from the two stored fields and rejects a mismatch before applying a delta. Mutating only the budget, only the allowlist, or only the digest therefore invalidates the selection/ChangeSet binding.

Neither `ExternalProposal` nor `EvolutionChangeSet` contains a lifecycle status. Lifecycle records are separate immutable state surfaces; an external producer cannot self-declare `validated`, `accepted`, `resolved`, or `canonicalized` in proposal JSON.

## Authority budgets

`ChangeSetBudget` has finite integer bounds. The current defaults are:

| Field | Default | Enforcement |
| --- | ---: | --- |
| `max_payload_bytes` | 1,000,000 | before JSON decode |
| `max_deltas` | 64 | proposal and validated ChangeSet |
| `max_dependencies` | 256 | total dependency references |
| `max_conflicts` | 256 | total conflict references |
| `max_dependency_depth` | 16 | dependency-closure traversal |
| `max_modules_added` | 64 | typed patch composite |
| `max_modules_removed` | 64 | typed patch composite |
| `max_edges_added` | 256 | typed patch composite |
| `max_edges_removed` | 256 | typed patch composite |
| `max_touched_objects` | 512 | per-delta union of expectation read set and patch write set |
| `max_result_modules` | 10,000 | final materialized graph |
| `max_result_edges` | 100,000 | final materialized graph |
| `max_analysis_steps` | 5,000,000 | deterministic preflight estimate |

Positive-capacity fields reject values below one; count fields that can legitimately forbid an action may be zero. Every budget field has an absolute upper bound of 1,000,000,000. Effective policy may be stricter than these defaults.

Budgets are enforced twice. Preflight rejects oversized payloads, proposal structure, closure depth, and projected composite work before expensive graph transformation. Post-composite checks the actual touched objects and final module/edge counts. A small input that expands into an oversized graph therefore still fails closed.

## Lifecycle tables

These transitions validate separate state records; they are not writable status fields on proposal or ChangeSet payloads.

### ChangeSet

| From | Allowed next state | Meaning |
| --- | --- | --- |
| `proposed` | `validated`, `invalid`, `superseded` | schema, policy, and cross-delta consistency review |
| `validated` | `under_selection`, `superseded` | selection preview may begin |
| `under_selection` | `closure_materialized`, `superseded` | exact valid closure and result have been produced |
| `closure_materialized` | `resolved`, `superseded` | a later transaction service may record the terminal outcome |
| `invalid`, `resolved`, `superseded` | none | terminal |

### Delta

| From | Allowed next state | Meaning |
| --- | --- | --- |
| `proposed` | `structurally_valid`, `invalid`, `superseded` | patch and relationships reviewed |
| `structurally_valid` | `accepted`, `rejected`, `deferred`, `superseded` | complete preview disposition recorded |
| `accepted` | `dependency_closed`, `conflict_blocked`, `superseded` | exact selection algebra applied |
| `dependency_closed` | `candidate_materialized`, `superseded` | exact patch composite produced |
| `candidate_materialized` | `canonicalized`, `superseded` | only a later committed pointer event may justify `canonicalized` |
| `invalid`, `rejected`, `deferred`, `conflict_blocked`, `canonicalized`, `superseded` | none | terminal for this ChangeSet path |

`canonicalized` means that a later committed pointer event targets an immutable graph version containing the delta. The delta is never itself a pointer, and Phase 3 cannot perform this transition.

## Exact selection and explicit no-change

A selection preview must contain exactly one disposition for every delta. It cannot omit a difficult delta or add an unknown delta. The resolver then:

1. starts from the `accepted` IDs;
2. requires every transitive dependency to be accepted;
3. rejects a closure containing any conflict;
4. relies on ChangeSet validation to reject ambiguous independent typed write-set overlap that is neither dependency-ordered nor explicitly conflicted;
5. produces a deterministic dependency-first `application_order`;
6. records the sorted closure and hashes the exact resolution.

An accepted delta whose dependency is rejected or deferred blocks the selection. A conflict with an excluded delta does not block it. Conflict declarations must still be symmetric when the ChangeSet is validated.

The selection hash is SHA-256 over canonical `mycevo.selection.v1` data containing `change_set_hash`, `decisions` (the complete sorted delta dispositions), `closure_delta_ids`, and `application_order`. The derived `selection_hash` and display `status` are not inputs to their own hash.

If no delta is accepted, the resolver returns `status=no_change` and a `NoChangeReceipt` bound to the base, ChangeSet, and selection hashes plus sorted rejected/deferred IDs. It creates no candidate graph and no re-evaluation binding. No-change is an explicit successful resolution, not an empty candidate pretending to be evaluated.

## Deterministic materialization and structural revalidation

Materialization accepts only a `ready` selection whose ChangeSet and base hashes still match. It recomputes the complete selection, closure, application order, status, and selection hash before transforming anything. Deltas are applied once in deterministic dependency order. Every expectation is checked against the current complete object hash before its operation. The final transform must satisfy the immutable effective budget already bound into the ChangeSet.

The materialized graph:

- preserves the base `graph_id`;
- receives deterministic `version_id = <base.graph_id>.candidate.<first 24 characters of selection_hash>`;
- records the base graph hash in lineage parents;
- records `created_from_changeset = change_set_id`;
- remains a candidate immutable version and is never installed as canonical by this service.

Before validation begins, non-empty materialization freezes a `MaterializationContext` containing exactly:

```text
task: TaskContract.to_dict()
known_refs: sorted immutable string catalog
diagnostic_policy: every DiagnosticPolicy field
```

Its `validation_context_hash` is SHA-256 over that canonical JSON. This digest binds the result to the exact task, reference catalog, validation gates, size limits, issue limit, and analysis-step limit used for revalidation; a caller cannot later reinterpret the receipt under different validation semantics.

The exact candidate is then passed through strict structural validation with `DiagnosticPolicy(require_known_refs=True)`. Only a result with zero error-severity issues produces a successful materialization receipt and a re-evaluation requirement. Non-error diagnostics remain attached to that receipt, which also repeats `validation_context_hash`. This revalidation is mandatory even when every individual delta was structurally valid, because their composite may violate graph, task, permission, reference, or budget constraints.

If validation returns one or more error-severity issues, materialization returns a frozen `MaterializationFailureReceipt` rather than a candidate or evaluation claim. Its serialized surface is exactly:

```text
base_graph_hash
change_set_hash
selection_hash
closure_delta_ids
attempted_candidate_hash
validation_context_hash
issues
issues_digest
candidate_graph: null
reevaluation: null
```

`issues` is the complete, deterministic ordered list of `DiagnosticIssue.to_dict()` values returned for the failed validation; it is not reduced to error codes or truncated after the fact. `issues_digest` is SHA-256 over the canonical JSON issue list. `attempted_candidate_hash` identifies the immutable composite that was checked, but the failed candidate graph is deliberately withheld and `reevaluation` is always null. Consumers therefore receive auditable failure evidence without a usable candidate object or any suggestion that evaluation may proceed.

## Exact-result re-evaluation binding

Successful materialization returns a receipt bound to:

- `base_graph_hash`;
- `change_set_hash`;
- `selection_hash`;
- exact sorted `closure_delta_ids`;
- the complete candidate graph and `candidate_graph_hash`;
- the `validation_context_hash` that binds the validation task, reference catalog, and diagnostic policy;
- structural diagnostic results;
- one `ReevaluationRequirement`.

The requirement repeats the base, ChangeSet, selection, and candidate hashes; canonically sorts the selected deltas' evaluator requirement refs; and fixes `requires_fresh_evaluation=true`. Evidence or scores for the aggregate proposal, the base graph, or a different partial selection cannot be reused for this result.

Phase 3 only emits this binding. It does not compute the Phase 4 evaluation requirement digest, run an evaluator, interpret evidence, or mark the candidate eligible.

## Explicit authority boundary

The Phase 3 core is pure with respect to external state. It must not:

1. import or execute Agent/runtime bindings or workflow modules;
2. open source references, call a model, start an evaluator process, use network services, or read credentials;
3. accept actor identity, approval, promotion, canonical-pointer, CAS, retry, or transaction fields in proposal/ChangeSet schemas;
4. write graph, registry, evidence, decision, audit, lifecycle, or pointer state;
5. treat schema validity, confidence, an accepted preview disposition, a clean structural revalidation, or a materialization receipt as authority to apply or promote.

Phase 4A owns evaluator execution and evidence admissibility. Phase 4B owns authenticated human decisions, final drift checks, pointer transactions, and crash recovery.
