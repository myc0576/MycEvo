# Phase 1 adapter fitness: ResearchLoop and SmartMoney read-only review

Date: 2026-07-15
Scope: read-only reconnaissance and sanitized fixture design for G002. This note does not claim that a runtime adapter exists, execute either workflow, or authorize canonical mutation.

## Verdict

The proposed portable `MethodModule` / `MethodGraphVersion` field set is eligible to freeze for Phase 1 only when the **computed coverage and gap classifier** verifies the pinned sources, normalizes both unlike adapters, and returns zero unresolved blocking gaps. The `fitness.schema_freeze_verdict: ready` and empty `fitness.blocking_gaps` values inside a fixture are adapter declarations and test inputs; they are not evidence and cannot decide schema freeze by themselves.

The normalization decisions are explicit parts of the adapter contract. The classifier must compute coverage from the normalized graph and the pinned source inventory rather than trusting those declarations.

The two normative fixtures are:

- `tests/fixtures/adapters/researchloop_paper_lifecycle_v1.yaml`
- `tests/fixtures/adapters/smartmoney_read_only_review_v1.yaml`

Both fixtures are synthetic and contain no private path, real research data, real trading symbol or position, credential, account identifier, or secret. Their source inventories are immutable: ResearchLoop uses a combined revision digest plus per-file SHA-256 pins, while SmartMoney uses commit `acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4` plus per-file SHA-256 pins. A missing source, revision mismatch, hash mismatch, uncovered portable requirement, or computed blocking gap invalidates the freeze result.

## Fitness rule

A gap is **blocking** when omitting it would prevent deterministic topology operations or would make type/prerequisite validation, bounded control flow, evidence binding, side-effect/permission checks, immutable lineage, identity/hash handling, or human-gate semantics ambiguous. A gap is **non-blocking** only when it concerns presentation, external scheduling, optional runtime bindings, or pack-owned domain policy.

The core must therefore carry the following portable fields.

| Contract | Required portable meaning |
| --- | --- |
| `MethodModule` identity | stable `module_id`, immutable `version_id` |
| applicability | purpose and explicit applicability conditions |
| artifact contract | typed inputs and outputs |
| correctness contract | preconditions, postconditions, failure modes, counterexamples, fallback refs |
| evidence contract | required and produced evidence refs |
| safety contract | side-effect class and permission requirements |
| change economics | cost, risk, reversibility |
| linked planes | content, rule, and memory refs |
| adapter boundary | explicit portable kind; runtime bindings and namespaced `adapter_extensions` remain adapter-owned and are excluded from canonical IR |
| `MethodGraphVersion` identity | stable graph/version IDs and canonical-JSON hash policy |
| composition | entry/exit modules plus typed edges |
| bounded control | stable condition rule refs, explicit fallback, finite loop bounds, parallel join keys |
| provenance | parent version, source adapter, source revision |

The core validates these declarations but never imports a runtime, calls a model, waits for delayed data, runs an experiment, or executes a node.

## Adapter A: current ResearchLoop paper lifecycle

### Primary local sources

- `workflows/paper_lifecycle/paper_state_machine.yaml` — `sha256:954661f51d890e8ad88955e6e7e2730ea0935c347867d4fab04d053ed2137231`
- `workflows/paper_lifecycle/validation_rules.yaml` — `sha256:f26fb89bbd8c3faa2ca5a3924c9e6d7633e8f78282a433c6a7abfd3d238f495b`
- `workflows/paper_driven/paper_loop.md` — `sha256:764228d3ac5622d8dd649559577d5d80001fbdb7145e722cb22eb5b8cc0565dd`
- `workflows/paper_driven/reviewer_gate.md` — `sha256:b473d0ba0d2813d00e4044e18c551af364a541deb7b383f3ce49ad742ebb74c6`
- `workflows/self_evolution_loop/README.md` — `sha256:275d8777b3318d71896032d5228feef9d5be9ab7623f9a379b7aed52218e3222`

The fixture revision is the combined source-set identity `sha256:33eea51342844670b87d623f4411901fd2c831c3c074ef5f612165980425e257`. The classifier must verify every listed file hash before using this adapter as freeze evidence.

The source is an artifact- and gate-oriented research lifecycle. It already distinguishes candidate writeback from reusable status, requires evidence-linked review, and keeps `validated`, `reusable`, `approved`, `pass`, and `paper_ready` human controlled. The source state list is ordered, but its transition and failure routes are mostly implicit.

### Module mapping

| Source states/concern | Normalized module | Portable contracts exercised |
| --- | --- | --- |
| idea | `research_brief` | task input, candidate artifact write, idea gate |
| literature targeting + gap/claim design | `literature_and_claim_design` | source refs, claim evidence, memory read |
| experiment/evidence | `experiment_and_evidence` | external run envelope, immutable hashes, replay ref |
| experiment gate | `evidence_validation` | admissible evidence and evaluator binding |
| figure loop + manuscript story | `figure_and_manuscript_loop` | finite loop, review fallback, provenance |
| release + dissemination | `release_and_dissemination` | public/private policy and candidate packaging |
| retro + asset/improvement review | `retro_and_candidate_memory` | candidate memory and linked topology/rule/content deltas |
| reuse gate | `human_reuse_gate` | explicit per-delta accept/reject/defer |
| closeout | `closeout` | append-only audit and restartable lineage |

### Exact gap disposition

| Source gap | Blocking if left implicit? | Fixture resolution | Residual |
| --- | --- | --- | --- |
| ordered states have no explicit transition objects | yes: reorder and reachability would be ambiguous | emit `sequence`, `conditional`, and `fallback` edges | none |
| `figure_quality_loop` has no machine-readable bound | yes: an unbounded loop is unsafe | adapter policy emits `max_iterations: 3`, then fails closed | none |
| permissions and side effects are distributed across prose | yes: guard checks cannot be deterministic | every module declares both fields | none |
| claim/paper/figure vocabulary could leak into core | yes: would break domain portability | retain it in artifact types, rule refs, or `adapter_extensions` | none |
| exact venue, section labels, and figure numbering | no | leave to presentation/content adapters | non-blocking |
| external experiment scheduling | no, provided the evidence prerequisite is explicit | runtime binding only; the core records a RunEnvelope ref | non-blocking |

## Adapter B: SmartMoney Cub Harness read-only trading review

### Primary public sources

- [Repository README](https://github.com/myc0576/smartmoney-cub-harness/blob/acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4/README.md)
- [Harness contract](https://github.com/myc0576/smartmoney-cub-harness/blob/acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4/docs/harness-contract.md)
- [Architecture](https://github.com/myc0576/smartmoney-cub-harness/blob/acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4/docs/architecture.md)
- [Agent loop](https://github.com/myc0576/smartmoney-cub-harness/blob/acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4/docs/agent-loop.md)
- [Agent runbook](https://github.com/myc0576/smartmoney-cub-harness/blob/acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4/AGENT_RUNBOOK.md)
- [Manifest validation source](https://github.com/myc0576/smartmoney-cub-harness/blob/acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4/src/smartmoney_cub_harness/manifest.py)
- [Decision contract source](https://github.com/myc0576/smartmoney-cub-harness/blob/acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4/src/smartmoney_cub_harness/decision.py)
- [Delayed outcome source](https://github.com/myc0576/smartmoney-cub-harness/blob/acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4/src/smartmoney_cub_harness/outcome.py)
- [Evaluator source](https://github.com/myc0576/smartmoney-cub-harness/blob/acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4/src/smartmoney_cub_harness/evaluator.py)

All nine public files are pinned in the fixture to commit `acf7d6d3fd99dd6de61593a5101f56b1ff4e02f4` and to their observed SHA-256 values. Branch-head URLs are not admissible freeze evidence.

The repository defines a local-first, review-only chain: `Plan -> Observe -> Record -> Outcome -> Evaluate -> Memory -> Rule Candidate`. It declares `READ_ONLY_NO_ORDER_NO_CANCEL_NO_TRADE`, rejects evidence whose availability time is after the decision time, binds delayed D1/D3 outcomes, and keeps a rule as challenger until explicit human confirmation. A successful Agent loop has no promotion effect.

### Module mapping

| Source stage/concern | Normalized module | Portable contracts exercised |
| --- | --- | --- |
| plan + observe | `plan_and_observe` | task contract, provenance, forbidden capabilities |
| record | `record_manifest_and_decision` | append-only manifest/decision artifacts |
| manifest validation | `provenance_and_safety_validation` | temporal availability and read-only policy |
| D1/D3 outcome | `bind_delayed_outcomes` | static temporal prerequisites and parallel evidence branches |
| evaluate | `evaluate_outcomes` | hash-bound evaluator and BenchmarkPack-owned metrics |
| rule candidate | `create_challenger_candidate` | candidate memory, immutable lineage, no promotion effect |
| confirm-promotion | `human_promotion_gate` | explicit authorized decision after evidence pass |
| report/trace/case/memory/ledger | `append_review_memory` | append-only closeout and candidate memory |

### Exact gap disposition

| Source gap | Blocking if left implicit? | Fixture resolution | Residual |
| --- | --- | --- | --- |
| delayed D1/D3 collection could be mistaken for core scheduling | yes: execution semantics would leak into the IR | encode horizons as typed evidence prerequisites; external scheduler supplies artifacts | none |
| read-only permissions are chiefly policy prose/constants | yes: unsafe modules could otherwise compose | every module declares permissions/side effects; adapter policy lists forbidden capabilities | none |
| recommendation, human confirmation, and pointer movement can appear as one CLI flow | yes: promotion authority would be ambiguous | model evaluation, `HumanDecision`, and later canonical pointer event separately | none |
| symbol/action/price/horizon/return fields could leak into core | yes: would make the IR trading-specific | keep them in artifact schemas or `adapter_extensions` | none |
| exact D1/D3 clock scheduling | no | runtime scheduler adapter | non-blocking |
| optional external multi-agent review bridge | no | optional runtime binding only | non-blocking |
| metric names, weights, and thresholds | no | trading BenchmarkPack/policy pack | non-blocking |

## Domain-leakage check

The common core does not contain research-specific keys such as `paper_id`, `claim_id`, `manuscript_section`, `figure_id`, journal, venue, or scientific modality. It also does not contain trading-specific keys such as ticker/symbol, price, position, order, broker, account, D1/D3, action label, return, or invalidation price.

These terms may appear only in:

1. typed artifact schema names and payloads owned by the adapter;
2. pack-owned rule/evaluator references;
3. namespaced `adapter_extensions` ignored by portable topology operations;
4. human-facing labels.

Portable topology operations compare module identity, typed ports, prerequisites/postconditions, evidence, side effects, permissions, lineage, and typed edges. They do not interpret adapter payload fields.

Raw adapter conditions such as status expressions are never copied into canonical IR and are never executed. `normalize_adapter_fixture` replaces them with stable `condition.<edge_id>` rule references; runtime bindings and `adapter_extensions` are omitted entirely. Source pins are verifier inputs retained in the fixture audit record; the pinned `source_revision` and `source_refs` form the portable lineage. None of these raw adapter extensions becomes executable graph content.

## Legacy MCP scope boundary

The historical `mcp/research_harness_mcp.py` surface is a CLI/MCP compatibility layer, not a topology adapter and not an input to the portable graph hash. Its search-command parity remains a Phase 6 compatibility obligation. Removing that concern from the Phase 1 schema-freeze gate is an explicit release-scope boundary: this phase claims only static graph normalization and validation, while the Phase 6 tests remain responsible for preserving legacy CLI/MCP behavior.

## Freeze conditions carried forward

Schema freeze remains valid only if the computed coverage and gap classifier, followed by implementation tests, proves all of the following:

1. every `source_ref` has exactly one `source_pins` entry, every source byte stream matches its SHA-256 pin, and the declared `source_revision` matches the pinned source set;
2. both fixtures round-trip with stable canonical hashes;
3. every edge endpoint resolves, typed data edges connect compatible ports, and all module ports remain declarative contracts;
4. computed portable-field and control-form coverage is complete and the classifier returns zero blocking gaps, without trusting the fixture's `ready` label;
5. raw conditions normalize to stable rule refs and loops have finite bounds;
6. `runtime_bindings` and `adapter_extensions` never enter portable IDs or hashes;
7. a successful run or passing evaluation cannot mutate a canonical pointer;
8. candidate-memory writes cannot be treated as reusable-memory writes;
9. human gates require both authorized human decision and append-only audit permissions;
10. the core performs no imports, subprocess calls, network access, waiting, or workflow-node execution while loading or validating either fixture.

Only the classifier's computed result can establish that the two adapters expose no remaining schema-blocking gap. Fixture labels remain declarations even when their expected value is `ready`.
