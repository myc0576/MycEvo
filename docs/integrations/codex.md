# Codex Integration

Status: **L1 configuration dry-run tested; native event capture not claimed.**

```powershell
mycevo --root <workspace> mcp install codex --dry-run
```

Review the generated command and environment before installing. MycEvo separates:

- `MYCEVO_ENGINE_ROOT`: public engine/code location;
- `MYCEVO_ROOT`: local workspace instance.

The adapter must not silently replace either path with a developer-machine default. Automatic writes remain candidate-first and cannot promote canonical state.
