from datetime import timedelta

import azure.durable_functions as df


durable_blueprint = df.Blueprint()

AGENT_NAMES = (
    "support-triage-agent",
    "support-knowledge-agent",
    "support-risk-reviewer-agent",
)


def orchestrator_logic(context: df.DurableOrchestrationContext):
    workflow_input = context.get_input()
    ticket_id = workflow_input["ticket_id"]

    yield context.call_activity("CreateTicketActivity", workflow_input)
    context.set_custom_status("Analyzing")

    agent_tasks = [
        context.call_activity(
            "RunFoundryAgentActivity",
            {
                "ticket_id": ticket_id,
                "agent_name": agent_name,
                "ticket": workflow_input["ticket"],
            },
        )
        for agent_name in AGENT_NAMES
    ]
    agent_results = yield context.task_all(agent_tasks)
    recommendation = yield context.call_activity(
        "BuildRecommendationActivity", agent_results
    )
    yield context.call_activity(
        "SavePendingApprovalActivity",
        {"ticket_id": ticket_id, "recommendation": recommendation},
    )
    context.set_custom_status("PendingApproval")

    approval_task = context.wait_for_external_event("ApprovalDecision")
    timeout_at = context.current_utc_datetime + timedelta(
        minutes=workflow_input["approval_timeout_minutes"]
    )
    timeout_task = context.create_timer(timeout_at)
    winner = yield context.task_any([approval_task, timeout_task])
    if winner == timeout_task:
        yield context.call_activity(
            "PersistApprovalTimeoutActivity",
            {"ticket_id": ticket_id, "timeout_at": timeout_at.isoformat()},
        )
        context.set_custom_status("ApprovalTimedOut")
        return {"ticket_id": ticket_id, "status": "ApprovalTimedOut"}

    timeout_task.cancel()
    decision_payload = approval_task.result
    decision = yield context.call_activity(
        "RecordApprovalActivity",
        {"ticket_id": ticket_id, "decision": decision_payload},
    )
    terminal_status = "Approved" if decision["decision"] == "approve" else "Rejected"
    yield context.call_activity(
        "FinalizeDecisionActivity",
        {"ticket_id": ticket_id, "status": terminal_status},
    )
    context.set_custom_status(terminal_status)

    if terminal_status == "Approved":
        retry_options = df.RetryOptions(
            first_retry_interval_in_milliseconds=5_000,
            max_number_of_attempts=3,
        )
        yield context.call_activity_with_retry(
            "ExecuteAssignmentActivity",
            retry_options,
            {
                "ticket_id": ticket_id,
                "action_id": f"assign:{ticket_id}",
                "assigned_team": recommendation["recommended_team"],
            },
        )
        yield context.call_activity(
            "FinalizeDecisionActivity",
            {"ticket_id": ticket_id, "status": "Completed"},
        )
        context.set_custom_status("Completed")
        terminal_status = "Completed"

    return {"ticket_id": ticket_id, "status": terminal_status}


@durable_blueprint.orchestration_trigger(
    context_name="context", orchestration="ticket_decision_orchestrator"
)
def ticket_decision_orchestrator(context: df.DurableOrchestrationContext):
    return orchestrator_logic(context)