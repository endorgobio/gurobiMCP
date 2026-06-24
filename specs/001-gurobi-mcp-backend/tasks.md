# Tasks: Gurobi MCP Multi-User Backend

**Feature**: 001-gurobi-mcp-backend
**Input**: Design documents from `/specs/001-gurobi-mcp-backend/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓

**Organization**: Tasks grouped by user story for independent implementation and testing.
**Tests**: Not requested — no test tasks included.

## Format: `[ID] [P?] [Story?] Description with file path`

- **[P]**: Parallelizable (different files, no blocking dependencies on incomplete tasks)
- **[Story]**: User story label (US1–US4); omitted in Setup and Foundational phases

---

## Phase 1: Setup

**Purpose**: Project initialization and shared infrastructure scaffolding

- [X] T001 Create directory structure: app/, app/db/, app/schemas/, app/core/, app/services/, app/api/, tests/contract/, tests/integration/, tests/unit/, deploy/, data/workspaces/ with __init__.py files in each app/ subdirectory
- [X] T002 Create pyproject.toml (Python ≥3.11) with runtime deps (fastapi, uvicorn[standard], mcp, docker, python-jose[cryptography], passlib[bcrypt], cryptography, pydantic>=2, pydantic-settings, sqlalchemy>=2, aiosqlite) and dev deps (pytest, pytest-asyncio, httpx) plus [tool.pytest.ini_options] asyncio_mode="auto"
- [X] T003 [P] Create .env.example with keys: JWT_SECRET_KEY, FERNET_KEY, IDLE_TIMEOUT_MINUTES=15, PORT_POOL_START=61100, PORT_POOL_END=61200, DOMAIN, DB_PATH=data/app.db
- [X] T004 [P] Create .gitignore excluding data/, .env, __pycache__/, *.pyc, *.db, .venv/, venv/

**Checkpoint**: Repository scaffold complete; dependency manifest committed

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core shared infrastructure that every user story depends on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T005 Implement Config (pydantic-settings BaseSettings) loading JWT_SECRET_KEY, FERNET_KEY, IDLE_TIMEOUT_MINUTES, PORT_POOL_START, PORT_POOL_END, DOMAIN, DB_PATH from environment; export a module-level `settings` singleton in app/config.py
- [X] T006 [P] Create async SQLAlchemy engine (aiosqlite driver), AsyncSession factory, declarative Base, and async init_db() coroutine (creates all tables) in app/db/database.py
- [X] T007 [P] Create User ORM model (table `users`) with fields: id (PK autoincrement), username (UNIQUE NOT NULL), password_hash (NOT NULL), access_id (NOT NULL), encrypted_secret (NOT NULL), assigned_port (UNIQUE nullable integer), container_name (nullable text), last_used_at (datetime UTC nullable), created_at (datetime UTC NOT NULL default now) in app/db/models.py
- [X] T008 Create FastAPI app factory with lifespan context (call init_db on startup); configure a logging filter that strips request bodies for paths /signup /login /chat from log records (FR-005); add GET /healthz returning {"status":"ok"} in app/main.py

**Checkpoint**: Foundation ready — database initializes, app starts, /healthz responds

---

## Phase 3: User Story 1 — Register and sign in securely (Priority: P1) 🎯 MVP

**Goal**: Users can register with bcrypt-hashed passwords and Fernet-encrypted Gurobi secrets, sign in for a JWT, and access protected endpoints; credentials are unreadable in the DB and absent from logs.

**Independent Test**: POST /signup → 201; inspect DB row (bcrypt hash, Fernet ciphertext); POST /login correct → 200 JWT; wrong password → 401; duplicate username → 409; call protected GET /me without token → 401.

- [X] T009 [P] [US1] Implement Fernet-based encrypt_secret(plaintext: str) → str and decrypt_secret(ciphertext: str) → str using settings.FERNET_KEY in app/core/crypto.py
- [X] T010 [P] [US1] Implement hash_password(password) and verify_password(plain, hashed) (passlib bcrypt), create_access_token(user_id: int) → str (HS256 JWT, 60-min exp, sub=str(user_id)), verify_token(token) → int (returns user_id, raises 401 on invalid/expired), and get_current_user FastAPI dependency (verifies JWT, loads User from DB, raises 401 if not found) in app/core/security.py
- [X] T011 [P] [US1] Create Pydantic v2 schemas: SignupRequest (username: str min 1, password: str min 8, access_id: str, gurobi_secret: str), SignupResponse (id: int, username: str), LoginRequest (username, password), TokenResponse (access_token, token_type="bearer", expires_in: int) in app/schemas/auth.py
- [X] T012 [US1] Implement POST /signup (hash password via security, encrypt gurobi_secret via crypto, insert User → 201 SignupResponse; return 409 on IntegrityError for duplicate username) and POST /login (load user by username, verify_password → 200 TokenResponse or 401); include GET /me (protected by get_current_user, returns SignupResponse) in app/api/auth.py
- [X] T013 [US1] Register auth router (no prefix) in app/main.py via app.include_router

**Checkpoint**: User Story 1 fully functional — register, login, JWT-protected endpoint all work; credentials unreadable in DB

---

## Phase 4: User Story 2 — Continuous conversation with the optimization assistant (Priority: P1)

**Goal**: Authenticated users start a chat thread bound to one agent (gurobot/explainer/modeler), send multi-turn messages with full context continuity, receive agent responses with optional file output, and end a conversation explicitly; agent cannot change mid-thread.

**Independent Test**: POST /chat agent="modeler" new conversation_id → container starts, 200 response; follow-up on same conversation_id returns contextual reply; different conversation_id → independent context; invalid agent → 400; mismatched agent on existing thread → 400; DELETE /conversations/{id} → 204.

- [X] T014 [P] [US2] Create Pydantic v2 schemas: FilePayload (filename: str, content_b64: str), ChatRequest (conversation_id: str, agent: Literal["gurobot","explainer","modeler"], prompt: str, input_files: list[FilePayload] | None = None), ChatResponse (conversation_id: str, agent: str, response: str, output_files: list[FilePayload] = [], recovered: bool = False) in app/schemas/chat.py
- [X] T015 [P] [US2] Implement get_user_workspace(user_id: int) → Path, ensure_workspace(user_id) → Path, write_input_files(user_id, files: list[FilePayload]) → list[str] (base64-decode each file into data/workspaces/<user_id>/, reject filenames with ".." or absolute paths, return filenames), read_output_files(user_id, filenames: list[str]) → list[FilePayload] (read and base64-encode each file) in app/services/files.py
- [X] T016 [P] [US2] Implement PortPool class (asyncio.Lock, available: set[int] from PORT_POOL_START–PORT_POOL_END, in_use: dict[int, int]; acquire(user_id) → int raises PoolExhausted, release(port)) and ContainerManager class (docker.from_env(); start_container(user_id, port, access_id, secret) starts gurobi/mcp:latest named gurobimcp-{user_id} with ports={"61095/tcp": ("127.0.0.1", port)}, /work bind-mount to workspace, Gurobi env vars, labels={"app":"gurobimcp"}; stop_container(user_id); is_container_running(container_name) → bool; poll_readiness(port, timeout=20) → bool via TCP connect to /api/v1/agent/mcp) in app/services/container_manager.py
- [X] T017 [P] [US2] Implement open_mcp_session(port: int) → tuple[ClientSession, AsyncExitStack] (enters streamablehttp_client("http://127.0.0.1:{port}/api/v1/agent/mcp") then ClientSession via AsyncExitStack, calls session.initialize(), returns (session, stack)) and call_tool(session: ClientSession, agent: str, prompt: str, input_files: list[str], work_dir: str) → tuple[str, list[str]] (calls session.call_tool(agent, {"prompt":..., "inputFiles":..., "currentDir":...}), returns text response and output file list) in app/services/mcp_client.py
- [X] T018 [US2] Implement SessionEntry dataclass (agent: str, session: ClientSession, exit_stack: AsyncExitStack, container_name: str, lock: asyncio.Lock, last_used_at: datetime, recovered: bool = False) and SessionRegistry class (dict keyed by (user_id, conversation_id); get_or_create validates agent binding → raises HTTPException 400 on mismatch, opens new session via mcp_client.open_mcp_session if not present; close_session(user_id, conversation_id) acquires lock then awaits exit_stack.aclose(); close_all_for_container(container_name) closes all matching entries; all open/close under per-entry lock per R7) in app/services/session_registry.py
- [X] T019 [US2] Implement POST /chat (get_current_user dep; validate agent Literal → 400 on invalid; if user has no running container acquire port + start via ContainerManager + update User.assigned_port/container_name + poll_readiness; write input files; call session_registry.get_or_create → 400 on agent mismatch; call mcp_client.call_tool under session lock; read output files; update User.last_used_at; return ChatResponse) and DELETE /conversations/{conversation_id} (session_registry.close_session, return 204; idempotent) in app/api/chat.py
- [X] T020 [US2] Initialize module-level singletons: port_pool: PortPool, container_manager: ContainerManager, session_registry: SessionRegistry; pass them via FastAPI app.state in lifespan; register chat router (no prefix) via app.include_router in app/main.py

**Checkpoint**: User Story 2 fully functional — container starts on first /chat, multi-turn context works, agent binding enforced

---

## Phase 5: User Story 3 — Isolation and automatic resource reclamation (Priority: P2)

**Goal**: Each user's workload runs in a dedicated container seeded only with their own Gurobi credentials; idle containers are automatically stopped after IDLE_TIMEOUT_MINUTES; ports are released for reuse; no user can reach another's container, session, or workspace; pool bounded at 100 slots.

**Independent Test**: Two users active → two distinct containers on distinct 127.0.0.1-bound ports unreachable from off-host; leave user idle past threshold → container stopped, port released, DB cleared; pool exhaustion → 503.

- [X] T021 [US3] Implement async reaper_loop(app_state) background task (sleeps 60s per iteration; queries all containers with label app=gurobimcp; for each whose owning user's last_used_at exceeds IDLE_TIMEOUT_MINUTES: call session_registry.close_all_for_container, container_manager.stop_container, port_pool.release(port), clear User.assigned_port and User.container_name in DB via async session) in app/services/reaper.py
- [X] T022 [US3] Add async reconcile_port_pool(pool: PortPool, db) to app/services/container_manager.py (queries running app=gurobimcp containers, marks their assigned ports as in_use; for User rows whose container_name is not in the running set, clears assigned_port and container_name in DB to prevent stale state after restart); call it from FastAPI lifespan after init_db in app/main.py
- [X] T023 [US3] Harden app/services/files.py path construction: resolve every file path under data/workspaces/{user_id}/, raise ValueError for filenames containing ".." or starting with "/", and assert resolved path is still within the workspace root before any read or write operation
- [X] T024 [US3] Start reaper_loop as an asyncio.create_task in FastAPI lifespan (app/main.py) and cancel+await it on shutdown; in app/api/chat.py return 503 with {"detail":"no container slot available"} when PoolExhausted is caught

**Checkpoint**: User Story 3 complete — per-user container isolation verified, reaper running, pool exhaustion returns 503

---

## Phase 6: User Story 4 — Graceful recovery after reclamation (Priority: P3)

**Goal**: When a user sends a message after their container was reaped while the thread was still active, the backend transparently restarts the container, opens a fresh session reusing the same bound agent, and returns a valid response with recovered=True — never a 5xx.

**Independent Test**: Start thread, force-stop container (docker stop gurobimcp-{id}), POST /chat on same thread → 200 with recovered=true; no partial resources left allocated; bound agent unchanged.

- [X] T025 [US4] Add stale-session liveness check to SessionRegistry.get_or_create in app/services/session_registry.py: before returning a cached SessionEntry call container_manager.is_container_running(entry.container_name); if stale, best-effort close entry (swallow errors), restart container via ContainerManager, open fresh session via mcp_client.open_mcp_session reusing the same bound agent, replace the registry entry, set entry.recovered=True
- [X] T026 [US4] In app/api/chat.py wrap the mcp_client.call_tool invocation to catch transport/connection errors: on error delete the registry entry then restart container + open fresh session (same agent) + retry call_tool once; propagate recovered=True from the session entry into ChatResponse.recovered; log recovery at INFO without credentials

**Checkpoint**: User Story 4 complete — graceful recovery verified; no 5xx surfaced to caller

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Deployment artifacts, edge-case hardening, and end-to-end validation

- [X] T027 [P] Create deploy/Caddyfile configuring HTTPS on :443 with `reverse_proxy 127.0.0.1:8000`; add comment for `tls internal` fallback when DOMAIN is a bare IP (research R11)
- [X] T028 [P] Create deploy/gurobimcp.service systemd unit: ExecStart invokes uvicorn app.main:app --host 127.0.0.1 --port 8000 via project venv, EnvironmentFile=<project>/.env, User=opc, Restart=on-failure, RestartSec=5
- [X] T029 Enforce 25 MB total input-file size cap in app/api/chat.py: sum base64-decoded byte lengths of all ChatRequest.input_files before writing to workspace; return 413 with {"detail":"input files exceed 25 MB limit"} if exceeded
- [X] T030 Add 502 error handling in app/services/container_manager.py and app/api/chat.py: if poll_readiness times out after container start, stop the container, release the port, clear DB fields, and raise HTTPException 502 with {"detail":"container failed to start or credentials rejected"} so no partial resources remain allocated (FR-026)
- [X] T031 Run quickstart.md validation scenarios V1–V5 on the Oracle VM; confirm all acceptance criteria pass; update quickstart.md TODOs with verified Gurobi container env var names and outputFiles shape (research R3/R8)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — **BLOCKS all user stories**
- **User Story 1 (Phase 3)**: Depends on Phase 2 only
- **User Story 2 (Phase 4)**: Depends on Phase 2 and Phase 3 (auth needed for /chat)
- **User Story 3 (Phase 5)**: Depends on Phase 4 (reaper builds on container_manager + session_registry)
- **User Story 4 (Phase 6)**: Depends on Phase 5 (recovery builds on isolation/reaper infrastructure)
- **Polish (Phase 7)**: Depends on all prior phases

### Story Dependency Graph

```
Phase 1 (Setup)
  └─▶ Phase 2 (Foundational)
        └─▶ Phase 3 (US1: Auth)
                └─▶ Phase 4 (US2: Chat proxy)
                          └─▶ Phase 5 (US3: Isolation + Reaper)
                                    └─▶ Phase 6 (US4: Recovery)
                                                └─▶ Phase 7 (Polish)
```

### Within Each Story

- Tasks marked [P] within a phase can all be launched simultaneously
- Tasks without [P] depend on all [P] tasks in that same phase completing first
- Modify app/main.py last in each phase (T008 → T013 → T020 → T022/T024 → T026)

### Parallel Opportunities

**Phase 1**: T003, T004 in parallel after T001/T002  
**Phase 2**: T006 and T007 in parallel after T005; then T008  
**Phase 3 (US1)**: T009, T010, T011 all in parallel; then T012; then T013  
**Phase 4 (US2)**: T014, T015, T016, T017 all in parallel; then T018; then T019; then T020  
**Phase 7**: T027 and T028 in parallel with T029/T030; T031 last

---

## Parallel Example: User Story 2

```bash
# Launch simultaneously (all different files, no blocking deps):
Task T014: schemas/chat.py — FilePayload, ChatRequest, ChatResponse
Task T015: services/files.py — workspace helpers, base64 encode/decode
Task T016: services/container_manager.py — PortPool + ContainerManager
Task T017: services/mcp_client.py — open_mcp_session, call_tool

# After T014–T017 complete:
Task T018: services/session_registry.py — SessionEntry + SessionRegistry

# After T018:
Task T019: api/chat.py — POST /chat, DELETE /conversations/{id}

# After T019:
Task T020: app/main.py — wire chat router, initialize singletons
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational — **CRITICAL, blocks everything**
3. Complete Phase 3: User Story 1 — register + login working
4. Complete Phase 4: User Story 2 — chat proxy working
5. **STOP and VALIDATE**: Run quickstart.md V1 + V2 scenarios
6. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → app starts, DB initializes, /healthz responds
2. + User Story 1 → register + login working; credentials safe in DB and logs
3. + User Story 2 → multi-turn chat proxy live; agent binding enforced
4. + User Story 3 → isolation verified; idle reaper running; pool bounded
5. + User Story 4 → graceful recovery after reap; zero 5xx on recovery path
6. + Polish → production deployment on Oracle VM via systemd + Caddy

---

## Notes

- [P] tasks write to different files and carry no blocking dependencies — safe to execute concurrently
- All tasks include exact file paths matching the project structure in plan.md
- No test tasks generated — not requested in the feature specification
- **Confirm during implementation**: exact Gurobi container env var names (R3) and outputFiles return shape (R8) — see quickstart.md TODOs; document findings in T031
- **anyio discipline (R7)**: all AsyncExitStack open/close must happen from the same task; all session use serialized via per-entry asyncio.Lock
- **Credential safety (FR-005)**: never log passwords, gurobi_secret, JWT tokens, or FERNET_KEY — enforced at logging layer (T008) and verified in T031 V1
