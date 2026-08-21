import asyncio
import json
from datetime import datetime
from pathlib import Path

from app.activities_blueprint import create_ticket
from app.api_blueprint import start_ticket_request, submit_decision_request
from app.db import update_ticket_status
from app.models import TicketStatus


class FakeRequest:
    def __init__(self, body, route_params: dict[str, str] | None = None) -> None:
        self.body = body
        self.route_params = route_params or {}

    def get_json(self):
        return self.body


class FakeClient:
    def __init__(self) -> None:
        self.starts: list[tuple] = []
        self.events: list[tuple] = []

    async def start_new(self, name, instance_id=None, client_input=None):
        self.starts.append((name, instance_id, client_input))
        return instance_id

    async def raise_event(self, instance_id, event_name, event_data=None):
        self.events.append((instance_id, event_name, event_data))


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