# Quickstart & Validation Guide: Gurobi MCP Multi-User Backend

How to set up, run, and validate the backend end-to-end on the Oracle VM. This is a run/validation guide — implementation lives in `tasks.md` and the code.

## Prerequisites

- Oracle Linux 9 VM (`opc@157.137.238.60`), Docker installed, `gurobi/mcp:latest` pulled (already done).
- Current user in the `docker` group (so the Docker SDK reaches the daemon without sudo):
  `sudo usermod -aG docker $USER` then re-login. Verify: `docker ps` works without sudo.
- Python 3.11 available (via `uv` or `python3.11`); system Python 3.9 left untouched (research R12).
- A valid Gurobi Intelligence **Access ID + Secret** for at least one test account.

## Setup

1. **Virtualenv + deps**
   - Create a 3.11 venv under the project and install from `requirements.txt`/`pyproject.toml`.
2. **Secrets** — copy `.env.example` to `.env` and set:
   - `JWT_SECRET_KEY` — random 32+ byte string
   - `FERNET_KEY` — generate once: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `IDLE_TIMEOUT_MINUTES=15`, `PORT_POOL_START=61100`, `PORT_POOL_END=61200`
   - `DOMAIN` — the DNS name pointing at the VM (or leave blank to use Caddy `tls internal` for v1)
3. **Data dir** — ensure `data/` and `data/workspaces/` exist and are writable (gitignored).

## Run (development)

- Start the API on loopback: `uvicorn app.main:app --host 127.0.0.1 --port 8000`
- The lifespan startup must: init SQLite, reconcile the port pool against running `app=gurobimcp` containers, and start the reaper task.

## Run (production on the VM)

1. Install the systemd unit `deploy/gurobimcp.service` (runs uvicorn on `127.0.0.1:8000` as a non-root docker-group user). `systemctl enable --now gurobimcp`.
2. Install Caddy, drop in `deploy/Caddyfile` (`reverse_proxy 127.0.0.1:8000`), start Caddy.
3. **Open port 443** in the NSG `ig-quick-action-NSG` (and only 443). Leave 22 as-is. Never open 61100–61200.

## Validation scenarios

Each maps to a user story / success criterion. Run against the running API (use the `DOMAIN` over HTTPS, or `127.0.0.1:8000` on the VM for local checks).

### V1 — Secure signup & login (User Story 1 / SC-006)
1. `POST /signup` with username, password, access_id, gurobi_secret → expect `201`.
2. Inspect `data/app.db` `users` row → `password_hash` is a bcrypt hash, `encrypted_secret` is unreadable ciphertext, no plaintext secret anywhere.
3. `grep` the logs for the secret/password values → **zero** matches (FR-005).
4. `POST /login` correct creds → `200` + JWT. Wrong password → `401`. Duplicate signup → `409`.
5. Call `/chat` with no/elapsed token → `401`.

### V2 — Multi-turn conversation (User Story 2 / SC-002)
1. `POST /chat` `{conversation_id: "c1", agent: "modeler", prompt: "..."}` with a fresh token → `200`, container `gurobimcp-<id>` now running (`docker ps`), a host port in 61100–61200 bound to `127.0.0.1`.
2. Send a follow-up `POST /chat` same `conversation_id: "c1"`, same `agent: "modeler"` → response reflects prior turn (not a restart).
3. `POST /chat` with `conversation_id: "c2"`, `agent: "explainer"` → independent context and its own bound agent (FR-012).
4. Send `input_files` and confirm `output_files` returned base64 round-trips (FR-013).
5. Invalid/missing `agent` (e.g. `"solver"`) → `400` (FR-015).
6. Follow-up on `c1` with a different `agent` (e.g. `"gurobot"`) → `400`, binding unchanged (FR-030).

### V3 — Isolation & reaper (User Story 3 / SC-004, SC-005, SC-008)
1. Two users active simultaneously → two distinct containers, two distinct ports; confirm neither port is reachable from off-host (`curl` from your laptop to `157.137.238.60:<port>` fails).
2. Leave a user idle > `IDLE_TIMEOUT_MINUTES` → reaper stops/removes their container, releases the port, clears `assigned_port`/`container_name` (`docker ps` + DB row).
3. Confirm running-container count never exceeds pool size; exhaust the pool to see `503`.

### V4 — Graceful recovery (User Story 4 / SC-003)
1. Start conversation `c1`, then force-reap (lower the timeout or `docker stop gurobimcp-<id>`).
2. `POST /chat` again on `c1` → response is `200` with `"recovered": true`; **no** 5xx (FR-023).

### V5 — Network boundary (SC-007)
1. From off-host: `https://<domain>/` works; `http://157.137.238.60:8000` and `:<container port>` both fail/refused.
2. NSG shows only 22 and 443 inbound.

## Known v1 limitations (validate they degrade gracefully, not silently)
- In-memory session registry: restarting the backend drops live contexts; next message recovers (V4-style) (FR-025).
- One active conversation per container, serialized (FR-024).
- `tls internal` self-signed certs if no domain is configured (research R11).

## Confirmed implementation details (from Phase 4 VM testing, 2026-06-24)

**Gurobi container env var names (R3)** — confirmed against `gurobi/mcp:latest` on the Oracle VM:
- `GRB_INTELLIGENCE_ACCESS_ID` — Gurobi Intelligence Hub Access ID (UUID)
- `GRB_INTELLIGENCE_SECRET` — Gurobi Intelligence Hub Secret (UUID)
- `GRB_MCP_MOUNT=/workspace` — container-side workspace mount path
- Volume bind: `data/workspaces/<user_id>` → `/workspace` (rw)
- Container port: `61095/tcp` internally; mapped to `127.0.0.1:<pool_port>` on the host

**MCP URL**: `http://127.0.0.1:<port>/api/v1/agent/mcp`

**outputFiles shape (R8)** — `call_tool` returns `result.content` as a list of `TextContent` objects.
The text response is the primary output.  `outputFiles` appears in `content.data["outputFiles"]`
as a list of filename strings (relative paths within the workspace), if the tool writes files.
The backend reads those filenames from the workspace bind-mount and base64-encodes them for the response.

**`notifications/progress` warnings**: the Gurobi MCP server sends `notifications/progress` events
with `progressToken: null`, which the MCP Python SDK cannot parse against its union discriminator.
These produce `WARNING root: Failed to validate notification` log lines but are silently discarded
and do not affect tool-call results.  No fix needed for v1.
