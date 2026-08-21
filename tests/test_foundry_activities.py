import json
from pathlib import Path

import pytest

import app.activities_blueprint as activities
from app.activities_blueprint import AgentActivityError, create_ticket, run_foundry_agent
from app.db import list_agent_results, list_audit_events
from app.foundry_client import FoundryClientError
from app.recommendation import KNOWLEDGE_AGENT, RISK_AGENT, TRIAGE_AGENT


VALID_RESULTS = {
    TRIAGE_AGENT: {
        "category": "IdentityAccess",
        "priority": "P2",
        "recommended_team": "Identity",
        "summary": "Several users are blocked by authorization failures.",
        "reasoning": ["The ticket reports HTTP 403 responses."],
        "confidence": 0.91,
    },
    KNOWLEDGE_AGENT: {
        "likely_issue": "A recent authorization configuration may have changed.",
        "suggested_steps": ["Review recent access policy changes."],
        "missing_information": ["The deployment change identifier"],
        "draft_response": "We are reviewing the authorization failures.",
        "confidence": 0.82,
    },
    RISK_AGENT: {
        "risk_level": "medium",
        "approval_required": True,
        "concerns": ["An access change could affect additional users."],
        "required_escalation": "Identity",
        "review_notes": "Review the proposed access change before execution.",
        "confidence": 0.87,
    },
}

CONFIGURED_NAMES = {
    TRIAGE_AGENT: "configured-triage",
    KNOWLEDGE_AGENT: "configured-knowledge",
    RISK_AGENT: "configured-risk",
}


def workflow_input() -> dict:
    return {
        "ticket_id": "ticket-foundry",
        "workflow_instance_id": "workflow-foundry",
        "ticket": {
            "title": "Access failure",
            "description": "Multiple users receive 403 responses.",
            "affected_service": "Payroll Portal",
        },
        "created_at": "2026-08-20T12:00:00+00:00",
        "approval_timeout_minutes": 4320,
    }


def configure_live_mode(monkeypatch, database_path: Path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("FOUNDRY_MOCK_MODE", "false")
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT",
        "https://example.services.ai.azure.com/api/projects/demo",
    )
    monkeypatch.setenv("TRIAGE_AGENT_NAME", CONFIGURED_NAMES[TRIAGE_AGENT])
    monkeypatch.setenv("KNOWLEDGE_AGENT_NAME", CONFIGURED_NAMES[KNOWLEDGE_AGENT])
    monkeypatch.setenv("RISK_AGENT_NAME", CONFIGURED_NAMES[RISK_AGENT])


@pytest.mark.parametrize("agent_role", [TRIAGE_AGENT, KNOWLEDGE_AGENT, RISK_AGENT])
def test_foundry_activity_routes_role_validates_and_persists(
    tmp_path: Path, monkeypatch, agent_role: str
) -> None:
    database_path = tmp_path / f"{agent_role}.db"
    configure_live_mode(monkeypatch, database_path)
    create_ticket(workflow_input())
    calls: list[tuple[str, str, str]] = []

    def invoke(project_endpoint: str, agent_name: str, prompt: str) -> str:
        calls.append((project_endpoint, agent_name, prompt))
        return json.dumps(VALID_RESULTS[agent_role])

    monkeypatch.setattr(activities, "invoke_prompt_agent", invoke)

    envelope = run_foundry_agent(
        {
            "ticket_id": "ticket-foundry",
            "agent_name": agent_role,
            "ticket": workflow_input()["ticket"],
        }
    )

    assert envelope == {"agent_name": agent_role, "result": VALID_RESULTS[agent_role]}
    assert len(calls) == 1
    project_endpoint, configured_name, raw_prompt = calls[0]
    assert project_endpoint.startswith("https://example.services.ai.azure.com/")
    assert configured_name == CONFIGURED_NAMES[agent_role]
    prompt = json.loads(raw_prompt)
    assert prompt["ticket"] == workflow_input()["ticket"]
    assert ("support_playbook" in prompt) is (agent_role == KNOWLEDGE_AGENT)

    stored = list_agent_results(database_path, "ticket-foundry")
    assert len(stored) == 1
    assert stored[0].agent_name == CONFIGURED_NAMES[agent_role]
    assert json.loads(stored[0].result_json) == VALID_RESULTS[agent_role]


@pytest.mark.parametrize("raw_output", ["not JSON", "{}"])
def test_foundry_activity_rejects_invalid_output_before_persistence(
    tmp_path: Path, monkeypatch, raw_output: str
) -> None:
    database_path = tmp_path / "invalid.db"
    configure_live_mode(monkeypatch, database_path)
    create_ticket(workflow_input())
    monkeypatch.setattr(
        activities,
        "invoke_prompt_agent",
        lambda project_endpoint, agent_name, prompt: raw_output,
    )

    with pytest.raises(AgentActivityError) as raised:
        run_foundry_agent(
            {
                "ticket_id": "ticket-foundry",
                "agent_name": TRIAGE_AGENT,
                "ticket": workflow_input()["ticket"],
            }
        )

    assert str(raised.value) == "Agent returned an invalid response."
    assert list_agent_results(database_path, "ticket-foundry") == []
    assert [event.event_type for event in list_audit_events(database_path, "ticket-foundry")] == [
        "TicketSubmitted"
    ]


def test_foundry_activity_sanitizes_service_errors(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "service-error.db"
    configure_live_mode(monkeypatch, database_path)
    create_ticket(workflow_input())
    secret = "sensitive-service-detail"

    def fail(project_endpoint: str, agent_name: str, prompt: str) -> str:
        raise FoundryClientError(f"service rejected token {secret}")

    monkeypatch.setattr(activities, "invoke_prompt_agent", fail)

    with pytest.raises(AgentActivityError) as raised:
        run_foundry_agent(
            {
                "ticket_id": "ticket-foundry",
                "agent_name": RISK_AGENT,
                "ticket": workflow_input()["ticket"],
            }
        )

    assert str(raised.value) == "Agent service is unavailable."
    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None
    assert list_agent_results(database_path, "ticket-foundry") == []


def test_foundry_activity_is_idempotent_with_mocked_responses(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "idempotent.db"
    configure_live_mode(monkeypatch, database_path)
    create_ticket(workflow_input())
    monkeypatch.setattr(
        activities,
        "invoke_prompt_agent",
        lambda project_endpoint, agent_name, prompt: json.dumps(
            VALID_RESULTS[TRIAGE_AGENT]
        ),
    )
    payload = {
        "ticket_id": "ticket-foundry",
        "agent_name": TRIAGE_AGENT,
        "ticket": workflow_input()["ticket"],
    }

    first = run_foundry_agent(payload)
    second = run_foundry_agent(payload)

    assert first == second
    assert len(list_agent_results(database_path, "ticket-foundry")) == 1
    assert [
        event.event_type
        for event in list_audit_events(database_path, "ticket-foundry")
    ].count("AgentCompleted") == 1


def test_mock_mode_never_calls_foundry(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "offline.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("FOUNDRY_MOCK_MODE", "true")
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    create_ticket(workflow_input())

    def fail(*args, **kwargs):
        raise AssertionError("mock mode called Foundry")

    monkeypatch.setattr(activities, "invoke_prompt_agent", fail)

    envelope = run_foundry_agent(
        {
            "ticket_id": "ticket-foundry",
            "agent_name": KNOWLEDGE_AGENT,
            "ticket": workflow_input()["ticket"],
        }
    )

    assert envelope["agent_name"] == KNOWLEDGE_AGENT
    assert envelope["result"]["confidence"] == 0.75
