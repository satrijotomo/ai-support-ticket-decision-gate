import os
import time
from uuid import uuid4

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_E2E", "").lower() not in {"1", "true", "yes"},
    reason="set RUN_E2E=true to run tests against a local Function App",
)

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:7071/api").rstrip("/")
POLL_INTERVAL_SECONDS = 0.5
WORKFLOW_TIMEOUT_SECONDS = 90


def _request(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    response = client.request(method, f"{BASE_URL}{path}", **kwargs)
    response.raise_for_status()
    return response


def _submit_ticket(client: httpx.Client, scenario: str) -> dict:
    suffix = uuid4().hex[:8]
    response = _request(
        client,
        "POST",
        "/tickets",
        json={
            "title": f"E2E {scenario} access failure {suffix}",
            "description": "Multiple test users receive 403 responses after deployment.",
            "affected_service": "Payroll Portal",
            "customer_impact": "Test users are blocked",
            "submitted_by": "phase8.e2e@contoso.com",
        },
    )
    assert response.status_code == 202
    return response.json()


def _wait_for_ticket_status(
    client: httpx.Client, ticket_id: str, expected_status: str
) -> dict:
    deadline = time.monotonic() + WORKFLOW_TIMEOUT_SECONDS
    last_status = None
    while time.monotonic() < deadline:
        response = client.get(f"{BASE_URL}/tickets/{ticket_id}")
        if response.status_code == 404:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        response.raise_for_status()
        detail = response.json()
        last_status = detail["ticket"]["status"]
        if last_status == expected_status:
            return detail
        if last_status in {"ApprovalTimedOut", "Failed"}:
            pytest.fail(
                f"ticket {ticket_id} reached terminal status {last_status}; "
                f"expected {expected_status}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(
        f"ticket {ticket_id} remained {last_status}; expected {expected_status} "
        f"within {WORKFLOW_TIMEOUT_SECONDS} seconds"
    )


def _assert_workflow_completed(
    client: httpx.Client, instance_id: str, expected_status: str
) -> None:
    workflow = _request(client, "GET", f"/workflows/{instance_id}").json()
    assert workflow["runtime_status"] == "Completed"
    assert workflow["custom_status"] == expected_status
    assert workflow["output"]["status"] == expected_status


def test_approve_flow_retries_assignment_and_completes() -> None:
    with httpx.Client(timeout=10) as client:
        ui = _request(client, "GET", "/ui")
        assert "AI Support Ticket Decision Gate" in ui.text

        accepted = _submit_ticket(client, "approval")
        pending = _wait_for_ticket_status(
            client, accepted["ticket_id"], "PendingApproval"
        )
        assert len(pending["agent_results"]) == 3
        assert pending["recommendation"] is not None

        _request(client, "POST", "/demo/fail-next-action", json={})
        decision = _request(
            client,
            "POST",
            f"/tickets/{accepted['ticket_id']}/decision",
            json={
                "decision": "approve",
                "approver": "Phase 8 E2E",
                "comments": "Approve the retry-path test.",
            },
        )
        assert decision.status_code == 202

        completed = _wait_for_ticket_status(
            client, accepted["ticket_id"], "Completed"
        )
        assert completed["approval"]["decision"] == "approve"
        assert completed["ticket"]["assigned_team"] == completed["recommendation"][
            "recommended_team"
        ]
        event_types = [event["event_type"] for event in completed["audit_events"]]
        assert event_types.count("AssignmentCompleted") == 1
        assert event_types.count("WorkflowCompleted") == 1
        _assert_workflow_completed(
            client, accepted["workflow_instance_id"], "Completed"
        )


def test_reject_flow_completes_without_assignment() -> None:
    with httpx.Client(timeout=10) as client:
        accepted = _submit_ticket(client, "rejection")
        _wait_for_ticket_status(client, accepted["ticket_id"], "PendingApproval")

        decision = _request(
            client,
            "POST",
            f"/tickets/{accepted['ticket_id']}/decision",
            json={
                "decision": "reject",
                "approver": "Phase 8 E2E",
                "comments": "Reject the alternate-path test.",
            },
        )
        assert decision.status_code == 202

        rejected = _wait_for_ticket_status(
            client, accepted["ticket_id"], "Rejected"
        )
        assert rejected["approval"]["decision"] == "reject"
        assert rejected["ticket"]["assigned_team"] is None
        event_types = [event["event_type"] for event in rejected["audit_events"]]
        assert "WorkflowRejected" in event_types
        assert "AssignmentCompleted" not in event_types
        _assert_workflow_completed(
            client, accepted["workflow_instance_id"], "Rejected"
        )