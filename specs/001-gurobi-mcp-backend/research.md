# Phase 0 Research: Gurobi MCP Multi-User Backend

The spec's Assumptions already fixed the headline stack (FastAPI, SQLite, Docker SDK, bcrypt, Fernet, JWT, Caddy). Research below resolves the non-obvious design questions those choices create. Each item is in Decision / Rationale / Alternatives form.

---

## R1. Keeping an MCP `ClientSession` alive across multiple HTTP requests

**Decision**: For each `(user_id, conversation_id)`, open the MCP connection once and keep it open across requests by driving the SDK's async context managers with an `contextlib.AsyncExitStack` that is **entered manually and stored** in the session registry, rather than wrapped in a request-scoped `async with`. Concretely: call `stack.enter_async_context(streamablehttp_client(url))` to get the `(read, write, _)` streams, then `stack.enter_async_context(ClientSession(read, write))`, then `await session.initialize()`. Store `{session, stack, lock, container_name, last_used}` in the registry. On cleanup call `await stack.aclose()`.

**Rationale**: The MCP Python SDK exposes both `streamablehttp_client(url)` and `ClientSession(read, write)` as async context managers intended for `async with`. A FastAPI request handler cannot hold an `async with` open across separate requests. `AsyncExitStack` lets us enter those contexts imperatively, retain the live `ClientSession`, and unwind them in LIFO order later — this is the documented escape hatch for long-lived MCP sessions. Streamable HTTP (not stdio/SSE) is the right transport because the gurobi/mcp server exposes an HTTP MCP endpoint at `/api/v1/agent/mcp` on port 61095.

**Concurrency**: Each registry entry carries an `asyncio.Lock`. Every tool call acquires the lock, satisfying FR-024 (one active conversation per container, serialized). All entered contexts for one session must be entered and closed from the **same task** to keep `anyio` cancel-scope semantics valid — see R7.

**Alternatives considered**:
- *Per-request session (open+close each message)*: rejected — breaks multi-turn explainer/modeler (FR-010) and pays full connection cost every turn.
- *Dedicated worker task per session communicating via queues*: more robust against cross-task cancel-scope issues, but heavier for v1. Chosen approach is simpler; R7 documents the discipline needed to use it safely. Worker-task model is the documented fallback if cancel-scope errors appear.

---

## R2. URL and transport for the gurobi/mcp container

**Decision**: Connect to `http://127.0.0.1:<assigned_port>/api/v1/agent/mcp` using `streamablehttp_client`. `<assigned_port>` is the host port mapped to the container's internal `61095`.

**Rationale**: Brief states the container exposes `61095` internally with MCP path `/api/v1/agent/mcp`. The backend reaches it over loopback only; the host port comes from the pool (R4).

**Alternatives**: Docker internal network DNS (`gurobimcp-<id>:61095`) — rejected for v1 because the backend runs on the host (systemd), not in a container, so it uses published host ports on loopback.

---

## R3. Starting the per-user container with the Docker Python SDK

**Decision**: Use `docker.from_env()` and `client.containers.run(image="gurobi/mcp:latest", name=f"gurobimcp-{user_id}", environment={...}, ports={"61095/tcp": assigned_port}, volumes={host_workdir: {"bind": "/work", "mode": "rw"}}, detach=True, mem_limit=..., labels={"app": "gurobimcp"})`. Pass the user's decrypted Gurobi Access ID + Secret as environment variables at startup. Bind `127.0.0.1:assigned_port` only (not `0.0.0.0`) so the port is never externally reachable (FR-027).

**Rationale**: Brief mandates the Docker SDK, env-var credential injection, and a fresh container per user. Binding the published port to `127.0.0.1` enforces the "never exposed externally" rule at the Docker layer in addition to the NSG.

**Readiness**: After `run`, poll the MCP endpoint (TCP connect + a lightweight MCP `initialize`/list-tools, with timeout/backoff) before declaring the container ready; only then open the session. Prevents racing the container's startup (FR-017, FR-026).

**Credential env var names**: exact variable names the image expects (Access ID / Secret vs. generated license file) are an integration detail to confirm against the image on the VM during implementation; documented as a TODO in quickstart, not a blocker for design.

**Alternatives**: `subprocess` calls to the `docker` CLI — rejected by the brief ("not shell calls").

---

## R4. Port pool allocation and release

**Decision**: An in-memory `PortPool` over `61100–61200` guarded by an `asyncio.Lock`: `acquire()` returns the lowest free port (or raises `PoolExhausted`), `release(port)` returns it. Persist the currently-assigned port on the `User` row (`assigned_port`) so state is observable and reconcilable. On startup, reconcile the pool against actually-running `app=gurobimcp` containers (Docker labels) to recover from restarts.

**Rationale**: Bounded pool satisfies FR-018/SC-008. Startup reconciliation handles the "service restart" edge case without leaking ports. Pool exhaustion maps to a clear 503 (edge case: resource pool exhaustion).

**Alternatives**: Let Docker assign a random host port — rejected; the fixed pool is required by the brief and makes capacity explicit.

---

## R5. Idle reaper

**Decision**: A single asyncio background task started in the FastAPI lifespan, looping every N seconds (e.g., 60). For each running `app=gurobimcp` container whose owning user's `last_used_at` is older than `IDLE_TIMEOUT_MINUTES` (default 15): close any registry sessions bound to that container (LIFO `stack.aclose()`), stop+remove the container, release its port, clear `assigned_port`/`container_name` on the row. Ties session lifetime to container lifetime (FR-020, FR-022).

**Rationale**: Brief asks for a background reaper with a configurable threshold. asyncio task in lifespan needs no extra scheduler dependency for v1.

**Alternatives**: APScheduler / external cron — rejected as unnecessary weight; revisit if multiple periodic jobs appear.

---

## R6. Stale-session detection and graceful recovery

**Decision**: On each `/chat` call, resolve the registry entry for `(user_id, conversation_id)`. Before use, validate liveness: if the entry's `container_name` is no longer running (Docker status check) **or** a tool call raises a connection/transport error, treat the session as stale — discard it (`aclose()` best-effort, swallow errors), ensure the container is (re)started, open a fresh session, and retry the message once. Surface a normal response, never a 5xx for this path (FR-023, SC-003).

**Rationale**: The reaper can stop a container while its session is still registered; the next message must self-heal. A single retry bounds the recovery cost.

**Alternatives**: Proactively invalidate all sessions when the reaper stops a container (already done in R5) — kept as the primary mechanism; the per-call check is the safety net for races and post-restart orphans.

---

## R7. anyio cancel-scope discipline for manually-entered MCP contexts

**Decision**: Enter and close each session's `AsyncExitStack` from the same task context, and serialize all access via the per-session `asyncio.Lock`. Do not enter a session's contexts in one request task and close them in a different concurrently-running task. Reaper-initiated closes are funneled through the registry, which acquires the session lock before closing so no in-flight call is mid-stream.

**Rationale**: The MCP SDK is built on `anyio`; its streams use cancel scopes that are sensitive to being exited from a different task than they were entered. Funnelling all open/close/use through the lock-protected registry avoids "cancel scope in different task" runtime errors. This is the known sharp edge of R1's simpler model.

**Alternatives**: Dedicated per-session worker task owning the contexts (queue in / queue out) — fully sidesteps the issue; documented as the fallback if cancel-scope errors surface in integration testing.

---

## R8. File transport for chat input/output (the spec's open item)

**Decision**: Files are exchanged inline in JSON as base64. `ChatRequest.input_files: [{filename, content_b64}]`. The backend decodes them into the user's shared workspace `data/workspaces/<user_id>/` (host side), which is bind-mounted to `/work` in the container; it passes `currentDir="/work"` and `inputFiles=[filenames]` to the MCP tool. After the call, any files the tool reports in `outputFiles` (and/or new files under `/work`) are read back and returned as `output_files: [{filename, content_b64}]`.

**Rationale**: Keeps the API a "pure JSON API" (brief) with no separate upload channel for v1; the bind mount gives both the host backend and the in-container tool a shared filesystem view, which is how `currentDir`/`inputFiles`/`outputFiles` are meaningfully wired. Base64 inline is simplest for the future webapp to consume.

**Constraints**: enforce a per-request size cap (e.g., 25 MB total) to bound memory; document it. The exact shape of the tool's `outputFiles` return is confirmed against `contracts/mcp-tools.md` during implementation.

**Alternatives**: multipart upload + download URLs — rejected for v1 (more moving parts, not needed before the webapp exists); `docker cp`/`put_archive` instead of a bind mount — rejected as slower and clumsier than a shared mount.

---

## R9. Secret & key management

**Decision**: Two server-side secrets from environment (never committed): `JWT_SECRET_KEY` (HS256 signing) and `FERNET_KEY` (32-byte urlsafe base64). Loaded via `pydantic-settings` from `.env`/systemd `EnvironmentFile`. Gurobi Secret is Fernet-encrypted before insert and decrypted only in-memory at container start. Passwords use `passlib` bcrypt. Logging is configured to never include request bodies for `/signup`/`/chat` credential fields (FR-005).

**Rationale**: Directly implements FR-003/004/005. Fernet (AES-128-CBC + HMAC) is the brief's suggested symmetric scheme and is authenticated. HS256 is sufficient for a single-issuer single-host backend.

**Alternatives**: RS256/asymmetric JWT — unnecessary without a separate verifier; KMS/secret-manager — out of scope for single-VM v1, but `FERNET_KEY` rotation is noted as future work.

---

## R10. JWT policy

**Decision**: HS256, `sub=user_id`, `exp` ~60 minutes, issued at `/login`. No refresh token in v1; client re-authenticates on expiry. `get_current_user` FastAPI dependency verifies signature+expiry and loads the user; failures return 401 without detail (edge case: expired/tampered token).

**Rationale**: Matches spec Assumptions (time-limited token, no refresh in v1).

**Alternatives**: refresh-token rotation — deferred to a later feature.

---

## R11. Networking / Caddy

**Decision**: Uvicorn binds `127.0.0.1:8000`. A `Caddyfile` configures `<domain> { reverse_proxy 127.0.0.1:8000 }`, giving automatic HTTPS on `:443`. Only `:443` opened in the NSG; port 22 stays as-is. Container ports bound to loopback (R3) and never added to the NSG.

**Rationale**: Implements FR-027/FR-028 and the brief's networking diagram. Caddy talks only to `:8000`, never to a container.

**Open item**: automatic HTTPS needs a domain name resolving to `157.137.238.60`. If only the bare IP is available, use Caddy's `tls internal` (self-signed) for v1 and document it; a real domain is preferred. Captured in quickstart.

**Alternatives**: nginx + certbot — more manual cert plumbing than Caddy's built-in ACME.

---

## R12. Python runtime on the VM

**Decision**: Target Python 3.11 via a project virtual environment (`uv` or `python3.11 -m venv`), independent of the system Python 3.9.25 already present. Run under systemd as a non-root user added to the `docker` group (so the Docker SDK reaches the daemon without sudo).

**Rationale**: FastAPI/pydantic v2/modern MCP SDK are happiest on 3.11+; isolating from system 3.9 avoids breaking OS tooling. The brief notes the user must be in the `docker` group to avoid `sudo`.

**Alternatives**: system Python 3.9 — workable but risks dependency-version friction; rejected as the default.

---

## Resolved unknowns summary

| Topic | Resolution |
|-------|-----------|
| Persistent MCP session across requests | AsyncExitStack stored in registry (R1, R7) |
| Transport/URL | streamable HTTP to `127.0.0.1:<port>/api/v1/agent/mcp` (R2) |
| Container lifecycle | Docker SDK, loopback-bound published port, readiness poll (R3) |
| Port pool | in-memory 61100–61200 + DB mirror + startup reconcile (R4) |
| Idle reaping | asyncio lifespan task, default 15 min (R5) |
| Stale recovery | liveness check + single retry, no 5xx (R6) |
| File transport | base64 inline JSON over a per-user bind mount (R8) |
| Secrets | env-provided JWT + Fernet keys; never logged (R9, R10) |
| Networking | loopback app + Caddy `:443` only (R11) |
| Runtime | Python 3.11 venv, systemd, docker group (R12) |

No `NEEDS CLARIFICATION` markers remain. Two integration details are flagged as confirm-during-implementation (exact Gurobi env var names in R3; exact `outputFiles` return shape in R8) and are tracked in quickstart, not blocking.
