# Phase 2 deterministic workflow diagnostics

Date: 2026-07-15
Scope: G003 fixture and contract boundary for static `validate`, `diff`, and `diagnose` behavior.

## Outcome

Phase 2 adds deterministic inspection over immutable `MethodGraphVersion` values. It explains structural problems and safe candidate alternatives without running a module, importing an adapter runtime, calling a model, waiting for evidence, writing workflow state, or changing a canonical pointer.

The sanitized canonical goldens are under `tests/fixtures/diagnostics/`:

- `valid_graph.json` - evidence gate plus authorized human gate; expected issue set is empty;
- `unreachable_deprecated_stale_ref_graph.json` - reachable deprecated module, unreachable orphan, and an unbound external rule ref;
- `prerequisite_cycle_order_graph.json` - a constructor-legal prerequisite cycle plus contradictory sequence order;
- `gate_control_side_effect_graph.json` - a bounded loop with no exit, missing evidence/human gates, and an unauthorized write before validation;
- `researchloop_unbounded_fallback_graph.json` - a sanitized ResearchLoop adapter regression in which `e03` (`EVIDENCE_FOR`) and `e05` (`FALLBACK`) form an unbounded evidence/retry cycle; the golden records `GRAPH_ILLEGAL_CYCLE` for `e03`/`e05` and `GRAPH_FALLBACK_EXHAUSTION_MISSING` for `e05`, because an evidence-ordering edge is not an executable fallback-to-exit route;
- `clean_control_forms_graph.json` - clean condition, parallel join, bounded loop with a non-loop exit, and single fallback forms; the expected issue set is empty;
- `contexts.json` - caller-supplied task contracts, strict reference catalogs, explicit gate policy, and finite module/edge/issue budgets; `max_analysis_steps` uses the deterministic production default unless a caller overrides it;
- `expected_issues.json` - exact production `DiagnosticIssue.to_dict()` goldens with stable ordering.

All graph goldens must parse through `MethodGraphVersion.from_dict`. They deliberately avoid malformed endpoints, unknown prerequisites, invalid control contracts, mismatched data ports, and contradictory permission declarations already rejected by the IR constructor. Context-owned task policy and external-reference inventories trigger diagnostics that cannot be represented as constructor-valid graph fields.

## Surface boundaries

### `workflow validate`

`validate_graph` answers whether an already parsed graph satisfies deterministic invariants. IR parsing and constructor validation remain the responsibility of `MethodGraphVersion.from_dict`; validation then covers flow reachability, required input bindings, prerequisites, cycle/order safety, control topology, permission contracts, supplied reference inventory, task fitness, and required gates when an explicit policy is supplied.

It returns structured issues. It does not repair, execute, schedule, rank, promote, or persist the graph.

### `workflow diff`

`diff_graphs` compares two already-valid immutable graph versions. Its `GraphDiff` output is a deterministic structural delta over module identities/versions, entry/exit roles, typed edges, contracts, controls, evidence declarations, and permissions. Base and target hashes bind all other graph-level changes, including lineage, without pretending to explain them as module operations.

After deterministic topological orders are built, reordered-module detection maps target positions and uses prefix-maximum plus suffix-minimum scans. That comparison pass is `O(V)` in the number of common modules instead of enumerating all `O(V^2)` module pairs. This is a claim about the reorder comparison pass, not about the preceding graph construction and topological sorting work.

It does not infer semantic equivalence, choose a preferred graph, create an `EvolutionChangeSet`, apply operations, or move a canonical pointer. A diff is descriptive evidence, not an approval or promotion event.

### `diagnose`

`diagnose_graph` combines a valid graph with optional, explicit `TaskContract`, `known_refs`, and `DiagnosticPolicy` inputs. It may report missing capability contracts, unreachable modules, prerequisite/order contradictions, unsafe side effects, stale/deprecated references, invalid control topology, and missing evidence or human gates. It does not infer policy from source documents, filenames, adapter metadata, or domain vocabulary.

Deterministic diagnosis may propose only a textual `safe_alternative`. It does not create or apply a topology change. Agent-assisted semantic proposals belong to Phase 3 and remain candidate-only.

## Edge roles are explicit

One adjacency does not serve every question:

- **Execution edges** are `DATA`, `SEQUENCE`, `CONDITIONAL`, `DEPENDS_ON`, `WRITES_CANDIDATE_MEMORY`, `CONDITION_TRUE`, `CONDITION_FALSE`, `PARALLEL`, `LOOP`, and `FALLBACK`.
- **Evidence ordering** adds `EVIDENCE_FOR`. It is not an executable control instruction, but it declares that evidence production precedes validation or consumption. The combined ordering set is therefore used for topology reachability, exit reachability, prerequisite order, evidence binding, and illegal-cycle analysis. This is why the sanitized ResearchLoop `e03` evidence dependency followed by the `e05` fallback is rejected as an unbounded cycle. Explicit bounded `LOOP` edges are omitted from illegal-cycle detection and checked through their own loop contract.
- **Pure relationship edges** are `GOVERNED_BY` and `READS_MEMORY`. They have no portable forward execution direction, so they cannot make a dead module reachable, satisfy prerequisite order, or fabricate a cycle.
- **Diff reorder analysis** uses the same ordering roles but excludes `LOOP` and `FALLBACK`. Repetition and recovery do not define the stable forward order used by `GraphDiff.reordered_module_ids`; `EVIDENCE_FOR` does participate because it is an ordering dependency.
- **Cycle attribution** reports only non-`LOOP` ordering edges whose endpoints belong to the detected strongly connected component. Pure governance or memory relations and unrelated edges between other modules are not attributed as causes merely because their endpoints are nearby.
- **Control analysis** uses narrower contracts where required. A condition needs both a positive edge (`CONDITIONAL` or `CONDITION_TRUE`) and an exhaustion edge (`CONDITION_FALSE` or `FALLBACK`). A parallel fork needs a downstream common join that is distinct from every branch root; routing one branch through a sibling branch root does not count as joining. A bounded loop needs an outgoing non-`LOOP` execution edge and an execution-only route from that continuation to a declared exit; `EVIDENCE_FOR`, `GOVERNED_BY`, and `READS_MEMORY` cannot masquerade as a loop exit. Fallback ambiguity and fallback-to-exit exhaustion are checked independently.

This separation prevents reference-only edges from hiding unreachable modules or fabricating execution order while still detecting an evidence-driven fallback loop.

## Gates are bound, not inferred

Two gate checks have different strength and must not be conflated:

- An explicit `DiagnosticPolicy(require_evidence_gate=True)` or `require_human_gate=True` asks whether a qualifying gate is active: it must be reachable from an entry and able to reach an exit. This active-topology presence check is a static policy diagnostic only; it does not prove that a particular writeback is bound to the gate.
- Candidate-memory writeback is path-bound. Each required evidence specification must be satisfied by evidence **owned and produced by an upstream `VALIDATION` module**. Matching considers evidence type, required role when present, and aggregate minimum count; evidence declared by a non-validation module cannot satisfy this gate. Every candidate-to-exit ordering path must also pass through an authorized, append-only human decision. A validation node elsewhere in the graph or a human node on only one branch does not satisfy this contract.

Neither a presence-only policy result nor a clean path-bound diagnosis is evidence pass, human approval, or promotion authority.

## Budget and fail-closed behavior

`DiagnosticPolicy` bounds static analysis with positive `max_modules`, `max_edges`, `max_issues`, and `max_analysis_steps` values. Module and edge limits are checked first. Before graph walks begin, a deterministic conservative estimator accounts for graph size, nested contract items, ordering edges, repeated reachability work, supplied task/reference context, and bounded issue-collector work. If that estimate exceeds `max_analysis_steps`, diagnosis returns one `GRAPH_ANALYSIS_BUDGET_EXCEEDED` issue without starting traversal. Identical graph, task, reference inventory, and policy inputs produce the same estimate.

If issue production exceeds `max_issues`, the deterministic bounded result retains the lowest ordered `max_issues - 1` findings and adds a terminal `GRAPH_ANALYSIS_BUDGET_EXCEEDED` sentinel to state that analysis is incomplete. Callers must treat any budget issue as fail-closed, not as a partial pass.

When `require_known_refs=True`, omitting the caller-supplied reference catalog fails closed with `GRAPH_REFERENCE_CATALOG_MISSING`; an explicitly supplied but incomplete catalog yields `GRAPH_STALE_REFERENCE`. Strict coverage includes every module `contract_ref`, the graph `task_contract_ref`, and every content, rule, memory, and evidence reference. The diagnostic core never discovers or dereferences a catalog itself.

Each golden context therefore lists the immutable contract IDs actually used by its graph in addition to its domain references. The structural stale-reference fixture deliberately omits only `rule.missing`; its valid module and task contract IDs remain cataloged. This keeps clean intended cases clean without weakening separate strict empty-catalog and missing-catalog negative tests.

A supplied `TaskContract` independently enforces its module/edge budgets, required ports, and allowed side effects through deterministic `GRAPH_TASK_*` or `GRAPH_SIDE_EFFECT_UNAUTHORIZED` issues.

The fixture contexts explicitly record finite module, edge, issue, and task budgets; their analysis-step ceiling is the finite `DiagnosticPolicy` default. A caller that omits a `TaskContract` receives no task-fitness claim, and no diagnostic result can authorize apply, writeback, or promotion.

## Issue contract and ordering

Every `DiagnosticIssue` has exactly:

- a stable `GRAPH_*` code;
- severity (`error`, `warning`, or `info`);
- a deterministic human-readable `message`;
- sorted, deduplicated `module_ids` and `edge_ids` tuples;
- a non-executable `safe_alternative`.

Issues identify graph elements only through stable module and edge IDs, never array indexes. Identity references remain stable when canonical module ordering changes. Production ordering is the tuple `(code, edge_ids, message, module_ids, safe_alternative, severity)`, and the golden stores the exact `to_dict()` sequence returned by `diagnose_graph`.

The fixture code vocabulary includes:

| Code | Meaning |
| --- | --- |
| `GRAPH_ANALYSIS_BUDGET_EXCEEDED` | configured graph, analysis-step, or issue budget was exceeded; analysis is incomplete |
| `GRAPH_REFERENCE_CATALOG_MISSING` | strict reference checking was requested without a supplied catalog |
| `GRAPH_UNREACHABLE_MODULE` | no ordering path from any entry reaches the module |
| `GRAPH_MODULE_CANNOT_REACH_EXIT` | an entry-reachable module has no ordering route to a declared exit |
| `GRAPH_DEPRECATED_MODULE_REFERENCED` | an active path or contract still depends on a deprecated module |
| `GRAPH_STALE_REFERENCE` | a module/task contract or content/rule/memory/evidence ref is absent from supplied strict `known_refs` |
| `GRAPH_ILLEGAL_CYCLE` | an evidence/flow cycle exists without an explicit bounded `LOOP` edge; edge IDs contain only causal ordering edges inside the component |
| `GRAPH_PREREQUISITE_CYCLE` | module prerequisites contain a directed cycle |
| `GRAPH_PREREQUISITE_UNSATISFIED` | a declared prerequisite is not upstream in topology order |
| `GRAPH_ORDER_CONTRADICTION` | topology orders a module upstream of its prerequisite |
| `GRAPH_CONTROL_EXIT_MISSING` | a bounded loop lacks a non-loop execution continuation with an execution-only route to an exit |
| `GRAPH_CONDITION_INCOMPLETE` | a condition has no explicit positive outcome |
| `GRAPH_CONDITION_EXHAUSTION_MISSING` | a condition has no explicit false/fail-closed outcome |
| `GRAPH_FALLBACK_EXHAUSTION_MISSING` | declared fallback targets have no non-fallback, non-loop route to an exit |
| `GRAPH_FALLBACK_AMBIGUOUS` | multiple fallback routes lack a deterministic priority contract |
| `GRAPH_PARALLEL_JOIN_UNRESOLVED` | parallel branches do not converge on a downstream common node distinct from all branch roots |
| `GRAPH_EVIDENCE_GATE_MISSING` | required evidence is absent or is not owned by a matching upstream validation module |
| `GRAPH_HUMAN_GATE_MISSING` | an authorized human decision is absent or does not cover every required path |
| `GRAPH_SIDE_EFFECT_ORDER_UNSAFE` | a write boundary occurs before an explicitly required validation boundary |
| `GRAPH_SIDE_EFFECT_UNAUTHORIZED` | graph side effects exceed the supplied task contract allowlist |

## Read-only and immutability guarantee

For identical graph values plus identical task, reference inventory, and explicit policy values, diagnostics return the same ordered issue dictionaries. The fixture loader reads local golden files, but the production diagnostic core receives only parsed values. It never opens or hashes source files, reads source bytes, follows `source_refs`, or dereferences `known_refs`.

Tests snapshot each input graph object's `content_hash`, `canonical_bytes`, and `to_dict()` before `validate`, `diff`, or `diagnose`, then assert that all three representations are unchanged. Golden tests additionally assert that each explicitly loaded graph fixture's bytes already equal the parsed graph's canonical bytes. These checks prove object immutability and preservation of the fixture inputs used by the test. They do not claim that an entire watched workspace remains unchanged; unrelated editors, sync software, or concurrent tasks are outside the diagnostic core's guarantee.

The implementation must not:

1. execute or import workflow nodes or adapter bindings;
2. call network, model, telemetry, external-service, or subprocess APIs;
3. read source bytes/files, inspect private registries, or dereference external refs;
4. write graph, task, registry, evidence, decision, pointer, cache, or audit state;
5. treat a clean validation or diagnosis as evidence pass, human approval, or promotion authority.

Tests monkeypatch common I/O and execution surfaces to fail if the production core touches them.

## Explicitly out of scope

Phase 3 owns external-Agent semantic proposals, ranking, merge/split/reorder suggestions, and `EvolutionChangeSet` construction. Phase 4 owns frozen Benchmark/Evidence packs, evaluator execution boundaries, partial human decisions, repeated-failure policy, and canonical pointer transactions.

Phase 2 does not implement either phase and must not expose an apply, approve, promote, retry-until-success, or canonical-mutation path.
