# AI Support Ticket Decision Gate Build Plan

Use `.github/prompts/implementation-spec.md` as the authoritative product specification. Implement the PoC in eight strictly gated phases. A phase may start only after its automated tests, architecture boundary verification, manual checkpoint, and acceptance criteria pass. Architecture boundaries are release blockers, not preferences.

**Phase exit rule:** The **Acceptance Criteria** section in every phase is also that phase's explicit **Exit Criteria**. A phase cannot advance until every acceptance item passes.


## Architecture Invariants

These rules are non-negotiable and apply to every phase and pull request:

1. **The Durable orchestrator contains pure coordination logic and no I/O or side effects.** It may only call activities, aggregate activity results, wait for external events, wait on durable timers, and manage workflow state transitions/custom status. It must not import or use `sqlite3`, `app.db`, repositories, Foundry clients, HTTP clients, filesystem APIs, environment/config readers, random/UUID generators, system clock APIs, or direct business-action implementations. Durable time comes only from `context.current_utc_datetime`. Runtime configuration required by orchestration, including approval timeout, is resolved by an HTTP entry point and included in validated orchestration input.
2. **SQLite is accessible only from activity functions and HTTP API functions.** UI code, orchestrator code, Foundry client code, recommendation code, and other domain modules must not open a connection, import `sqlite3`, or import `app.db`. Database operations are exposed through focused persistence activities for workflow writes and repository calls from HTTP handlers for query/read needs.
3. **Microsoft Foundry is accessible only from Durable activity functions.** The required flow is `Orchestrator -> Agent Activity -> Foundry -> Pydantic validation in activity -> validated activity result -> Orchestrator aggregation`. The orchestrator must not instantiate `AIProjectClient` or OpenAI clients, call agents, parse model responses, read the playbook, or handle Foundry credentials/configuration.
4. **Human decisions enter the workflow only through Durable external events.** The orchestrator uses `wait_for_external_event("ApprovalDecision")` and a durable timer. The approval API validates the request, performs only the minimum read needed to resolve the orchestration instance and eligibility, and raises the event. It must not write approval records, update ticket/workflow status, execute rejection/approval behavior, or assign a ticket. Activities validate and persist the event after the orchestrator receives it.
5. **Assignment executes only through the dedicated Durable activity named `AssignTicketActivity`.** The orchestrator invokes `context.call_activity_with_retry("AssignTicketActivity", retry_options, payload)`. The activity exclusively owns assignment execution, idempotency checks, persistence updates, and assignment audit logging. It uses stable idempotency key `assign:<ticket_id>`.
6. **The web UI communicates exclusively through HTTP APIs.** The required flow is `UI -> HTTP API -> activity/repository boundary -> SQLite`. Browser code must not open/read SQLite, read database files/local data stores, access Foundry, invoke Durable storage directly, or bypass API endpoints.
7. **Any PR violating an invariant fails review**, even if functional tests pass. Boundary tests are mandatory CI/review gates and may not be skipped, weakened, or replaced by comments.

## Enforcement And Review Strategy

Create `tests/test_architecture_boundaries.py` as a permanent automated architecture test suite. It parses Python and JavaScript source using AST/token-based checks rather than fragile broad substring matching and fails when:

- `app/durable_blueprint.py` imports `sqlite3`, `app.db`, repository modules, `app.foundry_client`, Azure AI/OpenAI clients, HTTP/network libraries, filesystem helpers, `os`, config/environment readers, UUID/random APIs, or business-action modules.
- The orchestrator calls anything outside the approved Durable coordination API allowlist: activity calls, `task_all`, `task_any`, `wait_for_external_event`, `create_timer`, task cancellation, `set_custom_status`, `current_utc_datetime`, and local data aggregation/state branching.
- Any module other than `app/activities_blueprint.py`, `app/api_blueprint.py`, or `app/db.py` imports `sqlite3` or `app.db`; `app/db.py` is the SQL implementation and may be imported only by activity/API modules.
- Any module other than `app/activities_blueprint.py` or `app/foundry_client.py` imports Foundry/OpenAI/Azure identity clients; `app.foundry_client.py` is a leaf adapter invoked only by activities and must not import persistence.
- UI JavaScript contains database/file/local-store access, Foundry SDK usage, or non-HTTP application data access. All user actions must map to declared `/api/...` endpoints.
- Assignment implementation or assignment persistence appears outside `AssignTicketActivity`/the database repository it calls.

Every PR checklist must answer yes to all of the following:

- Orchestrator diff is coordination-only and uses no forbidden imports, configuration reads, parsing, I/O, side effects, or business execution.
- Every workflow database write is performed by a persistence/business activity; HTTP handlers use the repository only for API reads and expressly permitted request lookup.
- Foundry invocation and model-response parsing/validation remain inside an agent activity boundary.
- Approval API raises `ApprovalDecision` without mutating workflow or approval state.
- Assignment is reachable only through `AssignTicketActivity` and retains `assign:<ticket_id>` idempotency.
- Every UI action uses a documented HTTP endpoint.
- Boundary tests, phase tests, and full regression tests pass.

## Phase 1: Repository Scaffolding And Configuration

**Files to create or modify**
- `function_app.py`, `host.json`, `requirements.txt`, `pyproject.toml`, `local.settings.json.example`, `docker-compose.yml`, `.gitignore`, `.funcignore`.
- `app/__init__.py`, `app/config.py`, `data/.gitkeep`.
- `tests/__init__.py`, `tests/test_scaffolding.py`, `tests/test_architecture_boundaries.py`.

**Functionality delivered**
- Installable Python 3.11 Azure Functions v4 project with one `df.DFApp`, validated environment-backed configuration, Azurite, and optional Durable Task Scheduler.
- An architecture test harness and import allowlists exist before workflow code is introduced.
- `app/config.py` is an edge-layer configuration module for HTTP/activity use only; the future orchestrator is explicitly forbidden from importing it.

**Dependencies**
- Python 3.11, Functions Core Tools v4, Docker/Compose, Azurite image access.
- No Foundry resource or credential is required.

**Tests to run**
1. Install `requirements.txt` in a clean virtual environment.
2. Run `python -m pytest tests/test_scaffolding.py tests/test_architecture_boundaries.py`.
3. Validate JSON/Compose manifests and import `function_app` without indexing errors.

**Manual validation checkpoint**
- Copy example settings to ignored local settings, start containers and `func start`, and inspect the project layout against the PR boundary checklist.

**Architecture Boundary Verification**
- Boundary test starts with deny lists and approved module ownership rules, including the orchestrator/config prohibition, SQLite ownership, Foundry leaf-adapter ownership, and UI/API-only rule.
- Review confirms no empty placeholder modules are used to evade architecture checks.

**Failure Scenarios**
- Missing/malformed environment values fail at HTTP/activity startup or feature invocation, never during orchestration replay.
- Incompatible Durable packages or host configuration prevent phase exit.

**Anti-Pattern Examples**
- Importing `app.config` or calling `os.getenv()` from a future orchestrator.
- Creating a shared service locator that gives every module database or Foundry access.
- Adding empty TODO activity modules just to satisfy the planned structure.

**Acceptance Criteria**
- Function host starts, emulator configuration is valid, required settings contain no secrets, architecture tests execute successfully, and the PR checklist is documented.

**Risks or assumptions**
- Local tools are installed. Stable Durable 1.x and Functions versions must be pinned as a tested set.

## Phase 2: Pydantic Models And SQLite Persistence

**Files to create or modify**
- `app/models.py`, `app/db.py`, `sql/schema.sql`, `app/config.py`.
- `tests/conftest.py`, `tests/test_models.py`, `tests/test_db.py`, `tests/test_architecture_boundaries.py`.

**Functionality delivered**
- Strict Pydantic v2 contracts for HTTP input/output, orchestration input, all agent outputs, recommendation, complete approval event, persisted records, and sanitized errors.
- `app/db.py` is the sole SQL implementation, using short-lived connections, transactions, WAL, foreign keys, `busy_timeout`, parameterized SQL, UPSERTs, and stable dedupe keys.
- Repository APIs accept IDs/timestamps from allowed callers and do not generate orchestration data.

**Dependencies**
- Phase 1; built-in `sqlite3`; Pydantic v2.

**Tests to run**
1. Run model validation/round-trip tests and database schema, CRUD, rollback, ordering, injection-shaped input, concurrency, and idempotency tests.
2. Initialize the schema twice against a disposable database.
3. Run architecture boundary tests and the full regression suite.

**Manual validation checkpoint**
- Inspect `sqlite_master`, insert/read SQL-like text safely, repeat logical writes without duplicates, and inspect imports of every persistence consumer.

**Architecture Boundary Verification**
- AST tests prove only `app/db.py` imports `sqlite3`; only future activities/APIs may import `app.db`.
- `app/models.py` and config/domain modules cannot import persistence.
- Code review confirms no global long-lived SQLite connection and no SQL outside `app/db.py`.

**Failure Scenarios**
- Locked database respects `busy_timeout` and rolls back safely.
- Invalid foreign keys/check constraints fail without partial writes.
- Duplicate ticket, agent, approval, and audit writes resolve idempotently.

**Anti-Pattern Examples**
- Importing `sqlite3` in the orchestrator, UI server/static code, Foundry adapter, or recommendation module.
- Interpolating SQL strings or keeping a module-global connection.
- Hiding database access in a generic utility imported by the orchestrator.

**Acceptance Criteria**
- Exact four-table schema/indexes exist; all values are parameterized; idempotency and rollback tests pass; architecture tests prove the persistence boundary.

**Risks or assumptions**
- SQLite is local PoC storage only; multi-instance cloud persistence and migration tooling are excluded.

## Phase 3: Durable Functions Orchestration With Mock Agents

**Files to create or modify**
- `app/durable_blueprint.py`, `app/activities_blueprint.py`, `app/api_blueprint.py`, `app/recommendation.py`, `function_app.py`.
- `tests/test_orchestrator.py`, `tests/test_mock_activities.py`, `tests/test_recommendation.py`, `tests/test_api_validation.py`, `tests/test_architecture_boundaries.py`.

**Functionality delivered**
- `ticket_decision_orchestrator` performs only coordination: call `CreateTicketActivity`, set `Analyzing`, fan out three `RunMockAgentActivity` calls with `task_all`, aggregate returned validated dictionaries, call `BuildRecommendationActivity`, call `SavePendingApprovalActivity`, and set `PendingApproval`.
- Persistence Activity pattern: workflow state creation/writes and audit appends occur in named activities. The orchestrator receives JSON-safe activity results only.
- The HTTP submit endpoint resolves `APPROVAL_TIMEOUT_MINUTES` and includes it in validated orchestration input; the orchestrator never reads environment/configuration.
- Mock activities return the exact schemas expected from later Foundry activities.

**Dependencies**
- Phase 2 models/repository; Azurite or DTS.

**Tests to run**
1. Generator/history-based orchestrator tests for exact activity sequence, three-way parallel fan-out, custom status, replay stability, and JSON-safe aggregation.
2. Activity, recommendation, and API contract tests.
3. Architecture AST tests plus monkeypatch tests that make database, filesystem, network, environment, UUID/random, and system-clock APIs raise if invoked while replaying the orchestrator.
4. Full pytest regression suite.

**Manual validation checkpoint**
- Submit a sample ticket, observe fan-out/fan-in, query stored results/recommendation, and review the orchestrator file line-by-line with the coordination-only checklist.

**Architecture Boundary Verification**
- `app/durable_blueprint.py` imports only approved standard types and Durable APIs; it does not import config, models that perform parsing, DB, activities as callable functions, Foundry, HTTP, filesystem, UUID/random, or system time.
- Orchestrator invokes activities by name and never calls their Python implementations directly.
- All persistence is in activities/API repository calls.

**Failure Scenarios**
- One mock activity fails; orchestration records no fabricated recommendation and follows the defined failure path.
- Replay occurs after persisted activity completion; no duplicate rows/audit events are created.
- Invalid orchestration input is rejected before instance start.

**Anti-Pattern Examples**
- Calling `db.upsert_ticket()` or `build_recommendation()` directly inside the orchestrator.
- Calling `datetime.now()`, `uuid.uuid4()`, `os.getenv()`, `open()`, or `requests` from orchestration.
- Treating activity functions as ordinary local helpers.

**Acceptance Criteria**
- Ticket reaches `PendingApproval` with three results and one recommendation; replay is deterministic/idempotent; direct I/O traps remain untouched; architecture and functional tests pass.

**Risks or assumptions**
- Durable Python orchestrators are generator-based; tests must model yielded tasks/history accurately. Approval is intentionally deferred.

## Phase 4: Foundry Prompt Agent Integration

**Files to create or modify**
- `app/foundry_client.py`, `app/activities_blueprint.py`, `app/durable_blueprint.py`, `app/config.py`, `support_playbook.md`.
- `tests/test_foundry_client.py`, `tests/test_foundry_activities.py`, `tests/test_orchestrator.py`, `tests/test_architecture_boundaries.py`.

**Functionality delivered**
- `RunFoundryAgentActivity` exclusively owns: loading Foundry configuration, reading the playbook when the role is knowledge, invoking `app.foundry_client`, extracting/parsing JSON, selecting the Pydantic schema, validating output, persisting the result, and appending audit data.
- Required flow is explicit: `Orchestrator -> RunFoundryAgentActivity -> Foundry -> Pydantic validation in activity -> validated dict -> Orchestrator task_all aggregation`.
- `app.foundry_client.py` is a leaf transport adapter: it owns client creation/invocation but imports no DB and performs no persistence.
- Orchestrator changes only activity name/payload; it never handles raw model text or Foundry errors directly beyond activity failure control flow.

**Dependencies**
- Phase 3; Foundry project/model; three named Prompt Agents; RBAC; `az login` for live smoke tests.

**Tests to run**
1. Mock SDK tests for request shape, response extraction, malformed/non-JSON output, schema mismatch, auth/service errors, and sanitization.
2. Activity tests for exact role/schema routing, playbook inclusion only for knowledge, validation-before-persistence, and idempotent result writes.
3. Architecture tests proving Foundry imports/calls occur only in activity/leaf adapter, leaf adapter has no persistence import, and orchestrator has neither Foundry imports nor response parsing.
4. Full regression and opt-in live smoke tests.

**Manual validation checkpoint**
- Invoke each configured agent through the workflow, inspect validated persisted output, then review call stacks/import graph to confirm no direct orchestrator-to-Foundry path.

**Architecture Boundary Verification**
- Tests monkeypatch Foundry constructors and JSON/Pydantic response parsing to fail if called during orchestrator replay.
- Tests assert the orchestrator sees only validated JSON-safe activity return values.
- Static analysis forbids `app.foundry_client` imports outside activities/tests and forbids `app.db` imports in the Foundry adapter.

**Failure Scenarios**
- Malformed JSON/schema drift fails the activity before persistence.
- Authentication, quota, network, or agent-not-found errors are sanitized and follow activity failure behavior.
- One agent fails while others complete; no unvalidated aggregate is produced.

**Anti-Pattern Examples**
- Instantiating `AIProjectClient` or OpenAI clients in the orchestrator or API.
- Returning raw model text for the orchestrator to parse.
- Having `foundry_client.py` write AgentResults directly.

**Acceptance Criteria**
- All Foundry interaction occurs behind `RunFoundryAgentActivity`; every response is validated before return/persistence; boundary tests and mocked/live checks pass.

**Risks or assumptions**
- SDK surface can evolve; pin and verify the tested version. External RBAC/quota/network conditions can block opt-in live checks.

## Phase 5: Human Approval External-Event Flow

**Files to create or modify**
- `app/durable_blueprint.py`, `app/activities_blueprint.py`, `app/api_blueprint.py`, `app/models.py`, `app/db.py`.
- `tests/test_decision_flow.py`, `tests/test_orchestrator.py`, `tests/test_api_validation.py`, `tests/test_architecture_boundaries.py`.

**Functionality delivered**
- Orchestrator calls `wait_for_external_event("ApprovalDecision")`, creates a durable timer from `context.current_utc_datetime` and timeout supplied in workflow input, races with `task_any`, cancels the losing timer when required, and calls activities to validate/persist approval, rejection, or timeout effects.
- Approval API validates the browser request, reads only enough repository data to verify eligibility/resolve `workflow_instance_id`, creates the complete event envelope, and calls `raise_event`. It performs no approval/status/audit write and no workflow transition.
- `RecordApprovalActivity`, `RecordRejectionActivity`, and `RecordApprovalTimeoutActivity` own validation, persistence, status updates, and audit logging.

**Dependencies**
- Phase 4; Durable external events and timers.

**Tests to run**
1. Approval, rejection, timeout, duplicate event, late event, invalid state, and host-restart-during-wait tests.
2. API spy tests assert only validation, permitted read lookup, and `raise_event` occur; repository write methods must not be called.
3. Orchestrator tests assert `wait_for_external_event` and durable timer APIs are used and all state mutations are delegated to activities.
4. Architecture and full regression tests.

**Manual validation checkpoint**
- Reach `PendingApproval`, stop/restart the host, confirm the wait survives and prior activities do not rerun, then demonstrate approve, reject, and short timeout paths.

**Architecture Boundary Verification**
- Approval API contains no calls to approval/status/audit write methods.
- Orchestrator uses no system clock/config access and persists nothing directly.
- Decision effects are reachable only through named activities after event receipt.

**Failure Scenarios**
- Duplicate event with same `decision_id` is idempotent and cannot cause another transition.
- Late event after timeout cannot reverse `ApprovalTimedOut`.
- Host restart preserves wait state.
- Event targets missing/terminal instance and returns a sanitized API error.

**Anti-Pattern Examples**
- API calling `db.upsert_approval()`, setting `Approved`, or assigning before raising the event.
- Polling the database from the orchestrator for a human decision.
- Using `datetime.now()` or `sleep()` instead of Durable time/timer APIs.

**Acceptance Criteria**
- Human decisions enter exclusively as `ApprovalDecision`; API performs no workflow-state writes; all six required paths pass; restart behavior is demonstrated; boundary tests pass.

**Risks or assumptions**
- External events are at-least-once; stable decision IDs and idempotent activities are mandatory. Running orchestrator versioning remains a v1 limitation.

## Phase 6: Final Retryable And Idempotent Assignment Action

**Files to create or modify**
- `app/durable_blueprint.py`, `app/activities_blueprint.py`, `app/db.py`.
- `tests/test_idempotency.py`, `tests/test_orchestrator.py`, `tests/test_architecture_boundaries.py`.

**Functionality delivered**
- Dedicated activity function and registered Durable name: `AssignTicketActivity`.
- Orchestrator invokes exactly `context.call_activity_with_retry("AssignTicketActivity", retry_options, payload)` with 5-second first interval and maximum 3 attempts.
- `AssignTicketActivity` exclusively owns assignment execution, lookup/claim of idempotency key `assign:<ticket_id>`, assignment persistence, assigned team/action ID, and assignment audit logging. Existing completed action returns the stored result.
- Separate completion/failure persistence activities record workflow-level `Completed` or sanitized terminal `Failed` outcomes; assignment itself never executes in orchestration.

**Dependencies**
- Phase 5 approved branch; stable Durable retry API.

**Tests to run**
1. Retry-first-failure-then-success, repeated activity call, replay after success, duplicate approval/event, concurrent duplicate attempt, and exhausted-retry tests.
2. Assert one assignment row/effect, one stable action ID/result, and deduplicated assignment audit.
3. Orchestrator test asserts exact activity name, retry options, and absence of assignment implementation calls.
4. Architecture and full regression tests.

**Manual validation checkpoint**
- Approve a ticket, observe one assignment; force one transient failure and see retry success; replay/reinvoke and confirm the same action result; force three failures and verify safe terminal failure.

**Architecture Boundary Verification**
- Static analysis finds assignment execution/persistence only in `AssignTicketActivity` and repository methods it calls.
- Orchestrator only schedules the activity and branches on its returned result/failure.
- Duplicate approvals cannot schedule a second effective assignment because both decision and assignment idempotency gates apply.

**Failure Scenarios**
- First attempt fails before claim, after claim, or during persistence; retry resolves without duplicate effect.
- All three attempts fail; failure activity records sanitized terminal state.
- Replay and duplicate event both revisit the approved branch; stored assignment is returned.

**Anti-Pattern Examples**
- Updating `assigned_team` or generating `action_id` in the orchestrator.
- Calling a local `assign_ticket()` helper directly rather than `call_activity_with_retry`.
- Using a random idempotency key per attempt.

**Acceptance Criteria**
- Exact activity name/retry API/options are used; `assign:<ticket_id>` is stable; retries/replay/duplicates create one effective assignment; terminal failure is safe; all tests pass.

**Risks or assumptions**
- Local SQLite guarantees are PoC-scoped. A real downstream system must honor the same idempotency key across crash boundaries.

## Phase 7: Web UI And Demo Controls

**Files to create or modify**
- `ui/index.html`, `ui/styles.css`, `ui/app.js`.
- `app/api_blueprint.py`, `app/demo_controls.py`, `app/activities_blueprint.py`, `app/db.py`, `sql/schema.sql` as required by the approved demo-control representation.
- `tests/test_demo_controls.py`, `tests/test_api_validation.py`, `tests/test_ui_api_contract.py`, `tests/test_architecture_boundaries.py`.

**Functionality delivered**
- Responsive dependency-free UI for ticket creation/list/detail, workflow polling, agent/recommendation/audit display, approval/rejection, and fail-next-assignment.
- Every action uses a documented HTTP endpoint: ticket submit/list/detail, workflow status, decision event, static assets, and demo control.
- UI stores only transient presentation state; it has no SQLite, database-file, local-store, Foundry, or Durable backend access.
- Demo control is armed through HTTP and consumed inside `AssignTicketActivity`; it cannot move assignment into UI/API/orchestrator code.

**Dependencies**
- Phase 6 complete backend; modern browser; no Node/npm/framework/CDN.

**Tests to run**
1. API contract tests map every interactive control to an endpoint and validate request/response schemas.
2. Static JS analysis forbids database/file/localStorage/IndexedDB/Foundry/Durable SDK access and requires application data fetches to approved `/api/...` routes.
3. Demo control atomic one-shot and assignment retry tests.
4. Static content type, XSS-safe rendering, polling terminal-state, architecture, and full regression tests.

**Manual validation checkpoint**
- Use browser dev tools Network panel to demonstrate every action as an HTTP request; verify no local file/database access, complete desktop/mobile workflow, and one-shot retry behavior.

**Architecture Boundary Verification**
- UI source has no persistence/Foundry/Durable imports or browser storage use.
- API endpoints are the only browser integration surface.
- API handlers may read/write allowed HTTP-owned demo/query data but never execute assignment or Foundry calls; fail-next is consumed by the assignment activity.

**Failure Scenarios**
- API unavailable, validation error, conflict, or terminal workflow produces a safe UI state and stops inappropriate polling/actions.
- Duplicate button clicks are disabled/deduplicated.
- Malicious response text is rendered through safe DOM APIs.

**Anti-Pattern Examples**
- Fetching `data/support_gate.db`, using a SQLite WASM library, reading local files, or storing business records in `localStorage`/IndexedDB.
- Calling Foundry or Durable storage directly from JavaScript.
- Letting the fail-next API perform assignment work.

**Acceptance Criteria**
- Every UI action has a tested HTTP endpoint; browser network inspection confirms API-only flow; static boundary tests find no client persistence bypass; responsive/accessibility/demo checks and full tests pass.

**Risks or assumptions**
- Polling is acceptable for the PoC. The four-table schema versus persistent demo-control representation must be explicitly resolved without weakening boundaries.

## Phase 8: Integration Testing, Documentation, And Demo Controls

**Files to create or modify**
- `tests/test_integration.py`, `tests/conftest.py`, `tests/test_architecture_boundaries.py`.
- `README.md`, `docs/demo-script.md`, `docs/architecture.md`, `docs/build-plan.md`, `support_playbook.md`.
- Optional `.github/pull_request_template.md` to make the invariant checklist visible in every PR.

**Functionality delivered**
- Repeatable offline integration scenarios plus opt-in live Foundry/emulator checks.
- Documentation includes Architecture Invariants, allowed dependency directions, activity ownership table, API-to-UI action map, PR checklist, setup/troubleshooting, SQLite limitation, and restart/retry demo.

**Dependencies**
- Phases 1-7 green; local emulators; Foundry resources for opt-in validation.

**Tests to run**
1. Full `python -m pytest`, with architecture tests mandatory and non-skippable.
2. Critical branch coverage for approve/reject/timeout/duplicates/late events/restart/retry/failure/replay.
3. Clean-environment bootstrap and opt-in live Foundry smoke.
4. Complete manual demo with host restart and browser Network inspection.

**Manual validation checkpoint**
- Follow README from clean checkout; execute demo; inspect DTS fan-out/event wait/retry; inspect SQLite persistence; inspect browser API-only traffic; complete PR checklist against final source.

**Architecture Boundary Verification**
- Generate/review an import-dependency report and confirm allowed directions: UI -> API; API -> repository/Durable client; orchestrator -> Durable task APIs only; orchestrator -> activities by name; activities -> repository/Foundry adapter/filesystem as owned; Foundry adapter -> SDK only.
- Deliberately introduce one forbidden import in a temporary validation branch/worktree and prove the architecture test fails, then remove it.
- Final code review signs every invariant; any violation blocks release.

**Failure Scenarios**
- Clean machine lacks credentials or emulator: offline tests still pass and live checks report explicit prerequisites.
- Host restart during approval and retry preserves durable behavior.
- Foundry malformed output, late/duplicate decisions, and exhausted assignment retries remain sanitized and consistent.

**Anti-Pattern Examples**
- Marking architecture tests optional/slow or excluding them from CI.
- Documenting a boundary that source/tests do not enforce.
- Adding a shortcut demo route that mutates workflow state or performs assignment directly.

**Acceptance Criteria**
- Full suite and boundary tests pass from a clean environment; deliberate-negative boundary check proves enforcement; live/demo checkpoints pass when prerequisites exist; documentation and PR checklist exactly match implemented ownership.

**Risks or assumptions**
- Emulator/live tests are environment-dependent and opt-in, but architecture/unit/integration tests are offline and mandatory. Cloud deployment, IaC, Azure SQL, Search, MCP, and real ticket-system assignment remain out of v1 scope.

## Dependency Order

1. Phase 1 establishes boundary enforcement and blocks all later phases.
2. Phase 2 establishes contracts and the sole SQL implementation.
3. Phase 3 introduces coordination and persistence activities under architecture tests.
4. Phase 4 replaces mock invocation only inside agent activities.
5. Phase 5 adds the external-event gate without API state mutation.
6. Phase 6 adds only retry scheduling in orchestration and all assignment effects in `AssignTicketActivity`.
7. Phase 7 adds an HTTP-only UI and one-shot demo control consumed by assignment activity.
8. Phase 8 validates the complete dependency graph, behavior, documentation, and review gate.

## Scope Boundaries

- Prompt Agents are manually provisioned and invoked by name; the app does not provision Azure resources.
- Azure AI Search, MCP, React/Node/npm, Flask/FastAPI, production cloud persistence, real ticket-system assignment, IaC, and cloud deployment are excluded.
- No placeholder/TODO implementation is accepted at a phase gate.
- Architecture invariants supersede convenience. Any implementation task that would place persistence, Foundry, approval state mutation, assignment, or client data access in the wrong layer must be redesigned before merge.