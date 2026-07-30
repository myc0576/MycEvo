# Third-Party Notices — Release Audit Draft

MycEvo depends on the following Python packages at runtime. The 2026-07-15 Windows verification environment reported:

| Distribution | Observed version | Declared license metadata |
| --- | --- | --- |
| PyYAML | 6.0.2 | MIT |
| FastMCP | 2.14.5 | Apache-2.0 |
| Typer | 0.21.1 | MIT |
| Rich | 13.9.4 | MIT |

These values are an audit aid, not a release lock and not legal approval. The final build environment must regenerate the dependency inventory, retain the applicable upstream notices and be reviewed before G1 passes. This draft does not assert that the dependency audit is complete.

The license candidate text is sourced from the n8n Sustainable Use License Version 1.0 section. Provenance is recorded in `docs/release/license-provenance.md`; n8n code is not incorporated by that reference.

ResearchLoop-derived files require an owner/provenance review before inclusion in the public staging repository. Only files listed by the public allowlist may enter staging.
