import json
from datetime import datetime, timezone
from pathlib import Path

import azure.durable_functions as df
from pydantic import ValidationError

from app.config import AppSettings
from app.db import (
    append_audit_event,
    get_ticket,
    initialize_database,
    record_approval,
    record_assignment,
    update_ticket_status,
    upsert_agent_result,
    upsert_ticket,
)
from app.demo_controls import consume_fail_next_assignment
from app.foundry_client import FoundryClientError, invoke_prompt_agent
from app.models import (
    AgentResultRecord,
    AssignmentRequest,
    ApprovalDecision,
    ApprovalRecord,
    AuditEventRecord,
    KnowledgeAgentResult,
    OrchestrationInput,
    Recommendation,
    RiskAgentResult,
    TicketCreateRequest,
    TicketRecord,
    TicketStatus,
    TriageAgentResult,
)
from app.recommendation import (
    KNOWLEDGE_AGENT,
    RISK_AGENT,
    TRIAGE_AGENT,
    build_recommendation,
)


activities_blueprint = df.Blueprint()
SUPPORT_PLAYBOOK_PATH = Path(__file__).parents[1] / "support_playbook.md"


class AgentActivityError(RuntimeError):
    """A sanitized agent activity failure safe for workflow history."""


class AssignmentActivityError(RuntimeError):
    """A sanitized transient assignment failure safe for workflow history."""


def _database_path() -> str:
    return AppSettings.from_environment().database_path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _append_audit(
    database_path: str,
    ticket_id: str,
    event_type: str,
    dedupe_key: str,
    occurred_at: datetime,
    event_data: dict | None = None,
) -> None:
    append_audit_event(
        database_path,
        AuditEventRecord(
            event_id=f"audit:{dedupe_key}",
            ticket_id=ticket_id,
            event_type=event_type,
            event_data=json.dumps(event_data, sort_keys=True) if event_data else None,
            dedupe_key=dedupe_key,
            occurred_at=occurred_at,
        ),
    )


def create_ticket(payload: dict) -> dict:
    workflow_input = OrchestrationInput.model_validate(payload)
    database_path = _database_path()
    initialize_database(database_path)
    ticket = TicketRecord(
        ticket_id=workflow_input.ticket_id,
        workflow_instance_id=workflow_input.workflow_instance_id,
        title=workflow_input.ticket.title,
        description=workflow_input.ticket.description,
        affected_service=workflow_input.ticket.affected_service,
        customer_impact=workflow_input.ticket.customer_impact,
        submitted_by=workflow_input.ticket.submitted_by,
        status=TicketStatus.SUBMITTED,
        created_at=workflow_input.created_at,
        updated_at=workflow_input.created_at,
    )
    stored = upsert_ticket(database_path, ticket)
    _append_audit(
        database_path,
        ticket.ticket_id,
        "TicketSubmitted",
        f"{ticket.ticket_id}:submitted",
        workflow_input.created_at,
    )
    return stored.model_dump(mode="json")


@activities_blueprint.function_name(name="CreateTicketActivity")
@activities_blueprint.activity_trigger(
    input_name="payload", activity="CreateTicketActivity"
)
def create_ticket_activity(payload: dict) -> dict:
    return create_ticket(payload)


def _mock_result(agent_name: str) -> TriageAgentResult | KnowledgeAgentResult | RiskAgentResult:
    if agent_name == TRIAGE_AGENT:
        return TriageAgentResult(
            category="IdentityAccess",
            priority="P2",
            recommended_team="Identity",
            summary="Multiple users are blocked by authorization failures.",
            reasoning=["Multiple users receive HTTP 403 responses."],
            confidence=0.9,
        )
    if agent_name == KNOWLEDGE_AGENT:
        return KnowledgeAgentResult(
            likely_issue="An authorization configuration may have changed.",
            suggested_steps=[
                "Confirm the affected user scope.",
                "Review the most recent authorization configuration change.",
            ],
            missing_information=["Recent deployment or policy change details"],
            draft_response="We are reviewing the reported authorization failures.",
            confidence=0.75,
        )
    if agent_name == RISK_AGENT:
        return RiskAgentResult(
            risk_level="medium",
            approval_required=True,
            concerns=["Changing authorization may affect additional users."],
            required_escalation="Identity",
            review_notes="Require approval before changing access configuration.",
            confidence=0.85,
        )
    raise ValueError(f"unsupported mock agent: {agent_name}")


def _agent_contract(settings: AppSettings, agent_role: str):
    contracts = {
        TRIAGE_AGENT: (settings.triage_agent_name, TriageAgentResult),
        KNOWLEDGE_AGENT: (settings.knowledge_agent_name, KnowledgeAgentResult),
        RISK_AGENT: (settings.risk_agent_name, RiskAgentResult),
    }
    try:
        return contracts[agent_role]
    except KeyError:
        raise AgentActivityError("Agent request is invalid.") from None


def _build_agent_prompt(ticket_payload: dict, playbook: str | None = None) -> str:
    ticket = TicketCreateRequest.model_validate(ticket_payload)
    prompt_payload = {"ticket": ticket.model_dump(mode="json", exclude_none=True)}
    if playbook is not None:
        prompt_payload["support_playbook"] = playbook
    return json.dumps(prompt_payload, sort_keys=True, separators=(",", ":"))


def _validate_agent_output(agent_model, raw_output: str):
    try:
        return agent_model.model_validate_json(raw_output)
    except (ValidationError, ValueError):
        raise AgentActivityError("Agent returned an invalid response.") from None


def run_foundry_agent(payload: dict) -> dict:
    ticket_id = str(payload["ticket_id"])
    agent_role = str(payload["agent_name"])
    settings = AppSettings.from_environment()
    configured_agent_name, agent_model = _agent_contract(settings, agent_role)
    started_at = _now()

    if settings.foundry_mock_mode:
        result = _mock_result(agent_role)
    else:
        if settings.foundry_project_endpoint is None:
            raise AgentActivityError("Foundry configuration is incomplete.")
        playbook = None
        if agent_role == KNOWLEDGE_AGENT:
            try:
                playbook = SUPPORT_PLAYBOOK_PATH.read_text(encoding="utf-8")
            except OSError:
                raise AgentActivityError("Agent configuration is unavailable.") from None
        try:
            prompt = _build_agent_prompt(payload["ticket"], playbook)
            raw_output = invoke_prompt_agent(
                str(settings.foundry_project_endpoint),
                configured_agent_name,
                prompt,
            )
        except FoundryClientError:
            raise AgentActivityError("Agent service is unavailable.") from None
        except ValidationError:
            raise AgentActivityError("Agent request is invalid.") from None
        result = _validate_agent_output(agent_model, raw_output)

    completed_at = _now()
    database_path = settings.database_path
    stored = upsert_agent_result(
        database_path,
        AgentResultRecord(
            result_id=f"{ticket_id}:{configured_agent_name}",
            ticket_id=ticket_id,
            agent_name=configured_agent_name,
            result_json=result.model_dump_json(),
            confidence=result.confidence,
            started_at=started_at,
            completed_at=completed_at,
        ),
    )
    _append_audit(
        database_path,
        ticket_id,
        "AgentCompleted",
        f"{ticket_id}:agent:{configured_agent_name}",
        completed_at,
        {"agent_name": configured_agent_name},
    )
    return {
        "agent_name": agent_role,
        "result": json.loads(stored.result_json),
    }


@activities_blueprint.function_name(name="RunFoundryAgentActivity")
@activities_blueprint.activity_trigger(
    input_name="payload", activity="RunFoundryAgentActivity"
)
def run_foundry_agent_activity(payload: dict) -> dict:
    return run_foundry_agent(payload)


def aggregate_recommendation(payload: list[dict]) -> dict:
    return build_recommendation(payload).model_dump(mode="json")


@activities_blueprint.function_name(name="BuildRecommendationActivity")
@activities_blueprint.activity_trigger(
    input_name="payload", activity="BuildRecommendationActivity"
)
def build_recommendation_activity(payload) -> dict:
    return aggregate_recommendation(payload)


def save_pending_approval(payload: dict) -> dict:
    ticket_id = str(payload["ticket_id"])
    validated = Recommendation.model_validate(payload["recommendation"])
    timestamp = _now()
    database_path = _database_path()
    stored = update_ticket_status(
        database_path,
        ticket_id,
        TicketStatus.PENDING_APPROVAL,
        timestamp,
        recommendation_json=validated.model_dump_json(),
    )
    _append_audit(
        database_path,
        ticket_id,
        "ApprovalRequested",
        f"{ticket_id}:approval-requested",
        timestamp,
    )
    return stored.model_dump(mode="json")


@activities_blueprint.function_name(name="SavePendingApprovalActivity")
@activities_blueprint.activity_trigger(
    input_name="payload", activity="SavePendingApprovalActivity"
)
def save_pending_approval_activity(payload: dict) -> dict:
    return save_pending_approval(payload)


def persist_approval(payload: dict) -> dict:
    ticket_id = str(payload["ticket_id"])
    decision_payload = payload["decision"]
    if isinstance(decision_payload, (str, bytes, bytearray)):
        decision = ApprovalDecision.model_validate_json(decision_payload)
    else:
        decision = ApprovalDecision.model_validate(decision_payload)
    database_path = _database_path()
    stored = record_approval(
        database_path,
        ApprovalRecord(
            approval_id=decision.decision_id,
            decision_id=decision.decision_id,
            ticket_id=ticket_id,
            approver=decision.approver,
            decision=decision.decision,
            comments=decision.comments,
            decided_at=decision.decided_at,
        ),
    )
    _append_audit(
        database_path,
        ticket_id,
        "ApprovalDecisionRecorded",
        f"{ticket_id}:decision:{decision.decision_id}",
        decision.decided_at,
        {"decision": decision.decision, "approver": decision.approver},
    )
    return stored.model_dump(mode="json")


@activities_blueprint.function_name(name="RecordApprovalActivity")
@activities_blueprint.activity_trigger(
    input_name="payload", activity="RecordApprovalActivity"
)
def record_approval_activity(payload: dict) -> dict:
    return persist_approval(payload)


def persist_approval_timeout(payload: dict) -> dict:
    ticket_id = str(payload["ticket_id"])
    timeout_at = datetime.fromisoformat(str(payload["timeout_at"]))
    if timeout_at.tzinfo is None:
        raise ValueError("timeout_at must include a timezone")
    database_path = _database_path()
    stored = update_ticket_status(
        database_path,
        ticket_id,
        TicketStatus.APPROVAL_TIMED_OUT,
        timeout_at,
    )
    _append_audit(
        database_path,
        ticket_id,
        "ApprovalTimedOut",
        f"{ticket_id}:approval-timed-out",
        timeout_at,
    )
    return stored.model_dump(mode="json")


@activities_blueprint.function_name(name="PersistApprovalTimeoutActivity")
@activities_blueprint.activity_trigger(
    input_name="payload", activity="PersistApprovalTimeoutActivity"
)
def persist_approval_timeout_activity(payload: dict) -> dict:
    return persist_approval_timeout(payload)


def finalize_decision(payload: dict) -> dict:
    ticket_id = str(payload["ticket_id"])
    status = TicketStatus(payload["status"])
    if status not in {
        TicketStatus.APPROVED,
        TicketStatus.REJECTED,
        TicketStatus.COMPLETED,
    }:
        raise ValueError("workflow status must be Approved, Rejected, or Completed")
    timestamp = _now()
    database_path = _database_path()
    stored = update_ticket_status(database_path, ticket_id, status, timestamp)
    _append_audit(
        database_path,
        ticket_id,
        f"Workflow{status.value}",
        f"{ticket_id}:workflow:{status.value.lower()}",
        timestamp,
    )
    return stored.model_dump(mode="json")


@activities_blueprint.function_name(name="FinalizeDecisionActivity")
@activities_blueprint.activity_trigger(
    input_name="payload", activity="FinalizeDecisionActivity"
)
def finalize_decision_activity(payload: dict) -> dict:
    return finalize_decision(payload)


def execute_assignment(payload: dict) -> dict:
    assignment = AssignmentRequest.model_validate(payload)
    expected_action_id = f"assign:{assignment.ticket_id}"
    if assignment.action_id != expected_action_id:
        raise ValueError("assignment action_id is invalid")

    database_path = _database_path()
    existing = get_ticket(database_path, assignment.ticket_id)
    if existing is None:
        raise KeyError(f"ticket not found: {assignment.ticket_id}")
    if existing.status not in {TicketStatus.APPROVED, TicketStatus.COMPLETED}:
        raise ValueError("ticket must be approved before assignment")

    if existing.action_id is None and consume_fail_next_assignment(database_path):
        raise AssignmentActivityError("Assignment service is temporarily unavailable.")

    timestamp = _now()
    stored = record_assignment(
        database_path,
        assignment.ticket_id,
        assignment.action_id,
        assignment.assigned_team,
        timestamp,
    )
    _append_audit(
        database_path,
        assignment.ticket_id,
        "AssignmentCompleted",
        f"{assignment.ticket_id}:assignment:{assignment.action_id}",
        timestamp,
        {
            "action_id": assignment.action_id,
            "assigned_team": assignment.assigned_team,
        },
    )
    return stored.model_dump(mode="json")


@activities_blueprint.function_name(name="ExecuteAssignmentActivity")
@activities_blueprint.activity_trigger(
    input_name="payload", activity="ExecuteAssignmentActivity"
)
def execute_assignment_activity(payload: dict) -> dict:
    return execute_assignment(payload)