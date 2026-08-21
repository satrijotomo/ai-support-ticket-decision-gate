import json
from datetime import datetime, timezone

import azure.durable_functions as df

from app.config import AppSettings
from app.db import (
    append_audit_event,
    initialize_database,
    record_approval,
    update_ticket_status,
    upsert_agent_result,
    upsert_ticket,
)
from app.models import (
    AgentResultRecord,
    ApprovalDecision,
    ApprovalRecord,
    AuditEventRecord,
    KnowledgeAgentResult,
    OrchestrationInput,
    Recommendation,
    RiskAgentResult,
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


def run_mock_agent(payload: dict) -> dict:
    ticket_id = str(payload["ticket_id"])
    agent_name = str(payload["agent_name"])
    result = _mock_result(agent_name)
    timestamp = _now()
    database_path = _database_path()
    stored = upsert_agent_result(
        database_path,
        AgentResultRecord(
            result_id=f"{ticket_id}:{agent_name}",
            ticket_id=ticket_id,
            agent_name=agent_name,
            result_json=result.model_dump_json(),
            confidence=result.confidence,
            started_at=timestamp,
            completed_at=timestamp,
        ),
    )
    _append_audit(
        database_path,
        ticket_id,
        "AgentCompleted",
        f"{ticket_id}:agent:{agent_name}",
        timestamp,
        {"agent_name": agent_name},
    )
    return {
        "agent_name": stored.agent_name,
        "result": json.loads(stored.result_json),
    }


@activities_blueprint.function_name(name="RunMockAgentActivity")
@activities_blueprint.activity_trigger(
    input_name="payload", activity="RunMockAgentActivity"
)
def run_mock_agent_activity(payload: dict) -> dict:
    return run_mock_agent(payload)


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
    decision = ApprovalDecision.model_validate(payload["decision"])
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


def finalize_decision(payload: dict) -> dict:
    ticket_id = str(payload["ticket_id"])
    status = TicketStatus(payload["status"])
    if status not in {TicketStatus.APPROVED, TicketStatus.REJECTED}:
        raise ValueError("decision status must be Approved or Rejected")
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