# AI Support Ticket Decision Gate

A Python 3.11 proof of concept that runs support tickets through three parallel
Microsoft Foundry Prompt Agents, composes a recommendation, pauses at a durable
human approval gate, and then either assigns or rejects the ticket. The local UI
is a single-page vanilla HTML, CSS, and JavaScript application served by Azure
Functions.

## Architecture

- Azure Functions v4 and Durable Functions coordinate the workflow.
- Three activity functions run triage, knowledge, and risk analysis in parallel.
- The deterministic orchestrator waits for the `ApprovalDecision` external event.
- SQLite persists tickets, agent outputs, decisions, assignments, and audit events.
- Azurite supplies local Azure Storage; the DTS emulator is optional.
- Pydantic validates API input, agent output, workflow payloads, and persisted data.

SQLite is suitable for this local proof of concept only. A multi-instance production
deployment needs a shared, production-grade data store and a corresponding migration
of the persistence layer.

## Prerequisites

Install:

- Python 3.11
- Azure Functions Core Tools v4 (`func`)
- Docker Desktop with Docker Compose
- Azure CLI (`az`) for real Foundry mode
- A Microsoft Foundry project, deployed model, and project access for real agent calls

Verify the command-line tools:

```powershell
python --version
func --version
docker --version
docker compose version
az version
```

## Foundry Agents

Mock mode is enabled by default and does not require Azure resources. To use real
Foundry calls, create three Prompt Agents in the same Foundry project and use one
deployed model for all three. Do not attach tools in v1.

Create agents with these exact names and responsibilities:

| Agent name | Responsibility | Required JSON fields |
| --- | --- | --- |
| `support-triage-agent` | Select category, priority, and team from ticket evidence | `category`, `priority`, `recommended_team`, `summary`, `reasoning`, `confidence` |
| `support-knowledge-agent` | Use only the supplied ticket and playbook for safe diagnostics and a draft response | `likely_issue`, `suggested_steps`, `missing_information`, `draft_response`, `confidence` |
| `support-risk-reviewer-agent` | Identify security, privacy, action, certainty, and escalation risks without executing actions | `risk_level`, `approval_required`, `concerns`, `required_escalation`, `review_notes`, `confidence` |

Configure each agent to return JSON only, with no Markdown. Confidence must be from
0 to 1. The allowed category, priority, team, and risk values and the complete agent
instructions are in [docs/implementation-spec.md](docs/implementation-spec.md).
The application validates every response against those strict Pydantic contracts.

Sign in before starting the Function App in real Foundry mode:

```powershell
az login
```

Set `FOUNDRY_MOCK_MODE` to `false`, set `FOUNDRY_PROJECT_ENDPOINT` to the project
endpoint, and keep the three configured names aligned with the agents above.

## Local Development

From the repository root, create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For bash or zsh, activate with `source .venv/bin/activate`.

Copy the local settings template:

```powershell
Copy-Item local.settings.json.example local.settings.json
```

Leave `FOUNDRY_MOCK_MODE` as `true` for deterministic local agents. For real agents,
apply the Foundry settings described above. Never commit `local.settings.json`.

Start Azurite:

```powershell
docker compose up -d azurite
```

To also start the optional Durable Task Scheduler emulator and dashboard:

```powershell
docker compose --profile dts up -d
```

The DTS gRPC endpoint is exposed on port `8080` and its dashboard is at
http://localhost:8082. Configure `DURABLE_TASK_SCHEDULER_CONNECTION_STRING` only
when using that backend; otherwise leave it empty and Durable Functions uses Azurite.

Initialize the local database:

```powershell
python -c "from app.db import initialize_database; initialize_database('./data/support_gate.db')"
```

Start the Function App:

```powershell
func start
```

Open the UI at http://localhost:7071/api/ui. Stop the host with `Ctrl+C`; stop local
containers with `docker compose --profile dts down`.

## Tests

Run the normal suite, which keeps live E2E tests skipped:

```powershell
python -m pytest -q
```

The E2E tests use the real HTTP and Durable workflow boundaries. Start Azurite and
the Function App in mock mode first, then run in a second terminal:

```powershell
$env:RUN_E2E = "true"
python -m pytest tests/test_e2e.py -q
Remove-Item Env:RUN_E2E
```

The tests submit unique tickets to the configured app and retain them in its SQLite
database as audit evidence. To target another host, set `E2E_BASE_URL`, including
the `/api` prefix:

```powershell
$env:RUN_E2E = "true"
$env:E2E_BASE_URL = "http://localhost:7072/api"
python -m pytest tests/test_e2e.py -q
```

The approval E2E test arms the fail-next control and verifies eventual completion
after Durable retry. The rejection test verifies that no assignment is performed.

## API Examples

Submit a ticket:

```powershell
$ticket = Invoke-RestMethod -Method Post `
  -Uri http://localhost:7071/api/tickets `
  -ContentType "application/json" `
  -Body (@{
    title = "Multiple users cannot access payroll"
    description = "Twenty users receive HTTP 403 after today's deployment."
    affected_service = "Payroll Portal"
    customer_impact = "Payroll processing is blocked"
    submitted_by = "demo.user@contoso.com"
  } | ConvertTo-Json)
$ticket
```

List tickets and inspect the submitted ticket:

```powershell
Invoke-RestMethod http://localhost:7071/api/tickets
Invoke-RestMethod "http://localhost:7071/api/tickets/$($ticket.ticket_id)"
Invoke-RestMethod "http://localhost:7071/api/workflows/$($ticket.workflow_instance_id)"
```

After the ticket reaches `PendingApproval`, approve it:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:7071/api/tickets/$($ticket.ticket_id)/decision" `
  -ContentType "application/json" `
  -Body (@{
    decision = "approve"
    approver = "Demo Operator"
    comments = "Reviewed agent evidence."
  } | ConvertTo-Json)
```

Use `reject` as the decision to exercise the rejection branch. To make the next
approved assignment fail once before Durable retry:

```powershell
Invoke-RestMethod -Method Post http://localhost:7071/api/demo/fail-next-action
```

## Demo Walkthrough

1. Start Azurite, optionally start DTS, initialize SQLite, and run `func start`.
2. Open http://localhost:7071/api/ui and submit a multi-user access failure.
3. Show the triage, knowledge, and risk cards completing independently. With DTS,
   show the fan-out/fan-in execution at http://localhost:8082.
4. Wait for `PendingApproval`, then stop the Functions host with `Ctrl+C`.
5. Restart with `func start`. Confirm the ticket and agent output remain in SQLite,
   the workflow still waits at the approval gate, and completed activities do not rerun.
6. Click **Fail next assignment**, enter an approver, and click **Approve**.
7. Show the first assignment attempt fail and the Durable retry succeed.
8. Refresh the ticket and show `Completed`, assigned team, approval record, and the
   ordered audit timeline.
9. Submit another ticket, wait for `PendingApproval`, and click **Reject** to show
   the terminal rejection branch without assignment.

## Troubleshooting

**`func` is not recognized**

Install Azure Functions Core Tools v4 and open a new terminal. Confirm with
`func --version`.

**Azurite connection failures or orchestration does not start**

Run `docker compose ps` and confirm `support-gate-azurite` is healthy. Inspect logs
with `docker compose logs azurite`. Ports `10000` through `10002` must be available,
and `AzureWebJobsStorage` must be `UseDevelopmentStorage=true`.

**DTS dashboard is unavailable**

Start the profile with `docker compose --profile dts up -d` and inspect
`docker compose logs durable-task-scheduler`. Confirm ports `8080` and `8082` are
free. DTS is optional; clear its connection string to use Azurite-backed Durable
Functions instead.

**The UI opens but cannot load tickets**

Use the exact URL http://localhost:7071/api/ui and check the `func start` terminal
for request errors. Confirm `local.settings.json` exists and initialize the database
at the same path configured by `DATABASE_PATH`.

**A submitted ticket briefly returns 404**

Submission starts the durable instance before `CreateTicketActivity` writes SQLite.
This short eventual-consistency window is expected; poll until the ticket appears.

**A workflow remains at `PendingApproval`**

This is the durable approval gate. Submit one decision through the UI or decision
API. The default timeout is 4,320 minutes (three days). If the host was restarted,
start it again and allow Durable Functions to resume the stored orchestration.

**Real Foundry calls fail**

Run `az login`, verify the signed-in identity can access the Foundry project, confirm
`FOUNDRY_PROJECT_ENDPOINT`, and verify all three agent names. Set
`FOUNDRY_MOCK_MODE=true` to isolate local workflow behavior from Foundry access.

**E2E tests are skipped**

Set `RUN_E2E=true` in the test terminal. If they fail to connect, start `func start`
or set `E2E_BASE_URL` to the host's `/api` URL.

**SQLite reports that the database is locked**

Use one local Function App instance and avoid opening write transactions in external
SQLite tools. Stop the host before deleting `data/support_gate.db` for a clean reset,
then run the initialization command again.