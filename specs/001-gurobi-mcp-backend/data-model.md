# Phase 1 Data Model: Gurobi MCP Multi-User Backend

Two layers of state:
1. **Persistent** — SQLite (`User` table). Survives restarts.
2. **In-memory** — the port pool and the session registry. Lost on restart by design (documented v1 limitation, FR-025); reconciled against running containers on startup.

---

## Persistent entity: `User`

Table `users`. One row per registered person.

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `id` | integer | PK, autoincrement | Internal `user_id`; used in container name `gurobimcp-<id>` |
| `username` | text | UNIQUE, NOT NULL | Login identifier (FR-002 uniqueness) |
| `password_hash` | text | NOT NULL | bcrypt hash; never reversible (FR-003) |
| `access_id` | text | NOT NULL | Gurobi Intelligence Access ID (identifier, not the secret) |
| `encrypted_secret` | blob/text | NOT NULL | Fernet-encrypted Gurobi Secret (FR-004); never stored or returned in clear |
| `assigned_port` | integer | NULLABLE, UNIQUE when set | Host port from pool while a container runs; NULL when none |
| `container_name` | text | NULLABLE | `gurobimcp-<id>` while running; NULL when none |
| `last_used_at` | datetime (UTC) | NULLABLE | Updated on every interaction (FR-019); drives the reaper (FR-020) |
| `created_at` | datetime (UTC) | NOT NULL, default now | Audit |

**Validation rules**
- `username`: non-empty, unique; reject duplicate at signup → 409.
- `password`: presence required at signup; minimum length enforced (e.g., ≥ 8) at the schema layer — never persisted in clear.
- `access_id` + Gurobi secret: both required at signup; secret encrypted before insert.
- `assigned_port`: must be within `61100–61200` and unique among rows when non-NULL.

**Lifecycle / state transitions** (container association on a row)

```
no-container ──(first /chat: acquire port, start container)──▶ running
running ──(every interaction)──▶ running (last_used_at refreshed)
running ──(reaper: idle > threshold)──▶ no-container (port released, fields cleared)
running ──(explicit end of all convs / shutdown)──▶ no-container
no-container ──(/chat after reap)──▶ running (recovery, fresh session) [FR-023]
```

The row never stores conversation content or live session state — those are in-memory only.

---

## In-memory entity: `PortPool`

Singleton guarding ports `61100–61200`.

| Field | Type | Notes |
|-------|------|-------|
| `available` | set[int] | Free ports |
| `in_use` | dict[int, user_id] | Port → owner |
| `lock` | asyncio.Lock | Serializes acquire/release |

Operations: `acquire(user_id) -> port | raise PoolExhausted`, `release(port)`. On startup, reconcile from running `app=gurobimcp` containers + `users.assigned_port` so no port leaks across restarts (R4).

---

## In-memory entity: `SessionRegistry`

Maps `(user_id, conversation_id)` → a live MCP session bundle.

| Field | Type | Notes |
|-------|------|-------|
| key | tuple(user_id: int, conversation_id: str) | Conversation identity (FR-009) |
| `agent` | enum(`gurobot`,`explainer`,`modeler`) | Agent bound to this thread at creation; immutable (FR-029/FR-030) |
| `session` | mcp.ClientSession | Live, initialized MCP session |
| `exit_stack` | contextlib.AsyncExitStack | Owns the entered transport + session contexts (R1) |
| `container_name` | str | Container this session is bound to (for staleness checks, FR-022) |
| `lock` | asyncio.Lock | Serializes calls on this session (FR-024, R7) |
| `last_used_at` | datetime (UTC) | Mirrors interaction time for session-level idle cleanup |

**Validation / invariants**
- A key has at most one live session.
- `agent` is set when the key is first created and never changes; a request whose `agent` differs from the stored value for an existing key is rejected with 400 before any MCP call (FR-030). An `agent` value outside the enum is rejected with 400 (FR-015).
- On stale-session rebuild (FR-023/R6), the **same** bound `agent` is reused — recovery never re-binds a different agent.
- Closing is LIFO via `exit_stack.aclose()`, always under `lock` (R7).
- A session whose `container_name` is no longer running is **stale** → discarded and rebuilt on next use (FR-023, R6).
- Distinct keys are fully independent, including multiple conversations for one user, each with its own bound agent (FR-012).

---

## Transient value object: `FilePayload`

Used in chat request/response bodies (not persisted in the DB).

| Field | Type | Notes |
|-------|------|-------|
| `filename` | str | Base name; written into / read from the user's `/work` mount (R8) |
| `content_b64` | str | Base64-encoded file bytes |

Total per-request payload size capped (e.g., 25 MB) to bound memory.

---

## Relationships

- `User` 1 ── 0..1 `assigned_port` / `container_name` (a user has at most one running container at a time — one environment per user, FR-016).
- `User` 1 ── 0..N `SessionRegistry` entries (a user may hold several conversations; all bind to that user's single container, FR-012 + FR-016).
- `SessionRegistry` entry N ── 1 container (`container_name`); reaping the container invalidates all its sessions (FR-022).

## Isolation guarantees (FR-021, SC-005)

- A container is started only with its owner's decrypted credentials; no shared credential path.
- Published ports bound to `127.0.0.1` only; cross-user reachability is impossible from outside the host.
- File workspaces are per-user directories (`data/workspaces/<user_id>/`); no shared workspace.
- Registry keys include `user_id`; one user's token can never resolve another user's session or container.
