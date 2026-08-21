import builtins
import os
import random
import socket
import uuid
from datetime import datetime, timedelta, timezone

import app.activities_blueprint as activities
import function_app
from app.durable_blueprint import AGENT_NAMES, orchestrator_logic


class FakeTask:
    def __init__(self, kind: str, name: str, payload=None, retry_options=None) -> None:
        self.kind = kind
        self.name = name
        self.payload = payload
        self.retry_options = retry_options
        self.result = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeContext:
    def __init__(self) -> None:
        self.calls: list[FakeTask] = []
        self.statuses: list[str] = []
        self.current_utc_datetime = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

    def get_input(self) -> dict:
        return {
            "ticket_id": "ticket-001",
            "workflow_instance_id": "workflow-001",
            "ticket": {"title": "Access failure", "description": "Users receive 403."},
            "created_at": "2026-08-20T12:00:00+00:00",
            "approval_timeout_minutes": 4320,
        }

    def call_activity(self, name: str, payload):
        task = FakeTask("activity", name, payload)
        self.calls.append(task)
        return task

    def call_activity_with_retry(self, name: str, retry_options, input_):
        task = FakeTask("retry_activity", name, input_, retry_options)
        self.calls.append(task)
        return task

    def task_all(self, tasks: list):
        task = FakeTask("task_all", "task_all", tasks)
        self.calls.append(task)
        return task

    def wait_for_external_event(self, name: str):
        task = FakeTask("event", name)
        self.calls.append(task)
        return task

    def create_timer(self, fire_at: datetime):
        task = FakeTask("timer", "approval_timeout", fire_at)
        self.calls.append(task)
        return task

    def task_any(self, tasks: list):
        task = FakeTask("task_any", "task_any", tasks)
        self.calls.append(task)
        return task

    def set_custom_status(self, status: str) -> None:
        self.statuses.append(status)


def advance_to_approval_race():
    context = FakeContext()
    orchestrator = orchestrator_logic(context)

    assert next(orchestrator).name == "CreateTicketActivity"
    fan_in = orchestrator.send({"ticket_id": "ticket-001"})
    assert fan_in.kind == "task_all"
    assert [task.payload["agent_name"] for task in fan_in.payload] == list(AGENT_NAMES)

    agent_results = [
        {"agent_name": name, "result": {"mock": name}} for name in AGENT_NAMES
    ]
    assert orchestrator.send(agent_results).name == "BuildRecommendationActivity"
    recommendation = {"recommended_team": "Identity"}
    assert orchestrator.send(recommendation).name == "SavePendingApprovalActivity"
    race = orchestrator.send({"status": "PendingApproval"})
    assert race.kind == "task_any"
    approval_task, timeout_task = race.payload
    assert approval_task.name == "ApprovalDecision"
    assert timeout_task.payload == context.current_utc_datetime + timedelta(minutes=4320)
    return context, orchestrator, approval_task, timeout_task


def run_until_decision(decision: str):
    context, orchestrator, approval_task, timeout_task = advance_to_approval_race()
    approval_task.result = {"decision": decision}
    assert orchestrator.send(approval_task).name == "RecordApprovalActivity"
    assert timeout_task.cancelled is True
    final_task = orchestrator.send({"decision": decision})
    assert final_task.name == "FinalizeDecisionActivity"
    expected = "Approved" if decision == "approve" else "Rejected"
    assert final_task.payload["status"] == expected

    next_result = {"status": expected}
    if decision == "approve":
        assignment_task = orchestrator.send(next_result)
        assert assignment_task.kind == "retry_activity"
        assert assignment_task.name == "ExecuteAssignmentActivity"
        assert assignment_task.payload == {
            "ticket_id": "ticket-001",
            "action_id": "assign:ticket-001",
            "assigned_team": "Identity",
        }
        assert assignment_task.retry_options.first_retry_interval_in_milliseconds == 5_000
        assert assignment_task.retry_options.max_number_of_attempts == 3
        completed_task = orchestrator.send(
            {"action_id": "assign:ticket-001", "assigned_team": "Identity"}
        )
        assert completed_task.name == "FinalizeDecisionActivity"
        assert completed_task.payload["status"] == "Completed"
        next_result = {"status": "Completed"}

    try:
        orchestrator.send(next_result)
    except StopIteration as stopped:
        output = stopped.value
    else:
        raise AssertionError("orchestrator did not complete")

    return context, output


def test_orchestrator_fans_out_and_approves() -> None:
    context, output = run_until_decision("approve")
    assert context.statuses == ["Analyzing", "PendingApproval", "Approved", "Completed"]
    assert output == {"ticket_id": "ticket-001", "status": "Completed"}


def test_orchestrator_rejects_without_assignment() -> None:
    context, output = run_until_decision("reject")
    assert context.statuses == ["Analyzing", "PendingApproval", "Rejected"]
    assert output["status"] == "Rejected"
    assert all("Assignment" not in call.name for call in context.calls if call.kind == "activity")


def test_orchestrator_timeout_is_persisted_and_completes() -> None:
    context, orchestrator, _, timeout_task = advance_to_approval_race()
    timeout_activity = orchestrator.send(timeout_task)
    assert timeout_activity.name == "PersistApprovalTimeoutActivity"
    assert timeout_activity.payload == {
        "ticket_id": "ticket-001",
        "timeout_at": "2026-08-23T12:00:00+00:00",
    }

    try:
        orchestrator.send({"status": "ApprovalTimedOut"})
    except StopIteration as stopped:
        output = stopped.value
    else:
        raise AssertionError("orchestrator did not complete after timeout")

    assert context.statuses[-1] == "ApprovalTimedOut"
    assert output == {"ticket_id": "ticket-001", "status": "ApprovalTimedOut"}


def test_orchestrator_replay_has_stable_history() -> None:
    first_context, first_output = run_until_decision("approve")
    second_context, second_output = run_until_decision("approve")

    def task_signature(task: FakeTask):
        payload = task.payload
        if isinstance(payload, list) and all(
            isinstance(child, FakeTask) for child in payload
        ):
            payload = [task_signature(child) for child in payload]
        return task.kind, task.name, payload, task.cancelled

    assert [task_signature(task) for task in first_context.calls] == [
        task_signature(task) for task in second_context.calls
    ]
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
        call.name != "BuildRecommendationActivity"
        for call in context.calls
        if call.kind == "activity"
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
        "run_foundry_agent",
        "aggregate_recommendation",
        "save_pending_approval",
        "persist_approval",
        "persist_approval_timeout",
        "finalize_decision",
        "execute_assignment",
    ):
        monkeypatch.setattr(activities, function_name, fail)

    context, output = run_until_decision("approve")

    assert context.statuses[-1] == "Completed"
    assert output["status"] == "Completed"


def test_orchestrator_activity_targets_are_registered_function_names() -> None:
    registered_names = {
        function.get_function_name() for function in function_app.app.get_functions()
    }
    expected_activity_names = {
        "CreateTicketActivity",
        "RunFoundryAgentActivity",
        "BuildRecommendationActivity",
        "SavePendingApprovalActivity",
        "RecordApprovalActivity",
        "PersistApprovalTimeoutActivity",
        "FinalizeDecisionActivity",
        "ExecuteAssignmentActivity",
    }

    assert expected_activity_names <= registered_names