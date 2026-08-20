from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    ApprovalDecision,
    RiskAgentResult,
    TicketCreateRequest,
    TicketDetailResponse,
    TriageAgentResult,
)


def test_ticket_request_strips_text_and_rejects_extra_fields() -> None:
    request = TicketCreateRequest(title="  Access failure  ", description="  403  ")
    assert request.title == "Access failure"

    with pytest.raises(ValidationError):
        TicketCreateRequest(title="x", description="y", unexpected=True)


def test_agent_contracts_reject_invalid_enums_and_confidence() -> None:
    with pytest.raises(ValidationError):
        TriageAgentResult(
            category="Invalid",
            priority="P5",
            recommended_team="Unknown",
            summary="Summary",
            reasoning=[],
            confidence=1.2,
        )

    with pytest.raises(ValidationError):
        RiskAgentResult(
            risk_level="severe",
            approval_required=True,
            concerns=[],
            required_escalation="None",
            review_notes="Review",
            confidence=0.5,
        )


def test_approval_decision_requires_timezone_and_valid_decision() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecision(
            decision_id="decision-1",
            decision="approve",
            approver="Bambang",
            decided_at=datetime(2026, 8, 20, 12, 0),
        )

    decision = ApprovalDecision(
        decision_id="decision-1",
        decision="reject",
        approver="Bambang",
        decided_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )
    assert decision.decision == "reject"


def test_ticket_detail_response_round_trips(ticket) -> None:
    response = TicketDetailResponse(
        ticket=ticket,
        agent_results=[],
        audit_events=[],
        workflow_instance_id=ticket.workflow_instance_id,
    )

    assert TicketDetailResponse.model_validate_json(response.model_dump_json()) == response