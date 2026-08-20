
Recommended v1 design
Use ordinary Python Durable Functions orchestration and invoke three manually created Foundry Prompt Agents from activity functions. This is simpler and more educational than immediately adopting the Microsoft Agent Framework Durable Extension because you can clearly see the orchestrator, activities, SQLite persistence, external approval event, retry policy, and idempotency boundary.
Microsoft recommends the Python v2 decorator programming model for new Function Apps. Durable orchestrators can fan out activities with task_all, wait for human input through external events, and retry activities with durable retry policies. Foundry’s current Python SDK pattern uses azure-ai-projects>=2.3.0, AIProjectClient, and a project-bound OpenAI client to call a named prompt agent. [learn.microsoft.com], [learn.microsoft.com], [learn.microsoft.com], [learn.microsoft.com] [learn.microsoft.com]

Important SQLite boundary: use SQLite for the local/Codespaces PoC. Do not position it as a production cloud database. Function Apps commonly run from read-only deployment packages, and filesystem-backed databases do not provide a safe multi-instance persistence model. Keep the repository abstraction so SQLite can later be replaced with Azure SQL.




 Architecture

Browser UI
├─ Submit ticket
├─ View workflow status and agent results
└─ Approve / Reject
│
▼
Python Azure Function App
├─ HTTP API endpoints
├─ Durable orchestrator
└─ Activity functions
│
├── Create/update SQLite business records
│
├── FAN OUT ── Triage Foundry Agent
│ ├ Knowledge Foundry Agent
│ └ Risk Reviewer Foundry Agent
│
├── FAN IN ─── Build recommendation
│
├── Persist PendingApproval
│
├── Wait for ApprovalDecision external event
│
└── Approve → retryable/idempotent assignment
or Reject → close workflow

Durable backend: Azurite or Durable Task Scheduler
Business state: SQLite
AI reasoning: Microsoft Foundry Prompt Agents

The key boundary is that Foundry and SQLite calls happen only inside activities or ordinary HTTP functions—not inside orchestrator code. Orchestrators are replayed and must remain deterministic; activities can perform HTTP, database, and filesystem I/O. [learn.microsoft.com], [azure_dura...819_175827 | PDF]

 Figure 1: The PoC keeps deterministic coordination in Durable Functions, agent reasoning in Foundry activities, and ticket/audit records in SQLite; only activity functions perform I/O.


 Copy/paste master prompt for GitHub Copilot
Paste the following into GitHub Copilot Chat in Agent mode from an empty repository:

Build a complete working proof-of-concept named "AI Support Ticket Decision Gate".

TECHNOLOGY
- Python 3.11
- Azure Functions runtime v4
- Python Azure Functions v2 decorator programming model
- azure-functions
- stable azure-functions-durable 1.x API, not a beta package
- azure-ai-projects >= 2.3.0
- azure-identity
- Pydantic v2
- built-in Python sqlite3
- vanilla HTML, CSS, and JavaScript; no React, Node, npm, Flask, or FastAPI
- pytest
- Azurite for local Azure Functions storage
- optional Durable Task Scheduler emulator through Docker Compose

ARCHITECTURE RULES
1. Use a Durable Functions orchestrator as the deterministic workflow coordinator.
2. Never call Foundry, SQLite, filesystem APIs, random, current system time, or network APIs directly from the orchestrator.
3. All I/O must occur in activity functions or HTTP-trigger functions.
4. Run three independent Foundry-agent activities in parallel using context.task_all:
- support-triage-agent
- support-knowledge-agent
- support-risk-reviewer-agent
5. Each agent activity must call an existing Prompt Agent in Microsoft Foundry by name.
6. Use AIProjectClient with DefaultAzureCredential and:
project.get_openai_client(agent_name=<agent-name>)
openai.responses.create(input=<JSON prompt>)
7. Parse every agent response as JSON and validate it with Pydantic.
8. Persist business records in SQLite.
9. Use SQLite transactions, WAL mode, busy_timeout, parameterized SQL, and short-lived connections.
10. Make every database write idempotent using UPSERT and unique keys.
11. Use a Durable external event named ApprovalDecision.
12. ApprovalDecision payload:
{
"decision_id": "uuid",
"decision": "approve" | "reject",
"approver": "string",
"comments": "string",
"decided_at": "ISO-8601 UTC"
}
13. Race the external event against a configurable durable timer. Default approval timeout is 72 hours.
14. Use context.call_activity_with_retry for the final mock assignment:
first retry interval 5 seconds; maximum attempts 3.
15. Add a demo control that causes the next final assignment attempt to fail once, then succeed on retry.
16. Return JSON errors with useful status codes. Never expose secrets or stack traces to the browser.
17. Add unit tests for schemas, persistence, recommendation aggregation, decision validation, and idempotent execution.

WORKFLOW
1. POST /api/tickets validates the request, creates ticket_id and workflow_instance_id, and starts ticket_decision_orchestrator.
2. The orchestrator calls create_ticket_activity.
3. Set custom orchestration status to "Analyzing".
4. Fan out three run_foundry_agent_activity calls.
5. Each agent activity:
- records started_at
- invokes the configured Foundry Prompt Agent
- validates JSON
- upserts AgentResults
- appends an AuditEvent
- records completed_at
6. Fan in with context.task_all.
7. Call build_recommendation_activity. This is deterministic Python business logic, not another LLM:
- triage supplies category, priority, recommended_team
- knowledge supplies draft_response and suggested_steps
- risk supplies approval_required, concerns, and risk_level
8. Call save_pending_approval_activity:
- store recommendation_json in Tickets
- set ticket status to PendingApproval
- append ApprovalRequested audit event
9. Set custom orchestration status to "PendingApproval".
10. Wait for ApprovalDecision or approval timeout.
11. Validate and persist the decision using record_approval_activity.
12. If rejected:
- update ticket status to Rejected
- append WorkflowRejected audit event
- complete.
13. If approved:
- set status Approved
- call execute_assignment_activity with retry.
14. execute_assignment_activity must be idempotent:
- use action key "assign:<ticket_id>"
- if already completed, return the stored result
- otherwise update assigned_team and action_id once
- support fail-next-action demo behavior
15. Update status Completed and append WorkflowCompleted.
16. On timeout, set status ApprovalTimedOut and append an audit event.
17. On terminal failure, attempt to persist Failed status and sanitized error information.

PROJECT STRUCTURE
/
function_app.py
host.json
local.settings.json.example
requirements.txt
pyproject.toml
README.md
docker-compose.yml
.gitignore
.funcignore
app/
init.py
api_blueprint.py
durable_blueprint.py
activities_blueprint.py
config.py
models.py
db.py
foundry_client.py
recommendation.py
demo_controls.py
ui/
index.html
app.js
styles.css
data/
.gitkeep
support_playbook.md
sql/
schema.sql
tests/
test_models.py
test_db.py
test_recommendation.py
test_idempotency.py
test_api_validation.py

FUNCTION APP ORGANIZATION
- function_app.py creates one df.DFApp and registers all blueprints.
- api_blueprint.py contains HTTP endpoints and serves UI assets.
- durable_blueprint.py contains only the orchestrator.
- activities_blueprint.py contains all activity-trigger functions.
- db.py owns all SQL and connection handling.
- foundry_client.py owns Foundry authentication and invocation.
- models.py contains Pydantic request and output models.
- recommendation.py merges the three validated agent results.
- demo_controls.py manages the fail-next-action control in SQLite.

HTTP API
POST /api/tickets
Request:
{
"title": "Users cannot access payroll portal",
"description": "Multiple users receive 403 after this morning's deployment.",
"affected_service": "Payroll Portal",
"customer_impact": "Approximately 40 users blocked",
"submitted_by": "demo.user@contoso.com <demo.user@contoso.com>"
}
Return 202:
{
"ticket_id": "...",
"workflow_instance_id": "...",
"status": "Submitted"
}

GET /api/tickets
Return newest tickets first with summary fields.

GET /api/tickets/{ticket_id}
Return:
- ticket
- parsed recommendation
- agent_results array
- approval, if present
- audit_events ordered by occurred_at
- workflow_instance_id

GET /api/workflows/{instance_id}
Use DurableOrchestrationClient.get_status and return runtime status,
custom status, created time, updated time, and output.

POST /api/tickets/{ticket_id}/decision
Request:
{
"decision": "approve",
"approver": "Bambang",
"comments": "Reviewed and approved"
}
The API generates decision_id and decided_at, verifies that the
ticket is PendingApproval, records request receipt idempotently,
and raises ApprovalDecision to the correct orchestration instance.

POST /api/demo/fail-next-action
Set a database flag so the next assignment activity throws a
transient exception exactly once.

GET /api/ui
Serve ui/index.html.
Also provide routes for /api/ui/app.js and /api/ui/styles.css.

SQLITE SCHEMA
Create all four requested tables and indexes exactly from sql/schema.sql.

Tickets:
- ticket_id TEXT PRIMARY KEY
- workflow_instance_id TEXT UNIQUE NOT NULL
- title TEXT NOT NULL
- description TEXT NOT NULL
- affected_service TEXT
- customer_impact TEXT
- submitted_by TEXT
- status TEXT NOT NULL
- recommendation_json TEXT
- assigned_team TEXT
- action_id TEXT
- error_message TEXT
- created_at TEXT NOT NULL
- updated_at TEXT NOT NULL

AgentResults:
- result_id TEXT PRIMARY KEY
- ticket_id TEXT NOT NULL REFERENCES Tickets(ticket_id)
- agent_name TEXT NOT NULL
- result_json TEXT NOT NULL
- confidence REAL
- started_at TEXT NOT NULL
- completed_at TEXT NOT NULL
- UNIQUE(ticket_id, agent_name)

Approvals:
- approval_id TEXT PRIMARY KEY
- decision_id TEXT UNIQUE NOT NULL
- ticket_id TEXT NOT NULL REFERENCES Tickets(ticket_id)
- approver TEXT NOT NULL
- decision TEXT NOT NULL CHECK(decision IN ('approve','reject'))
- comments TEXT
- decided_at TEXT NOT NULL

AuditEvents:
- event_id TEXT PRIMARY KEY
- ticket_id TEXT NOT NULL REFERENCES Tickets(ticket_id)
- event_type TEXT NOT NULL
- event_data TEXT
- dedupe_key TEXT UNIQUE NOT NULL
- occurred_at TEXT NOT NULL

Also create indexes on Tickets(status), AgentResults(ticket_id),
Approvals(ticket_id), and AuditEvents(ticket_id, occurred_at).

FRONTEND
Build one responsive page with:
- "Create support ticket" form
- ticket list with status badges
- selected-ticket details pane
- three agent result cards
- consolidated recommendation card
- audit timeline
- Approver name and comments fields
- Approve and Reject buttons shown only for PendingApproval
- "Fail next assignment" demo button
- polling every 2 seconds while workflow is nonterminal
- clear loading, success, validation, and error states
- no JavaScript framework and no external CDN dependency

CONFIGURATION
local.settings.json.example must include:
- AzureWebJobsStorage=UseDevelopmentStorage=true
- FUNCTIONS_WORKER_RUNTIME=python
- FOUNDRY_PROJECT_ENDPOINT
- TRIAGE_AGENT_NAME=support-triage-agent
- KNOWLEDGE_AGENT_NAME=support-knowledge-agent
- RISK_AGENT_NAME=support-risk-reviewer-agent
- DATABASE_PATH=./data/support_gate.db
- APPROVAL_TIMEOUT_MINUTES=4320
- optional DURABLE_TASK_SCHEDULER_CONNECTION_STRING

README
Include exact prerequisites, agent setup, az login, Docker startup,
virtual environment creation, package installation, database initialization,
func start, UI URL, tests, API examples, troubleshooting, and demo script.

Generate every file with complete executable code. Do not leave TODOs,
pseudo-code, placeholders other than Azure resource values, or omitted sections.
After generation, inspect imports, run pytest, and fix all failures.



 Foundry agents to create manually
Create three Prompt Agents in the same Foundry project. A prompt agent combines a deployed model, instructions, tools, and prompts; it can be invoked by name through the project OpenAI client. For v1, use the same economical model deployment for all three agents. [learn.microsoft.com]
1. support-triage-agent
Purpose: classify severity, category, and routing.
Tools: none.
Instructions:

You are an enterprise support ticket triage specialist.

Analyze only the ticket supplied by the caller. Do not invent facts.
Select exactly one category:
IdentityAccess, Application, Infrastructure, Network, Data, Security, Other.

Select exactly one priority:
P1, P2, P3, P4.

Priority guidance:
- P1: widespread critical outage, safety/security emergency, or severe business stop.
- P2: significant impact with no practical workaround.
- P3: limited impact or a workaround exists.
- P4: informational, minor, or low urgency.

Select one recommended team:
Identity, ApplicationSupport, CloudPlatform, NetworkOperations,
DataPlatform, SecurityOperations, ServiceDesk.

Return JSON only. No markdown.
Include concise evidence from the input and express confidence from 0 to 1.

Output schema:

{
"category": "IdentityAccess",
"priority": "P2",
"recommended_team": "Identity",
"summary": "Concise summary",
"reasoning": ["Evidence one", "Evidence two"],
"confidence": 0.9
}

2. support-knowledge-agent
Purpose: produce safe diagnostic steps and a draft response.
Tools: none in v1. The activity passes the ticket and support_playbook.md content in the prompt.
Instructions:

You are a technical support knowledge specialist.

Use only the ticket and playbook supplied by the caller.
Do not claim an action was completed.
Do not invent URLs, commands, policies, or product behavior.
Provide safe, reversible diagnostic steps.
If evidence is insufficient, identify the missing information.
Write a concise, professional draft response to the submitter.

Return JSON only. No markdown.

Output schema:

{
"likely_issue": "Short hypothesis",
"suggested_steps": ["Step 1", "Step 2"],
"missing_information": ["Missing item"],
"draft_response": "Customer-ready draft",
"confidence": 0.75
}

3. support-risk-reviewer-agent
Purpose: identify security, privacy, and action risks.
Tools: none.
Instructions:

You are a support governance and risk reviewer.

Review the ticket and proposed support context for:
- sensitive or personal data exposure
- credential or secret exposure
- cybersecurity indicators
- unsafe or destructive remediation
- unsupported certainty
- need for specialist escalation

Never approve or execute an action.
Return JSON only. No markdown.
Set approval_required to true when the recommendation could change a
business system, communicate externally, involve security risk, or has
insufficient evidence.

Output schema:

{
"risk_level": "low",
"approval_required": true,
"concerns": ["Concern"],
"required_escalation": "None",
"review_notes": "Concise explanation",
"confidence": 0.85
}

Although structured outputs are supported by compatible Foundry agents and Agent Framework clients, the application should still validate every response with Pydantic and reject malformed output. [microsoft-...epoint.com]


 Azure AI Search and resource requirements
Version 1
Azure AI Search is not needed. Keep support_playbook.md small and pass it to the Knowledge Agent activity. Search becomes valuable when the agent must retrieve from a larger proprietary knowledge corpus and return source-backed citations. [learn.microsoft.com]

Resource	V1 requirement
Microsoft Foundry project	Required
One deployed Foundry model	Required
Three Prompt Agents	Required
Local Python Function App	Required
Azurite	Required locally
SQLite	Required locally
Durable Task Scheduler emulator	Recommended for dashboard demos
Azure Function App	Optional until cloud migration
Application Insights	Optional locally; recommended in Azure
Azure AI Search	Not required
MCP server	Not required

 Version 2
Add Azure AI Search only to support-knowledge-agent, using a small index of troubleshooting guides. Microsoft documents that the Search tool retrieves indexed documents and can return inline citations; the current guidance recommends exposing reusable tools through a Foundry toolbox/MCP endpoint. [learn.microsoft.com]


 Local development steps

python -m venv .venv
source .venv/bin/activate # Windows: .venv\Scripts\activate
pip install -r requirements.txt
az login

Start Azurite and the optional DTS emulator:

docker compose up -d

Initialize SQLite:

python -c "from app.db import initialize_database; initialize_database()"

Copy settings:

cp local.settings.json.example local.settings.json

Populate the Foundry project endpoint and agent names, then run:

func start

Open:

http://localhost:7071/api/ui

The Durable Task Scheduler emulator supports local orchestration monitoring through its browser dashboard; Microsoft documents the emulator as a Docker container and the dashboard at http://localhost:8082 when mapped to that port. [learn.microsoft.com], [learn.microsoft.com]


 Demo script

1. Submit a ticket describing a multi-user access failure. 
2. Show the three agent cards completing independently. 
3. Show the fan-out/fan-in execution in the DTS dashboard. 
4. Wait until the status becomes PendingApproval. 
5. Stop func start. 
6. Restart it and show: 
- the ticket and agent output still exist in SQLite; 
- the orchestration remains at the approval gate; 
- completed activities are not rerun. 
7. Click Fail next assignment. 
8. Enter an approver and click Approve. 
9. Show the first assignment attempt fail and the durable retry succeed. 
10. Refresh the ticket and show: 
- Completed; 
- assigned team; 
- approval record; 
- ordered audit timeline. 
11. Submit another ticket and click Reject to demonstrate the alternate branch. 
External-event approvals are asynchronous and can wake a waiting orchestration after the worker has stopped; Microsoft recommends unique event identifiers because external events use at-least-once delivery semantics. Likewise, activity writes should be idempotent—hence the UPSERTs, unique decision_id, dedupe_key, and action key used in this design. 
