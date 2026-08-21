import builtins
import os
import random
import socket
import uuid

import app.activities_blueprint as activities
import function_app
from app.durable_blueprint import AGENT_NAMES, orchestrator_logic


class FakeContext:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.statuses: list[str] = []

    def get_input(self) -> dict:
        return {
            "ticket_id": "ticket-001",
            "workflow_instance_id": "workflow-001",
            "ticket": {"title": "Access failure", "description": "Users receive 403."},
            "created_at": "2026-08-20T12:00:00+00:00",
            "approval_timeout_minutes": 4320,
        }

    def call_activity(self, name: str, payload):
        task = ("activity", name, payload)
        self.calls.append(task)
        return task

    def task_all(self, tasks: list):
        task = ("task_all", tasks)
        self.calls.append(task)
        return task

    def wait_for_external_event(self, name: str):
        task = ("event", name)
        self.calls.append(task)
        return task

    def set_custom_status(self, status: str) -> None:
        self.statuses.append(status)


def run_until_decision(decision: str):
    context = FakeContext()
    orchestrator = orchestrator_logic(context)

    assert next(orchestrator)[1] == "CreateTicketActivity"
    fan_in = orchestrator.send({"ticket_id": "ticket-001"})
    assert fan_in[0] == "task_all"
    assert [task[2]["agent_name"] for task in fan_in[1]] == list(AGENT_NAMES)

    agent_results = [
        {"agent_name": name, "result": {"mock": name}} for name in AGENT_NAMES
    ]
    assert orchestrator.send(agent_results)[1] == "BuildRecommendationActivity"
    recommendation = {"recommended_team": "Identity"}
    assert orchestrator.send(recommendation)[1] == "SavePendingApprovalActivity"
    assert orchestrator.send({"status": "PendingApproval"}) == (
        "event",
        "ApprovalDecision",
    )
    assert orchestrator.send({"decision": decision})[1] == "RecordApprovalActivity"
    final_task = orchestrator.send({"decision": decision})
    assert final_task[1] == "FinalizeDecisionActivity"
    expected = "Approved" if decision == "approve" else "Rejected"
    assert final_task[2]["status"] == expected

    try:
        orchestrator.send({"status": expected})
    except StopIteration as stopped:
        output = stopped.value
    else:
        raise AssertionError("orchestrator did not complete")

    return context, output


def test_orchestrator_fans_out_and_approves() -> None:
    context, output = run_until_decision("approve")
    assert context.statuses == ["Analyzing", "PendingApproval", "Approved"]
    assert output == {"ticket_id": "ticket-001", "status": "Approved"}


def test_orchestrator_rejects_without_assignment() -> None:
    context, output = run_until_decision("reject")
    assert context.statuses == ["Analyzing", "PendingApproval", "Rejected"]
    assert output["status"] == "Rejected"
    assert all("Assignment" not in call[1] for call in context.calls if call[0] == "activity")


def test_orchestrator_replay_has_stable_history() -> None:
    first_context, first_output = run_until_decision("approve")
    second_context, second_output = run_until_decision("approve")

    assert first_context.calls == second_context.calls
    assert first_context.statuses == second_context.statuses
    assert first_output == second_output


def test_failed_agent_fan_out_does_not_build_recommendation() -> None:
    context = FakeContext()
    orchestrator = orchestrator_logic(context)

    next(orchestrator)
    orchestrator.send({"ticket_id": "ticket-001"})

    try:
        orchestrator.throw(RuntimeError("mock agent failed"))
    except RuntimeError as exc:
        assert str(exc) == "mock agent failed"
    else:
        raise AssertionError("agent failure did not fail the orchestration")

    assert all(
        call[1] != "BuildRecommendationActivity"
        for call in context.calls
        if call[0] == "activity"
    )


def test_orchestrator_replay_does_not_touch_io_or_nondeterministic_apis(
    monkeypatch,
) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("orchestrator crossed an I/O or determinism boundary")

    monkeypatch.setattr(builtins, "open", fail)
    monkeypatch.setattr(os, "getenv", fail)
    monkeypatch.setattr(random, "random", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(uuid, "uuid4", fail)
    for function_name in (
        "create_ticket",
        "run_mock_agent",
        "aggregate_recommendation",
        "save_pending_approval",
        "persist_approval",
        "finalize_decision",
    ):
        monkeypatch.setattr(activities, function_name, fail)

    context, output = run_until_decision("approve")

    assert context.statuses[-1] == "Approved"
    assert output["status"] == "Approved"


def test_orchestrator_activity_targets_are_registered_function_names() -> None:
    registered_names = {
        function.get_function_name() for function in function_app.app.get_functions()
    }
    expected_activity_names = {
        "CreateTicketActivity",
        "RunMockAgentActivity",
        "BuildRecommendationActivity",
        "SavePendingApprovalActivity",
        "RecordApprovalActivity",
        "FinalizeDecisionActivity",
    }

    assert expected_activity_names <= registered_names