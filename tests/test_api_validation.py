import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.activities_blueprint import create_ticket
from app.api_blueprint import (
    arm_fail_next_assignment_request,
    get_ticket_detail_request,
    get_workflow_status_request,
    list_tickets_request,
    serve_ui_asset,
    start_ticket_request,
    submit_decision_request,
)
from app.db import get_approval, list_audit_events, update_ticket_status
from app.demo_controls import consume_fail_next_assignment
from app.models import TicketStatus


class FakeRequest:
    def __init__(self, body, route_params: dict[str, str] | None = None) -> None:
        self.body = body
        self.route_params = route_params or {}

    def get_json(self):
        return self.body


class FakeClient:
    def __init__(self, workflow_status=None) -> None:
        self.starts: list[tuple] = []
        self.events: list[tuple] = []
        self.workflow_status = workflow_status

    async def start_new(self, name, instance_id=None, client_input=None):
        self.starts.append((name, instance_id, client_input))
        return instance_id

    async def raise_event(self, instance_id, event_name, event_data=None):
        self.events.append((instance_id, event_name, event_data))

    async def get_status(self, instance_id):
        return self.workflow_status


def test_http_starter_rejects_invalid_input_before_start() -> None:
    client = FakeClient()
    response = asyncio.run(
        start_ticket_request(FakeRequest({"title": "Missing description"}), client)
    )

    assert response.status_code == 400
    assert client.starts == []


def test_http_starter_begins_valid_orchestration() -> None:
    client = FakeClient()
    response = asyncio.run(
        start_ticket_request(
            FakeRequest({"title": "Access failure", "description": "Users receive 403."}),
            client,
        )
    )

    body = json.loads(response.get_body())
    assert response.status_code == 202
    assert client.starts[0][0] == "ticket_decision_orchestrator"
    assert client.starts[0][1] == body["workflow_instance_id"]
    assert client.starts[0][2]["ticket_id"] == body["ticket_id"]


def _stored_ticket(database_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    create_ticket(
        {
            "ticket_id": "ticket-001",
            "workflow_instance_id": "workflow-001",
            "ticket": {
                "title": "Access failure",
                "description": "Users receive 403.",
            },
            "created_at": "2026-08-20T12:00:00+00:00",
            "approval_timeout_minutes": 4320,
        }
    )


def test_decision_endpoint_rejects_invalid_input_without_event(
    tmp_path: Path, monkeypatch
) -> None:
    _stored_ticket(tmp_path / "invalid.db", monkeypatch)
    client = FakeClient()
    response = asyncio.run(
        submit_decision_request(
            FakeRequest(
                {"decision": "maybe", "approver": "Bambang"},
                {"ticket_id": "ticket-001"},
            ),
            client,
        )
    )

    assert response.status_code == 400
    assert client.events == []


def test_decision_endpoint_returns_not_found_without_event(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "missing.db"
    _stored_ticket(database_path, monkeypatch)
    client = FakeClient()
    response = asyncio.run(
        submit_decision_request(
            FakeRequest(
                {"decision": "approve", "approver": "Bambang"},
                {"ticket_id": "missing"},
            ),
            client,
        )
    )

    assert response.status_code == 404
    assert client.events == []


def test_decision_endpoint_rejects_non_pending_ticket_without_event(
    tmp_path: Path, monkeypatch
) -> None:
    _stored_ticket(tmp_path / "conflict.db", monkeypatch)
    client = FakeClient()
    response = asyncio.run(
        submit_decision_request(
            FakeRequest(
                {"decision": "approve", "approver": "Bambang"},
                {"ticket_id": "ticket-001"},
            ),
            client,
        )
    )

    assert response.status_code == 409
    assert client.events == []


def test_decision_endpoint_raises_complete_external_event(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "pending.db"
    _stored_ticket(database_path, monkeypatch)
    update_ticket_status(
        database_path,
        "ticket-001",
        TicketStatus.PENDING_APPROVAL,
        datetime.fromisoformat("2026-08-20T12:30:00+00:00"),
    )
    client = FakeClient()
    response = asyncio.run(
        submit_decision_request(
            FakeRequest(
                {
                    "decision": "approve",
                    "approver": "Bambang",
                    "comments": "Proceed",
                },
                {"ticket_id": "ticket-001"},
            ),
            client,
        )
    )

    assert response.status_code == 202
    assert len(client.events) == 1
    instance_id, event_name, event_data = client.events[0]
    assert instance_id == "workflow-001"
    assert event_name == "ApprovalDecision"
    assert event_data["decision"] == "approve"
    assert event_data["approver"] == "Bambang"
    assert event_data["comments"] == "Proceed"
    assert event_data["decision_id"]
    assert event_data["decided_at"]
    assert get_approval(database_path, "ticket-001").decision_id == event_data["decision_id"]
    assert [
        event.event_type for event in list_audit_events(database_path, "ticket-001")
    ][-1] == "ApprovalDecisionReceived"


def test_decision_endpoint_replays_identical_approval_idempotently(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "duplicate.db"
    _stored_ticket(database_path, monkeypatch)
    update_ticket_status(
        database_path,
        "ticket-001",
        TicketStatus.PENDING_APPROVAL,
        datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc),
    )
    client = FakeClient()
    request = FakeRequest(
        {"decision": "approve", "approver": "Bambang"},
        {"ticket_id": "ticket-001"},
    )

    first = asyncio.run(submit_decision_request(request, client))
    second = asyncio.run(submit_decision_request(request, client))

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(client.events) == 2
    assert client.events[0] == client.events[1]
    assert len(
        [
            event
            for event in list_audit_events(database_path, "ticket-001")
            if event.event_type == "ApprovalDecisionReceived"
        ]
    ) == 1


def test_decision_endpoint_rejects_conflicting_duplicate(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "conflicting-duplicate.db"
    _stored_ticket(database_path, monkeypatch)
    update_ticket_status(
        database_path,
        "ticket-001",
        TicketStatus.PENDING_APPROVAL,
        datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc),
    )
    client = FakeClient()

    first = asyncio.run(
        submit_decision_request(
            FakeRequest(
                {"decision": "approve", "approver": "Bambang"},
                {"ticket_id": "ticket-001"},
            ),
            client,
        )
    )
    second = asyncio.run(
        submit_decision_request(
            FakeRequest(
                {"decision": "reject", "approver": "Bambang"},
                {"ticket_id": "ticket-001"},
            ),
            client,
        )
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert len(client.events) == 1


def test_workflow_status_endpoint_returns_durable_status() -> None:
    timestamp = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    client = FakeClient(
        SimpleNamespace(
            instance_id="workflow-001",
            runtime_status="Running",
            custom_status="PendingApproval",
            created_time=timestamp,
            last_updated_time=timestamp,
            output=None,
        )
    )

    response = asyncio.run(
        get_workflow_status_request(
            FakeRequest({}, {"instance_id": "workflow-001"}), client
        )
    )

    assert response.status_code == 200
    assert json.loads(response.get_body()) == {
        "instance_id": "workflow-001",
        "runtime_status": "Running",
        "custom_status": "PendingApproval",
        "created_time": "2026-08-20T12:00:00Z",
        "updated_time": "2026-08-20T12:00:00Z",
        "output": None,
    }


def test_workflow_status_endpoint_returns_not_found() -> None:
    response = asyncio.run(
        get_workflow_status_request(
            FakeRequest({}, {"instance_id": "missing"}), FakeClient()
        )
    )

    assert response.status_code == 404


def test_ticket_list_endpoint_returns_summary_records(
    tmp_path: Path, monkeypatch
) -> None:
    _stored_ticket(tmp_path / "ticket-list.db", monkeypatch)

    response = list_tickets_request(FakeRequest({}))

    assert response.status_code == 200
    assert json.loads(response.get_body()) == [
        {
            "ticket_id": "ticket-001",
            "workflow_instance_id": "workflow-001",
            "title": "Access failure",
            "status": "Submitted",
            "assigned_team": None,
            "created_at": "2026-08-20T12:00:00Z",
            "updated_at": "2026-08-20T12:00:00Z",
        }
    ]


def test_ticket_detail_endpoint_returns_parsed_recommendation(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "ticket-detail.db"
    _stored_ticket(database_path, monkeypatch)
    update_ticket_status(
        database_path,
        "ticket-001",
        TicketStatus.PENDING_APPROVAL,
        datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc),
        recommendation_json=json.dumps(
            {
                "category": "IdentityAccess",
                "priority": "P2",
                "recommended_team": "Identity",
                "draft_response": "We are reviewing the access failure.",
                "suggested_steps": ["Confirm the affected accounts."],
                "approval_required": True,
                "concerns": ["Multiple users are blocked."],
                "risk_level": "medium",
            }
        ),
    )

    response = get_ticket_detail_request(
        FakeRequest({}, {"ticket_id": "ticket-001"})
    )

    body = json.loads(response.get_body())
    assert response.status_code == 200
    assert body["ticket"]["status"] == "PendingApproval"
    assert body["recommendation"]["recommended_team"] == "Identity"
    assert body["agent_results"] == []
    assert body["approval"] is None
    assert body["audit_events"][0]["event_type"] == "TicketSubmitted"
    assert body["workflow_instance_id"] == "workflow-001"


def test_ticket_detail_endpoint_returns_not_found(
    tmp_path: Path, monkeypatch
) -> None:
    _stored_ticket(tmp_path / "ticket-missing.db", monkeypatch)

    response = get_ticket_detail_request(FakeRequest({}, {"ticket_id": "missing"}))

    assert response.status_code == 404


def test_ui_asset_handler_serves_index_html() -> None:
    response = serve_ui_asset("index.html")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert b"AI Support Ticket Decision Gate" in response.get_body()


def test_fail_next_assignment_endpoint_arms_one_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "demo-control.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))

    response = arm_fail_next_assignment_request(FakeRequest({}))

    assert response.status_code == 200
    assert json.loads(response.get_body()) == {
        "status": "armed",
        "action": "assignment",
    }
    assert consume_fail_next_assignment(database_path) is True
    assert consume_fail_next_assignment(database_path) is False