# Workflow Topology Evolution: Phase 0 Baseline

Status: completed for the Phase 0 characterization gate; later topology phases and production-grade canonical transactions remain pending.

## Scope and ownership snapshot

This snapshot was taken on 2026-07-15 before workflow-topology implementation. `main` was at `cec9e97bd35632a1376f29fbff45dc60908756c4`, one commit ahead of `origin/main`. The repository was already dirty: nine tracked files were modified, 128 non-ignored files were untracked, and nothing was staged. Those pre-existing changes are user-owned and are not normalized, staged, reset, or folded into Phase 0.

Phase 0 owns only the following new or narrowly changed surfaces:

- this baseline report;
- `tests/fixtures/migration/resevo_v1_manifest.json`;
- `tests/test_migration_baseline.py`;
- `src/mycevo/migration.py`, the fail-closed preflight classifier;
- narrow preflight and exit-code integration in `src/mycevo/services.py` and `src/mycevo/cli.py`;
- leader-owned `.omx/context`, `.omx/plans`, and `.omx/ultragoal` coordination artifacts.

OMX Team was launched from a dedicated tmux session but correctly refused to provision worktrees with `leader_workspace_dirty_for_worktrees`. The leader did not stash or commit user-owned work. Three native read-only evidence lanes were used for baseline, compatibility, and test mapping instead.

## Existing compatibility anchors

| Surface | Current anchor | Frozen Phase 0 behavior |
|---|---|---|
| Guarded evolution | `src/mycevo/evolution.py` | Held-out and improvement gates can only make a candidate experiment-applicable; human promotion is still required. |
| CLI refusal | `src/mycevo/cli.py` | `mycevo evolve promote` returns `human_confirmation_required` and exit code 2. |
| Legacy CLI services | `src/mycevo/services.py` | Existing scripts remain routed through explicit compatibility names and environment roots. |
| Resevo migration | `src/mycevo/services.py::migration_plan` | Preview is read-only; apply keeps `.resevo`, creates a byte-preserving backup, copies only missing targets, and refuses a repeated apply when the backup already exists. |
| Legacy imports | `src/resevo`, `src/researchloop`, `src/mycevo/compat.py` | Deprecated names continue to point to the MycEvo package and CLI. |
| MCP boundary | `src/mycevo/mcp/server.py`, `mcp/mycevo_mcp.py`, `mcp/resevo_mcp.py` | Checkout wrappers route to the packaged server; current MCP exposes read tools plus explicit bounded write tools and no canonical promotion tool. |
| Provenance | `src/mycevo/provenance.py` | Run metadata stores paths and SHA-256 values without copying artifact contents; decision records retain the human gate. |

## Classified legacy mapping

The executable golden manifest is `tests/fixtures/migration/resevo_v1_manifest.json`.

| Class | Meaning | Current examples | Migration rule |
|---|---|---|---|
| `lossless` | Identity and meaning survive byte-for-byte or field-for-field. | File bytes, persistent IDs, status text, timestamps, provenance references, append-only trace. | Preserve source and backup; copy only to absent targets; test exact bytes plus parsed IDs/status. |
| `lossy_candidate_only` | The old signal may seed a future candidate but cannot retain canonical authority. | `apply_allowed`, promotion flags, score-only acceptance. | Raw compatibility copies carry no authority; a later explicit adapter may seed candidate evidence with missing gates recorded, but never pointer movement or promotion. |
| `ambiguous` | More than one identity, root, or byte interpretation is possible. | Missing/generated IDs, conflicting roots, unequal existing target bytes. | Reject with a stable code and require an explicit mapping or owner choice. |
| `rejected_unrepresentable` | The input cannot safely enter the new lifecycle. | Unknown lifecycle statuses, opaque executable hooks or automatic execution authority. | Reject fail-closed and preserve the original for manual review; do not guess a new status or executable meaning. |

Dual-read/single-write is the migration posture: compatibility readers may observe legacy and new records, while new topology objects write only the versioned MycEvo contracts. Shim removal, schema freeze, public claims, and canonical promotion remain owner decisions.

The Phase 0 `--apply` path is a compatibility-copy migration for a quiescent regular-file tree, not the future canonical graph transaction. It rejects unknown registry schemas/statuses, unstable identities, executable hooks, symlinks, and unequal/non-file targets before backup or target writes. It is not claimed to be crash-atomic or safe against concurrent source mutation; the lock/CAS/journal/recovery contract belongs to Phase 4B. A process interruption during compatibility copy requires manual inspection of the preserved source, backup, target, and hashes before retry.

## Baseline verification ledger

| Command | Result | Evidence meaning |
|---|---|---|
| `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider` | 55 passed before Phase 0 fixture addition | No pre-existing test failure was observed; six collected cases came from an ignored sync-conflict copy and duplicate existing tests. |
| `python -m compileall -q src mcp scripts` | pass | Existing Python surfaces compile. |
| `git diff --check` | pass with LF-to-CRLF warnings | No whitespace error; future edits must remain narrow to avoid line-ending churn. |
| `python -m mycevo.cli --help` | pass | Current product command surface is discoverable. |
| `python -m mycevo.cli --workspace-root . migrate resevo` | exit 0 | Migration preview is available and reports preservation/rollback intent. |
| `python -m mycevo.cli --workspace-root . evolve promote` | exit 2, `human_confirmation_required` | Automatic promotion remains refused. |
| `python -m pytest tests/test_migration_baseline.py tests/test_cli.py -p no:cacheprovider -k "migration or migrate or legacy or guarded_evolution or candidate_first or mcp_resolves_engine" -q` | Phase 0 exit command | Proves bytes/IDs/status parity, unknown/future/opaque/downgrade refusal, collision handling, compatibility aliases, MCP roots, candidate-first behavior, and guarded evolution. |
| Phase 0 exit command above | 37 passed, 2 skipped, 11 deselected | The skipped cases require Windows symbolic-link privilege; the unprivileged NTFS junction external-write regression passed, and redirect checks remain present for roots, components, final destinations, and backup paths. |
| `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider` | 85 passed, 2 skipped | Full repository regression after Phase 0 changes. |
| Python AST parse with `feature_version=(3, 10)` | pass | Python 3.10 grammar compatibility only; a Python 3.10 runtime was not available locally. |

## Phase 0 acceptance mapping

- Criterion 12: existing guarded evolution, candidate-first behavior, MCP separation, migration, and compatibility are anchored by existing `tests/test_cli.py` coverage plus the full baseline suite.
- Criterion 21: the migration golden classifies lossless, lossy-candidate-only, ambiguous, and rejected/unrepresentable mappings; exact file bytes, persistent IDs, status text, provenance, and repeated-apply refusal are machine-tested.
- Criterion 28: the authoritative 28-row traceability matrix remains in `.omx/plans/test-spec-mycevo-workflow-topology-evolution.md`; Phase 0 now has concrete commands and artifacts.

## Remaining risks handed to later phases

- The Phase 0 classifier recognizes explicit registry v1 collections and known lifecycle values only; additional legacy schemas require an adapter mapping rather than relaxed parsing.
- Current MCP code mixes read surfaces and bounded write tools in one module; Phase 6 must route topology operations through shared services without widening write authority.
- Current provenance and evolution files use timestamp/UUID identifiers; deterministic graph identity begins only with Phase 1 canonical JSON and must not rewrite old run IDs.
- Existing dirty files have no safe ownership signal beyond the pre-Phase-0 snapshot. Future phases should prefer new files and serialize changes to `src/mycevo/evolution.py`, `src/mycevo/cli.py`, and `tests/test_cli.py`.
- Local verification used Python 3.13.2 while the declared project matrix is Python 3.10-3.12; declared-version and wheel/clean-install parity remain Phase 6 work.
- Ruff and Mypy are neither installed nor configured. Static checks must use existing surfaces until the owner explicitly accepts a dependency/configuration change.
- The editable install metadata and untracked `src/mycevo.egg-info` are stale relative to `pyproject.toml`; do not treat installed metadata as release evidence.
- Existing closeout health is already red with 40 legacy ResearchLoop path-drift errors. This is pre-existing evidence and cannot be silently waived or attributed to topology work.
- Old `mcp/research_harness_mcp.py` is not behaviorally equivalent to the packaged MycEvo MCP search backend. This remains a blocking adapter gap for Phase 1 schema freeze and must be resolved by a compatibility adapter or an explicit version boundary.
