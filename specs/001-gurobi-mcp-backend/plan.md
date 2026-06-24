# Implementation Plan: Gurobi MCP Multi-User Backend

**Branch**: `001-gurobi-mcp-backend` | **Date**: 2026-06-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-gurobi-mcp-backend/spec.md`

## Summary

A single FastAPI JSON backend that lets multiple users each run their own isolated `gurobi/mcp` container against their own Gurobi Intelligence Hub credentials. Users register (storing bcrypt-hashed passwords and Fernet-encrypted Gurobi secrets), sign in for a JWT, and chat with three MCP tools (`gurobot`, `explainer`, `modeler`) over multi-turn conversations. The backend manages one ephemeral container per active user from a bounded port pool, keeps a live MCP `ClientSession` per `(user_id, conversation_id)`, reaps idle containers/sessions on a background timer, and recovers gracefully when a session's container has been reaped. The app binds to `127.0.0.1:8000`; Caddy terminates HTTPS on `:443` as the only externally exposed port.

## Technical Context

**Language/Version**: Python 3.11+ (3.9 is present on the VM; plan targets 3.11 via a managed venv/uv — confirmed in research)

**Primary Dependencies**: FastAPI + Uvicorn (ASGI), `mcp` (official MCP Python SDK, `streamablehttp_client` + `ClientSession`), `docker` (Docker Python SDK), `python-jose[cryptography]` or `PyJWT` (JWT), `passlib[bcrypt]` (password hashing), `cryptography` (Fernet), `pydantic` v2 / `pydantic-settings` (config), SQLAlchemy 2.x (SQLite access)

**Storage**: SQLite (single file, `data/app.db`) via SQLAlchemy; per-user file workspaces on disk bind-mounted into containers

**Testing**: pytest + pytest-asyncio; httpx ASGI transport for API tests; Docker SDK and MCP session mocked/faked at the boundary for unit tests, with an opt-in integration suite that exercises a real container

**Target Platform**: Single Oracle Linux 9 VM (AMD), Docker installed system-wide, `gurobi/mcp:latest` already pulled

**Project Type**: Single backend web service (no frontend in this feature)

**Performance Goals**: Interactive chat — follow-up message round-trips bounded by upstream Gurobi/MCP latency (target backend overhead < 200 ms excluding model/solver time); cold start (container launch + MCP session open) target < 20 s

**Constraints**: App listens on loopback only (`127.0.0.1:8000`); only Caddy `:443` exposed in the NSG; per-user container ports `61100–61200` never exposed externally; credentials never logged; one active MCP session per container (serialized)

**Scale/Scope**: Single host; bounded pool of ~100 concurrent per-user containers (ports 61100–61200); tens of registered users for v1; in-memory session registry (lost on restart, by design)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The project constitution at `.specify/memory/constitution.md` is still the unpopulated template — it contains only placeholder principles and no ratified rules. There are therefore **no enforceable governance gates** for this feature.

- **Initial gate**: PASS (vacuously — no ratified principles to violate).
- **Post-design re-check**: PASS (see end of Phase 1).

Recommendation (non-blocking): run `/speckit-constitution` to ratify project principles (e.g., security-first credential handling, test coverage for the auth and session-recovery paths) so future features inherit real gates. This plan already adopts those as design defaults.

## Project Structure

### Documentation (this feature)

```text
specs/001-gurobi-mcp-backend/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── openapi.yaml     # REST contract: /signup, /login, /chat, /conversations/{id}
│   └── mcp-tools.md     # Upstream MCP tool contract (gurobot/explainer/modeler)
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
app/
├── main.py                  # FastAPI app factory, lifespan (startup/shutdown, reaper task), router wiring
├── config.py                # pydantic-settings: secrets, port pool, idle timeout, paths
├── db/
│   ├── database.py          # SQLAlchemy engine/session, init
│   └── models.py            # ORM model: User
├── schemas/
│   ├── auth.py              # SignupRequest, LoginRequest, TokenResponse
│   └── chat.py              # ChatRequest, ChatResponse, FilePayload
├── core/
│   ├── security.py          # bcrypt hashing, JWT encode/verify, get_current_user dependency
│   └── crypto.py            # Fernet encrypt/decrypt for Gurobi secret
├── services/
│   ├── container_manager.py # Docker SDK: start/stop/status, port pool, readiness wait
│   ├── session_registry.py  # (user_id, conversation_id) -> {bound agent, live MCP session}; AsyncExitStack + per-session lock; enforces agent immutability
│   ├── mcp_client.py        # open ClientSession over streamable HTTP, call tool, map files
│   ├── reaper.py            # background loop: stop idle containers, close their sessions
│   └── files.py             # per-user workspace, base64 <-> file on shared bind mount
├── api/
│   ├── auth.py              # POST /signup, POST /login
│   └── chat.py              # POST /chat, DELETE /conversations/{conversation_id}
└── __init__.py

tests/
├── contract/                # OpenAPI request/response shape tests per endpoint
├── integration/             # end-to-end user-story flows (auth, multi-turn, reaper, recovery)
└── unit/                    # crypto, security, port pool, session registry, files

deploy/
├── Caddyfile                # :443 TLS termination → 127.0.0.1:8000
└── gurobimcp.service        # systemd unit for the backend

data/                        # SQLite db + per-user workspaces (gitignored)
.env.example                 # SECRET_KEY (JWT), FERNET_KEY, IDLE_TIMEOUT_MINUTES, PORT_POOL, DOMAIN
requirements.txt / pyproject.toml
```

**Structure Decision**: Single backend web service (matches "Project Type: web-service" with no frontend in scope). Code lives under `app/` organized by layer (api → services → db), with `services/` holding the three subsystems that carry the real risk: the container manager, the MCP session registry, and the reaper. Deployment artifacts (Caddy, systemd) live under `deploy/`.

## Complexity Tracking

> No constitution violations to justify (no ratified principles). Table omitted.
