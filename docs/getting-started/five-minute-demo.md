# Five-Minute MycEvo Technical Preview

This demo proves the current candidate-first local loop. It does not claim that the future Community lifecycle is complete.

## 1. Install

```powershell
python -m pip install -e .
mycevo --help
```

## 2. Use an isolated workspace

```powershell
$workspace = Join-Path $env:TEMP "mycevo-five-minute-demo"
$env:MYCEVO_USER_ROOT = Join-Path $env:TEMP "mycevo-five-minute-demo-user"
mycevo --root $workspace init --json
```

The command seeds only the selected workspace and the explicitly isolated user root.

## 3. Create a candidate

```powershell
mycevo --root $workspace demo --json
```

Expected contract:

```json
{
  "candidate_status": "pending validation",
  "promotion_performed": false
}
```

Inspect:

```text
<workspace>/registry/knowledge.yaml
<workspace>/examples/demo-paper/validation.json
<workspace>/examples/demo-paper/closeout.json
<workspace>/examples/demo-paper/recall.json
```

## 4. Verify installation

```powershell
mycevo --root $workspace doctor --json
mycevo --root $workspace status --json
```

## 5. Preview Agent configuration

```powershell
mycevo --root $workspace mcp install codex --dry-run
mycevo --root $workspace mcp install claude --dry-run
```

Dry-run means that no real Agent configuration is modified.

## Current limitation

The preview demonstrates capture, candidate writeback, validation metadata, closeout and recall. A stable standalone human-decision/canonical/handoff/export/delete contract is not shipped yet. See `docs/release/community-release-contract.md`.
