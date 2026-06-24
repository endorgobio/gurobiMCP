# Feature Specification: Gurobi MCP Multi-User Backend

**Feature Branch**: `001-gurobi-mcp-backend`

**Created**: 2026-06-24

**Status**: Draft

**Input**: User description: "consider the attached document to develop a Gurobi MCP Multi-User Backend"

## Clarifications

### Session 2026-06-24

- Q: Should the assistant (agent) be selectable per message or fixed for a conversation thread? → A: Each `/chat` request carries an `agent` field (`gurobot` | `explainer` | `modeler`); the agent is fixed when a `conversation_id` is first created and stored with the session. Invalid enum values are rejected with 400. A follow-up request whose `agent` differs from the one stored for that `conversation_id` is rejected with 400 — the agent cannot change mid-conversation, because each agent tracks its own distinct workflow state on the Gurobi side.

## User Scenarios & Testing *(mandatory)*

The backend is a headless JSON service. Its "users" are people who interact through a future web app (built separately), plus the operator who runs and maintains the service. Each story below is a standalone slice that can be developed, tested, and demonstrated on its own.

### User Story 1 - Register and sign in securely (Priority: P1)

A new person creates an account by providing a username, a password, and their own Gurobi Intelligence Access ID and Secret. They can then sign in and receive a credential that authorizes every later request. Their Gurobi credentials are stored so that only the service can use them on their behalf, and are never visible to anyone — including operators reading the data store or logs.

**Why this priority**: No other capability is reachable without an identity and an authorization token, and the safe handling of each person's private Gurobi credentials is the central trust promise of the whole service. This is the smallest slice that delivers standalone, demonstrable value (a working, secure account system).

**Independent Test**: Register a new account, confirm sign-in returns a valid authorization token, confirm the same token is accepted by a protected endpoint, and confirm the stored Gurobi credentials are unreadable in the data store and absent from logs.

**Acceptance Scenarios**:

1. **Given** no existing account for a username, **When** the person registers with a username, password, and Gurobi Access ID + Secret, **Then** the account is created and the password and Gurobi Secret are stored in non-readable form.
2. **Given** a registered account, **When** the person signs in with the correct username and password, **Then** they receive a time-limited authorization token.
3. **Given** a registered account, **When** the person signs in with an incorrect password, **Then** access is refused and no token is issued.
4. **Given** a username that is already registered, **When** another registration uses the same username, **Then** the request is rejected.
5. **Given** any protected endpoint, **When** a request arrives without a valid authorization token, **Then** the request is refused.

---

### User Story 2 - Hold a continuous conversation with the optimization assistant (Priority: P1)

A signed-in person starts a chat thread and chooses one agent for it (`gurobot`, `explainer`, or `modeler`); that choice is fixed for the life of the thread. The explainer and modeler are multi-turn: they may ask clarifying questions and expect the person's next message to continue the same thread. The person sends follow-up messages on the same thread — reusing the same agent — and the agent responds with full awareness of the earlier exchange. Responses can include files produced by the agent.

**Why this priority**: This is the core value of the product — letting each person use the Gurobi optimization assistants conversationally with their own quota. A single, non-continuous request would break the explainer and modeler, so conversation continuity is essential rather than optional.

**Independent Test**: From an authenticated session, start a thread bound to one agent, send a message that triggers a clarifying question, send a follow-up on the same thread with the same agent, and confirm the agent's reply reflects the earlier turn rather than starting over; also confirm a follow-up naming a different agent is rejected.

**Acceptance Scenarios**:

1. **Given** an authenticated person with a new chat thread, **When** they send a first message naming one of the three agents in the `agent` field, **Then** that agent is bound to the thread and they receive that agent's response.
2. **Given** an active thread where the agent asked a clarifying question, **When** the person sends a follow-up message on the same thread with the same `agent`, **Then** the agent continues the same context instead of restarting.
3. **Given** a request that includes input files, **When** the agent produces output files, **Then** the response returns those output files to the caller.
4. **Given** two different chat threads owned by the same person, **When** messages are sent to each, **Then** each thread maintains its own independent context and its own bound agent.
5. **Given** a person explicitly ends a conversation, **When** they later start a new thread, **Then** the new thread begins with fresh context and may bind any agent.
6. **Given** a chat request whose `agent` value is not one of the three supported agents, **When** it is received, **Then** it is rejected with a 400 error before any agent is contacted.
7. **Given** a thread already bound to one agent, **When** a follow-up request for that same `conversation_id` specifies a different agent, **Then** it is rejected with a 400 error and the bound agent is unchanged.

---

### User Story 3 - Isolation and automatic resource reclamation (Priority: P2)

Each person's optimization workload runs in an isolated environment tied to their own Gurobi credentials and quota, separate from every other person. When a person stops interacting, their environment is automatically shut down after a configurable idle period so that resources are freed and their Gurobi quota is not held "live" while unused. Activity resets the idle timer.

**Why this priority**: Isolation protects each person's credentials, data, and quota from every other person, and automatic reclamation keeps the shared machine and per-person quotas from being consumed by idle sessions. It is essential for safe multi-user operation but builds on Stories 1 and 2.

**Independent Test**: Drive activity for one account, confirm a dedicated isolated environment is running for it; leave it idle beyond the configured threshold, confirm the environment is shut down; confirm a second account's environment and data are never reachable from the first.

**Acceptance Scenarios**:

1. **Given** an authenticated person sends their first message, **When** no environment is currently running for them, **Then** a dedicated environment is started using only that person's own Gurobi credentials.
2. **Given** a person with a running environment, **When** any interaction occurs, **Then** their "last used" time is updated.
3. **Given** a person whose environment has been idle beyond the configured threshold, **When** the reclamation check runs, **Then** their environment is stopped and its allocated resources are released for reuse.
4. **Given** two different people are both active, **When** each interacts, **Then** neither can observe the other's conversations, files, credentials, or quota.

---

### User Story 4 - Graceful recovery after reclamation (Priority: P3)

A person returns to a chat thread after their environment was automatically shut down for being idle. Their next message transparently brings a fresh environment back up and continues the thread. The previously in-progress multi-turn state is lost, but the person experiences automatic recovery rather than an error.

**Why this priority**: This hardens the idle-reclamation design (Story 3) so that the inevitable case of "user returns after timeout" degrades gracefully. It is valuable polish but not required for a first demonstrable system.

**Independent Test**: Start a thread, force the environment to be reclaimed, send another message on the same thread, and confirm the system recovers automatically and returns a valid response instead of failing.

**Acceptance Scenarios**:

1. **Given** a thread whose underlying environment was reclaimed while the thread was still considered active, **When** the next message arrives for that thread, **Then** the system detects the stale state, brings the environment back up, and returns a valid response.
2. **Given** such a recovery occurs, **When** the response is returned, **Then** the caller is not shown an internal error, even though earlier multi-turn context for that thread is no longer available.

---

### Edge Cases

- **Resource pool exhaustion**: What happens when every allocatable slot for per-user environments is already in use and another person becomes active?
- **Concurrent messages on one environment**: How does the system behave when two requests for the same person/environment arrive at once, given an environment is expected to handle one active conversation at a time?
- **Service restart**: Conversation continuity is held only for the lifetime of the running service; after a restart, active threads lose their in-progress context. The next message must recover gracefully (as in Story 4).
- **Invalid Gurobi credentials**: How is the person informed when their stored Gurobi credentials are rejected by the upstream Gurobi service when their environment starts?
- **Expired or tampered authorization token**: A request with an expired or invalid token must be refused without revealing why in a way that aids an attacker.
- **Environment fails to start**: If a person's environment cannot be started, the person receives a clear failure response and no partial/abandoned resources are left allocated.
- **Unknown agent requested**: A chat request whose `agent` value is not one of the three supported agents is rejected with a 400 error before any agent is contacted.
- **Agent change mid-conversation**: A follow-up request for an existing `conversation_id` that names a different agent than the one already bound to that thread is rejected with a 400 error; the originally bound agent is preserved.

## Requirements *(mandatory)*

### Functional Requirements

**Authentication & credential safety**

- **FR-001**: System MUST allow a new person to register with a username, a password, and their own Gurobi Intelligence Access ID and Secret.
- **FR-002**: System MUST reject registration when the chosen username already exists.
- **FR-003**: System MUST store passwords only in a non-reversible (one-way hashed) form and MUST never store or return them in readable form.
- **FR-004**: System MUST store each person's Gurobi Access ID and Secret encrypted at rest, decryptable only by the service using a key held server-side, and MUST never store or expose them in readable form.
- **FR-005**: System MUST never write Gurobi credentials, passwords, or authorization tokens to logs.
- **FR-006**: System MUST issue a time-limited authorization token upon successful sign-in and refuse sign-in for invalid credentials.
- **FR-007**: System MUST require a valid authorization token on every endpoint other than registration and sign-in, and MUST refuse requests lacking one.

**Conversational chat proxy**

- **FR-008**: System MUST require every chat request to carry an `agent` field naming exactly one of three supported agents — `gurobot`, `explainer`, and `modeler` — and MUST return that agent's response.
- **FR-009**: System MUST accept a caller-supplied conversation identifier on every chat request that groups messages into a single continuing thread.
- **FR-010**: System MUST continue an existing thread's context when a follow-up message arrives for a conversation that already has active context, rather than starting a new exchange.
- **FR-011**: System MUST begin a fresh context the first time it sees a given conversation identifier for a person.
- **FR-012**: System MUST keep the context of distinct conversation threads independent from one another, including multiple threads owned by the same person.
- **FR-013**: System MUST accept optional input files with a chat message and MUST return any files produced by the agent in the response.
- **FR-014**: System MUST allow a person to explicitly end a conversation, releasing its retained context.
- **FR-015**: System MUST reject, with a 400 error and before contacting any agent, a chat request whose `agent` value is missing or not one of the three supported agents.
- **FR-029**: System MUST bind the chosen agent to a conversation thread the first time it sees that conversation identifier, and MUST store the bound agent alongside that thread's session for the thread's lifetime.
- **FR-030**: System MUST reject, with a 400 error, a follow-up chat request for an existing conversation identifier whose `agent` value differs from the agent already bound to that thread, leaving the bound agent unchanged — an agent cannot be changed mid-conversation because each agent tracks its own distinct workflow state on the Gurobi side.

**Per-user isolation & resource lifecycle**

- **FR-016**: System MUST run each person's optimization workload in an environment dedicated to that person and started with only that person's own decrypted Gurobi credentials.
- **FR-017**: System MUST ensure a person's environment is running before their message is processed, starting one on demand if none is running.
- **FR-018**: System MUST allocate each environment a slot from a fixed, bounded pool of resources and MUST release that slot when the environment stops, making it available for reuse.
- **FR-019**: System MUST record the time of each person's most recent interaction.
- **FR-020**: System MUST automatically stop environments that have been idle longer than a configurable threshold, and MUST release their resources.
- **FR-021**: System MUST ensure that one person can never access another person's conversations, files, credentials, or Gurobi quota.
- **FR-022**: System MUST tie a thread's retained context to the lifetime of its environment, so that context is released when its environment is stopped.

**Resilience**

- **FR-023**: When a message arrives for a thread whose environment was stopped while the thread was still considered active, the System MUST detect the stale state, restart the environment, establish fresh context, and return a valid response instead of an error — accepting the loss of prior in-progress context.
- **FR-024**: System MUST treat one environment as handling a single active conversation at a time, either by serializing concurrent requests to the same environment or by limiting an environment to one active conversation as a documented v1 limitation.
- **FR-025**: System MUST recover gracefully on the first message after a service restart, even though in-progress thread context held only for the running service's lifetime is lost.
- **FR-026**: System MUST return a clear, non-internal error to the caller when a person's environment cannot be started or their Gurobi credentials are rejected upstream, without leaving partial resources allocated.

**Exposure & access boundary**

- **FR-027**: System MUST expose only a single secure (HTTPS) entry point to the outside world; the application service and all per-user environments MUST NOT be reachable from outside the host.
- **FR-028**: The future web app MUST be able to operate using only the registration, sign-in, and chat capabilities, without any direct access to environments, internal ports, or Gurobi credentials.

### Key Entities *(include if feature involves data)*

- **User account**: Represents a registered person. Attributes: unique identifier, username, non-reversible password representation, Gurobi Access ID, encrypted Gurobi Secret, currently allocated resource slot (if any), name of their dedicated environment (if any), and time of last interaction.
- **Authorization token**: A time-limited credential issued at sign-in that identifies the person on every subsequent request.
- **Conversation thread**: A continuing exchange identified by a caller-supplied identifier, owned by one person, bound to exactly one agent (`gurobot` | `explainer` | `modeler`) chosen at creation and fixed thereafter, and carrying its own context for the duration of its environment's life.
- **Agent**: One of three supported workflows — `gurobot`, `explainer`, `modeler` — each tracking its own distinct state on the Gurobi side; selected per conversation thread and not interchangeable mid-thread.
- **User environment**: A per-person isolated runtime that holds that person's Gurobi credentials and serves their optimization requests; created on demand, reclaimed when idle, and occupying one slot from a bounded pool.
- **Active-session registry**: The service's record of which conversation threads currently have live context, which agent each thread is bound to, and which environment each is bound to.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new person can go from registration to receiving their first assistant response in a single sitting without manual operator involvement.
- **SC-002**: A follow-up message within an active conversation receives a response that demonstrably reflects the prior turn in at least 99% of cases where the environment is still live.
- **SC-003**: When a returning person sends a message after their environment was reclaimed, the system recovers automatically and returns a valid response in 100% of cases, with zero internal-error responses surfaced to the caller.
- **SC-004**: Idle environments are stopped within the configured idle threshold (default 15 minutes) in 100% of cases, so no person's Gurobi quota is held live while they are inactive.
- **SC-005**: No person can ever read, reuse, or affect another person's conversations, files, credentials, or quota — verified to hold in 100% of isolation tests.
- **SC-006**: Stored Gurobi secrets and passwords are unreadable in the data store and never appear in any log line — verified across a full inspection of stored data and logs.
- **SC-007**: Only the single secure external entry point is reachable from outside the host; every attempt to reach the application service or a per-user environment directly from outside fails in 100% of probes.
- **SC-008**: The number of simultaneously running per-user environments never exceeds the bounded pool size, and freed slots become reusable for new active users.

## Assumptions

- **Single-host deployment**: The service runs on the existing single Oracle Linux VM described in the brief; horizontal scaling across multiple hosts is out of scope for v1.
- **Container-based environments**: Each per-user environment is realized as one container per active user from the provided `gurobi/mcp` image, named per user, drawn from a fixed port pool (e.g., 61100–61200). The application talks to containers via the platform's container management interface rather than ad-hoc shell calls.
- **Technology direction from the brief**: The service is implemented as a single backend JSON API; passwords use bcrypt hashing, Gurobi secrets use symmetric encryption at rest (e.g., Fernet), authorization uses JWTs, and persistence starts on SQLite. These are accepted defaults from the brief and will be confirmed during planning.
- **In-memory session registry for v1**: Live conversation context is held in process memory only; it is intentionally lost on service restart, documented as a known v1 limitation, with graceful recovery on the next message.
- **Open self-service registration**: Anyone who can reach the service may register; invite-only or operator-approved onboarding is out of scope for v1.
- **Token policy**: Authorization tokens are time-limited; token refresh/rotation is out of scope for v1 and a person re-signs in when a token expires.
- **One active conversation per environment for v1**: Where serialization is not in place, an environment serves one active conversation at a time; additional simultaneous conversations per person are a documented v1 limitation.
- **Edge/TLS termination is external to the application**: A reverse proxy in front of the service terminates HTTPS and is the only externally exposed port; the application itself listens only on the local host interface.
- **File transport**: Input and output files accompanying chat messages are exchanged through the JSON API; the precise encoding (e.g., inline-encoded content vs. references) will be settled during planning and does not change the externally observable behavior described here.
- **The web app is out of scope**: Only the backend is built here; the chat UI is a separate, later project that consumes registration, sign-in, and chat.
