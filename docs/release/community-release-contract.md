# MycEvo Single-User Release Contract

**Decision:** default release identity is **Source-Available Technical Preview**.

The release may use the **Community** label only when every required row is `shipped + tested`.

| Promise | Public entry point | Implementation evidence | Automated evidence | Status |
|---|---|---|---|---|
| Initialize isolated local workspace | `mycevo init` | `src/mycevo/services.py::init_workspace` | `tests/test_cli.py::test_product_cli_initializes_portable_workspace` | shipped + tested |
| Capture deterministic task intake | `mycevo demo` | `src/mycevo/services.py::run_demo` | `tests/test_cli.py::test_five_minute_demo_writes_candidate_without_promotion` | shipped + tested |
| Create candidate | `mycevo demo`; source-checkout compatibility `self-evolution run` | `services.py::run_demo`, `scripts/self_evolution_loop.py` | candidate-first CLI tests | demo shipped + tested; legacy path compatibility only |
| Store evidence/provenance | `mycevo provenance`, demo artifacts | `src/mycevo/provenance.py` | provenance tests in `tests/test_cli.py` | shipped + tested |
| Inspect candidate and evidence/diff | demo JSON/artifacts, `mycevo recall`, `mycevo evolve evaluate` | `services.py`, `retrieval.py`, `evolution.py` | CLI/evolution tests | partial; preview |
| Explicit human approve/reject/defer | none | no standalone public contract | none | roadmap; required for Community |
| Promote canonical after human decision | none | automatic promotion is forbidden | negative guard tests only | roadmap; required for Community |
| Build a portable handoff package | example/documentation only | no stable public schema | preview fixture only | roadmap; required for Community |
| Roll back canonical lineage | evaluation records champion preservation | `src/mycevo/evolution.py` | guard tests | partial; required for Community |
| Import public workspace format | migration compatibility only | `services.py::migration_plan` | migration test | partial; required for Community |
| Export user-owned state | filesystem access only | no standalone contract | none | roadmap; required for Community |
| Delete selected user-owned state | workspace unregister does not delete | `services.py::workspace_remove` | no-data-delete test | partial; required for Community |

## Naming gate

- Any required roadmap/partial row means the public name remains **Technical Preview**.
- Missing rows must remain visible in README and Release notes.
- A full architecture rewrite is not required to publish the preview.
- A Community release requires a separate review showing every row is `shipped + tested`.
