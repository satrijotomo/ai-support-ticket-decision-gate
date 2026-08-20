from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


NonEmptyString = Annotated[str, Field(min_length=1)]
Confidence = Annotated[float, Field(ge=0, le=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TicketStatus(str, Enum):
    SUBMITTED = "Submitted"
    ANALYZING = "Analyzing"
    PENDING_APPROVAL = "PendingApproval"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    APPROVAL_TIMED_OUT = "ApprovalTimedOut"
    COMPLETED = "Completed"
    FAILED = "Failed"


class TicketCreateRequest(StrictModel):
    title: NonEmptyString
    description: NonEmptyString
    affected_service: str | None = None
    customer_impact: str | None = None
    submitted_by: str | None = None


class TicketAcceptedResponse(StrictModel):
    ticket_id: NonEmptyString
    workflow_instance_id: NonEmptyString
    status: TicketStatus


class OrchestrationInput(StrictModel):
    ticket_id: NonEmptyString
    workflow_instance_id: NonEmptyString
    ticket: TicketCreateRequest
    created_at: AwareDatetime
    approval_timeout_minutes: Annotated[int, Field(gt=0)]


class TriageAgentResult(StrictModel):
    category: Literal[
        "IdentityAccess",
        "Application",
        "Infrastructure",
        "Network",
        "Data",
        "Security",
        "Other",
    ]
    priority: Literal["P1", "P2", "P3", "P4"]
    recommended_team: Literal[
        "Identity",
        "ApplicationSupport",
        "CloudPlatform",
        "NetworkOperations",
        "DataPlatform",
        "SecurityOperations",
        "ServiceDesk",
    ]
    summary: NonEmptyString
    reasoning: list[NonEmptyString]
    confidence: Confidence


class KnowledgeAgentResult(StrictModel):
    likely_issue: NonEmptyString
    suggested_steps: list[NonEmptyString]
    missing_information: list[NonEmptyString]
    draft_response: NonEmptyString
    confidence: Confidence


class RiskAgentResult(StrictModel):
    risk_level: Literal["low", "medium", "high", "critical"]
    approval_required: bool
    concerns: list[NonEmptyString]
    required_escalation: NonEmptyString
    review_notes: NonEmptyString
    confidence: Confidence


class Recommendation(StrictModel):
    category: TriageAgentResult.__annotations__["category"]
    priority: TriageAgentResult.__annotations__["priority"]
    recommended_team: TriageAgentResult.__annotations__["recommended_team"]
    draft_response: NonEmptyString
    suggested_steps: list[NonEmptyString]
    approval_required: bool
    concerns: list[NonEmptyString]
    risk_level: RiskAgentResult.__annotations__["risk_level"]


class ApprovalDecision(StrictModel):
    decision_id: NonEmptyString
    decision: Literal["approve", "reject"]
    approver: NonEmptyString
    comments: str = ""
    decided_at: AwareDatetime


class DecisionRequest(StrictModel):
    decision: Literal["approve", "reject"]
    approver: NonEmptyString
    comments: str = ""


class ErrorResponse(StrictModel):
    error: NonEmptyString
    details: dict[str, Any] | None = None


class TicketRecord(StrictModel):
    ticket_id: NonEmptyString
    workflow_instance_id: NonEmptyString
    title: NonEmptyString
    description: NonEmptyString
    affected_service: str | None = None
    customer_impact: str | None = None
    submitted_by: str | None = None
    status: TicketStatus
    recommendation_json: str | None = None
    assigned_team: str | None = None
    action_id: str | None = None
    error_message: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AgentResultRecord(StrictModel):
    result_id: NonEmptyString
    ticket_id: NonEmptyString
    agent_name: NonEmptyString
    result_json: NonEmptyString
    confidence: Confidence | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime


class ApprovalRecord(StrictModel):
    approval_id: NonEmptyString
    decision_id: NonEmptyString
    ticket_id: NonEmptyString
    approver: NonEmptyString
    decision: Literal["approve", "reject"]
    comments: str | None = None
    decided_at: AwareDatetime


class AuditEventRecord(StrictModel):
    event_id: NonEmptyString
    ticket_id: NonEmptyString
    event_type: NonEmptyString
    event_data: str | None = None
    dedupe_key: NonEmptyString
    occurred_at: AwareDatetime


class TicketSummaryResponse(StrictModel):
    ticket_id: NonEmptyString
    workflow_instance_id: NonEmptyString
    title: NonEmptyString
    status: TicketStatus
    assigned_team: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TicketDetailResponse(StrictModel):
    ticket: TicketRecord
    recommendation: Recommendation | None = None
    agent_results: list[AgentResultRecord]
    approval: ApprovalRecord | None = None
    audit_events: list[AuditEventRecord]
    workflow_instance_id: NonEmptyString


class WorkflowStatusResponse(StrictModel):
    instance_id: NonEmptyString
    runtime_status: NonEmptyString
    custom_status: Any | None = None
    created_time: AwareDatetime
    updated_time: AwareDatetime
    output: Any | None = None