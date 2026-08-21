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
            "RunMockAgentActivity",
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

    decision_payload = yield context.wait_for_external_event("ApprovalDecision")
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

    return {"ticket_id": ticket_id, "status": terminal_status}


@durable_blueprint.orchestration_trigger(
    context_name="context", orchestration="ticket_decision_orchestrator"
)
def ticket_decision_orchestrator(context: df.DurableOrchestrationContext):
    return orchestrator_logic(context)