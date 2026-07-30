# Cross-Agent Handoff Preview Fixture

This sanitized fixture demonstrates the information MycEvo intends to preserve between Agent A and Agent B while the full Community lifecycle is still under development.

Files:

- `agent-a-task.yaml`: task, constraints, corrections, evidence and acceptance criteria captured by Agent A;
- `candidate.yaml`: candidate method improvement produced at closeout;
- `handoff-context.yaml`: context Agent B may inspect;
- `human-decision.yaml`: deliberately pending, proving that the fixture has no canonical promotion.

The fixture is intentionally fail-closed:

```text
candidate.status = pending validation
human-decision.status = pending
handoff-context.canonical = false
```

It does not pretend that current MycEvo already ships a complete decide/canonical/rollback/export/delete API.
