# Claude Code Integration

Status: **L1 configuration dry-run tested; native event capture not claimed.**

```powershell
mycevo --root <workspace> mcp install claude --dry-run
```

The dry-run prints the proposed stdio MCP configuration without editing the real Claude Code configuration. Confirm the engine/workspace paths before applying it.

Automatic MCP writes may create candidates or pending-validation records only.
