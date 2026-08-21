import json
from pathlib import Path

from app.activities_blueprint import (
    aggregate_recommendation,
    create_ticket,
    finalize_decision,
    persist_approval,
    run_mock_agent,
    save_pending_approval,
)
from app.db import get_approval, get_ticket, list_agent_results, list_audit_events
from app.models import TicketStatus
from app.recommendation import KNOWLEDGE_AGENT, RISK_AGENT, TRIAGE_AGENT


def workflow_input() -> dict:
    return {
        "ticket_id": "ticket-001",
        "workflow_instance_id": "workflow-001",
        "ticket": {
            "title": "Access failure",
            "description": "Multiple users receive 403 responses.",
        },
        "created_at": "2026-08-20T12:00:00+00:00",
        "approval_timeout_minutes": 4320,
    }


def test_mock_activities_persist_results_and_pending_recommendation(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "workflow.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    create_ticket(workflow_input())

    results = [
        run_mock_agent(
            {"ticket_id": "ticket-001", "agent_name": agent, "ticket": {}}
        )
        for agent in (TRIAGE_AGENT, KNOWLEDGE_AGENT, RISK_AGENT)
    ]
    recommendation = aggregate_recommendation(results)
    save_pending_approval(
        {"ticket_id": "ticket-001", "recommendation": recommendation}
    )

    ticket = get_ticket(database_path, "ticket-001")
    assert ticket.status is TicketStatus.PENDING_APPROVAL
    assert json.loads(ticket.recommendation_json) == recommendation
    assert len(list_agent_results(database_path, "ticket-001")) == 3


def test_approval_activities_persist_approve_and_reject(
    tmp_path: Path, monkeypatch
) -> None:
    for decision, expected in (("approve", TicketStatus.APPROVED), ("reject", TicketStatus.REJECTED)):
        database_path = tmp_path / f"{decision}.db"
        monkeypatch.setenv("DATABASE_PATH", str(database_path))
        create_ticket(workflow_input())
        approval = persist_approval(
            {
                "ticket_id": "ticket-001",
                "decision": {
                    "decision_id": f"decision-{decision}",
                    "decision": decision,
                    "approver": "Bambang",
                    "comments": "Reviewed",
                    "decided_at": "2026-08-20T13:00:00+00:00",
                },
            }
        )
        finalize_decision(
            {"ticket_id": "ticket-001", "status": expected.value}
        )

        assert approval["decision"] == decision
        assert get_approval(database_path, "ticket-001").decision == decision
        assert get_ticket(database_path, "ticket-001").status is expected


def test_replayed_activities_do_not_duplicate_rows_or_audit_events(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "replay.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    payload = workflow_input()
    create_ticket(payload)
    create_ticket(payload)

    agent_payload = {
        "ticket_id": "ticket-001",
        "agent_name": TRIAGE_AGENT,
        "ticket": payload["ticket"],
    }
    run_mock_agent(agent_payload)
    run_mock_agent(agent_payload)

    decision_payload = {
        "ticket_id": "ticket-001",
        "decision": {
            "decision_id": "decision-replayed",
            "decision": "approve",
            "approver": "Bambang",
            "comments": "Reviewed",
            "decided_at": "2026-08-20T13:00:00+00:00",
        },
    }
    persist_approval(decision_payload)
    persist_approval(decision_payload)

    assert len(list_agent_results(database_path, "ticket-001")) == 1
    assert get_approval(database_path, "ticket-001").decision_id == "decision-replayed"
    assert sorted(
        event.event_type for event in list_audit_events(database_path, "ticket-001")
    ) == ["AgentCompleted", "ApprovalDecisionRecorded", "TicketSubmitted"]