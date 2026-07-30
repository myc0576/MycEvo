# Public release gate status

Status date: 2026-07-30
Release identity: **PaperFrames v0.2.0-rc.1**

This record preserves the historical blocked state and records the scoped
owner-authorized migration into the clean MycEvo release clone.

| Gate | Status | Evidence | Remaining requirement |
| --- | --- | --- | --- |
| G0 manifest and hashes | Approved for this RC | `docs/release/public-file-manifest.yaml`; audit selected 218, blocked 0 | None for this RC. |
| G1 owner, legal, rights and contribution approval | Approved for this RC | `docs/release/owner-authorization.md`; `LICENSE`; `NOTICE`; `docs/release/migration-review.md` | Scoped to the audited public tree; third-party assets retain original licenses. |
| G2 truthful release contract name | Pass for RC | `docs/releases/v0.2.0-rc.1.md` | Community label remains out of scope. |
| G3 tests and private-state isolation | Approved for this RC | 454 passed, 2 skipped on Windows golden path; hosted CI passed; source workspace unchanged | None for this RC. |
| G4 publication scans and clean install | Approved for this RC | Gitleaks 8.30.1 clean; wheel/sdist built and twine-checked; Golden Path passed | None for this RC. |
| G5 remote and visibility approval | Approved for this RC | clean clone origin `https://github.com/myc0576/MycEvo.git`; target is public | Push only the clean release clone; never change the protected source origin. |

## Publication rule

Only the clean release clone may be committed, pushed, tagged or used for a
GitHub pre-release. No PyPI, Docker, Marketplace or production package
publication is authorized by this RC.

## Historical boundary

The source workspace and its historical ResearchLoop origin remain untouched.
Private memory, knowledge, papers, experiments, logs, credentials, caches and
local configuration are excluded from the release tree.
