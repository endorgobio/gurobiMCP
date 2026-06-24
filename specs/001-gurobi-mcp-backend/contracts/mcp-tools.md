# Upstream MCP Tool Contract (gurobi/mcp container)

This is the **downstream** contract the backend speaks to — not something the backend exposes. The backend is an MCP *client* of each per-user container.

## Connection

- Transport: streamable HTTP (`streamablehttp_client`)
- URL: `http://127.0.0.1:<assigned_port>/api/v1/agent/mcp`
- `<assigned_port>`: host port (from pool `61100–61200`) mapped to container internal `61095`, bound to `127.0.0.1` only.
- Lifecycle: open once per `(user_id, conversation_id)`, `initialize()`, keep alive (see research R1/R7), close on reap/end.

> **Naming**: the REST API exposes these as the `agent` field (`gurobot` | `explainer` | `modeler`); each value maps 1:1 to the MCP tool of the same name. The agent is bound to a `conversation_id` on first use and is immutable (FR-029/FR-030), so a given session only ever calls one of the tools below.

## Tools

Three tools (one per agent), identical input shape:

| Tool | Multi-turn? | Purpose |
|------|-------------|---------|
| `gurobot` | yes | General Gurobi assistant |
| `explainer` | yes (asks clarifying questions) | Explains models/results; expects follow-ups |
| `modeler` | yes (asks clarifying questions) | Builds/edits optimization models; expects follow-ups |

### Input arguments (per brief)

```json
{
  "prompt": "string",
  "inputFiles": ["array of filenames in currentDir"] ,
  "currentDir": "string (in-container working dir, e.g. /work)"
}
```

- `inputFiles` may be `null`.
- The backend writes uploaded files (decoded from `ChatRequest.input_files`) into the user's host workspace bind-mounted at `currentDir` (`/work`) before the call.

### Output

- Text response (assistant message). Map to `ChatResponse.response`.
- Any produced files: returned by the tool and/or written under `currentDir`. The backend reads them back and returns them as `ChatResponse.output_files` (base64).

> **Confirm during implementation** (flagged in research R3/R8, non-blocking):
> 1. Exact environment variable names the image expects for Gurobi Access ID / Secret (or generated license file).
> 2. Exact structure of the tool result that enumerates `outputFiles` vs. relying on scanning `currentDir` for new files.
>
> Verify both against `gurobi/mcp:latest` on the VM (e.g., `docker run` + MCP `list_tools` / `call_tool`) and pin them in the container manager + mcp client modules.

## Error mapping

| Upstream condition | Backend behavior |
|--------------------|------------------|
| Container not ready / connection refused | readiness poll + retry; if persistent → 502 (FR-026) |
| Gurobi credentials rejected at startup | 502 with clear message; do not leave container/port allocated (FR-026) |
| Transport error mid-session (container reaped) | mark session stale, rebuild, retry once (FR-023) |
| Unknown/invalid `agent` value from client | 400 before any MCP call (FR-015) |
| `agent` differs from the one bound to the conversation_id | 400 before any MCP call (FR-030) |
