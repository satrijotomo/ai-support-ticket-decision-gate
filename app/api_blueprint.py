import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import azure.durable_functions as df
import azure.functions as func
from pydantic import ValidationError

from app.config import AppSettings
from app.db import (
    IdempotencyConflictError,
    append_audit_event,
    get_approval,
    get_ticket,
    list_agent_results,
    list_audit_events,
    list_tickets,
    record_approval,
)
from app.demo_controls import arm_fail_next_assignment
from app.models import (
    ApprovalDecision,
    ApprovalRecord,
    AuditEventRecord,
    DecisionRequest,
    ErrorResponse,
    OrchestrationInput,
    Recommendation,
    TicketAcceptedResponse,
    TicketCreateRequest,
    TicketDetailResponse,
    TicketStatus,
    TicketSummaryResponse,
    WorkflowStatusResponse,
)


api_blueprint = df.Blueprint()
UI_ROOT = Path(__file__).parents[1] / "ui"


def _json_response(payload: dict, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload), status_code=status_code, mimetype="application/json"
    )


async def start_ticket_request(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    try:
        ticket = TicketCreateRequest.model_validate(req.get_json())
        settings = AppSettings.from_environment()
    except (ValueError, ValidationError) as exc:
        error = ErrorResponse(error="Invalid ticket request", details={"reason": str(exc)})
        return _json_response(error.model_dump(mode="json"), 400)

    ticket_id = str(uuid4())
    workflow_instance_id = str(uuid4())
    workflow_input = OrchestrationInput(
        ticket_id=ticket_id,
        workflow_instance_id=workflow_instance_id,
        ticket=ticket,
        created_at=datetime.now(timezone.utc),
        approval_timeout_minutes=settings.approval_timeout_minutes,
    )
    await client.start_new(
        "ticket_decision_orchestrator",
        instance_id=workflow_instance_id,
        client_input=workflow_input.model_dump(mode="json"),
    )
    response = TicketAcceptedResponse(
        ticket_id=ticket_id,
        workflow_instance_id=workflow_instance_id,
        status=TicketStatus.SUBMITTED,
    )
    return _json_response(response.model_dump(mode="json"), 202)


@api_blueprint.route(route="tickets", methods=["POST"])
@api_blueprint.durable_client_input(client_name="client")
async def start_ticket(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    return await start_ticket_request(req, client)


def list_tickets_request(req: func.HttpRequest) -> func.HttpResponse:
    settings = AppSettings.from_environment()
    summaries = [
        TicketSummaryResponse(
            ticket_id=ticket.ticket_id,
            workflow_instance_id=ticket.workflow_instance_id,
            title=ticket.title,
            status=ticket.status,
            assigned_team=ticket.assigned_team,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
        )
        for ticket in list_tickets(settings.database_path)
    ]
    return _json_response(
        [summary.model_dump(mode="json") for summary in summaries], 200
    )


@api_blueprint.route(route="tickets", methods=["GET"])
def get_tickets(req: func.HttpRequest) -> func.HttpResponse:
    return list_tickets_request(req)


def get_ticket_detail_request(req: func.HttpRequest) -> func.HttpResponse:
    settings = AppSettings.from_environment()
    ticket_id = req.route_params.get("ticket_id", "")
    ticket = get_ticket(settings.database_path, ticket_id)
    if ticket is None:
        return _json_response(ErrorResponse(error="Ticket not found").model_dump(), 404)

    recommendation = (
        Recommendation.model_validate_json(ticket.recommendation_json)
        if ticket.recommendation_json
        else None
    )
    detail = TicketDetailResponse(
        ticket=ticket,
        recommendation=recommendation,
        agent_results=list_agent_results(settings.database_path, ticket_id),
        approval=get_approval(settings.database_path, ticket_id),
        audit_events=list_audit_events(settings.database_path, ticket_id),
        workflow_instance_id=ticket.workflow_instance_id,
    )
    return _json_response(detail.model_dump(mode="json"), 200)


@api_blueprint.route(route="tickets/{ticket_id}", methods=["GET"])
def get_ticket_detail(req: func.HttpRequest) -> func.HttpResponse:
    return get_ticket_detail_request(req)


def serve_ui_asset(filename: str) -> func.HttpResponse:
    mimetypes = {
        "index.html": "text/html",
        "app.js": "application/javascript",
        "styles.css": "text/css",
    }
    if filename not in mimetypes:
        return _json_response(ErrorResponse(error="UI asset not found").model_dump(), 404)

    asset_path = UI_ROOT / filename
    if not asset_path.is_file():
        return _json_response(ErrorResponse(error="UI asset not found").model_dump(), 404)
    return func.HttpResponse(
        asset_path.read_bytes(), status_code=200, mimetype=mimetypes[filename]
    )


@api_blueprint.route(route="ui", methods=["GET"])
def serve_ui(req: func.HttpRequest) -> func.HttpResponse:
    return serve_ui_asset("index.html")


@api_blueprint.route(route="ui/app.js", methods=["GET"])
def serve_ui_javascript(req: func.HttpRequest) -> func.HttpResponse:
    return serve_ui_asset("app.js")


@api_blueprint.route(route="ui/styles.css", methods=["GET"])
def serve_ui_styles(req: func.HttpRequest) -> func.HttpResponse:
    return serve_ui_asset("styles.css")


async def submit_decision_request(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    ticket_id = req.route_params.get("ticket_id", "")
    try:
        request = DecisionRequest.model_validate(req.get_json())
    except (ValueError, ValidationError) as exc:
        error = ErrorResponse(error="Invalid approval decision", details={"reason": str(exc)})
        return _json_response(error.model_dump(mode="json"), 400)

    settings = AppSettings.from_environment()
    ticket = get_ticket(settings.database_path, ticket_id)
    if ticket is None:
        return _json_response(ErrorResponse(error="Ticket not found").model_dump(), 404)
    if ticket.status is not TicketStatus.PENDING_APPROVAL:
        return _json_response(
            ErrorResponse(error="Ticket is not pending approval").model_dump(), 409
        )

    decided_at = datetime.now(timezone.utc)
    decision = ApprovalDecision(
        decision_id=str(uuid4()),
        decision=request.decision,
        approver=request.approver,
        comments=request.comments,
        decided_at=decided_at,
    )
    try:
        stored_approval = record_approval(
            settings.database_path,
            ApprovalRecord(
                approval_id=decision.decision_id,
                decision_id=decision.decision_id,
                ticket_id=ticket_id,
                approver=decision.approver,
                decision=decision.decision,
                comments=decision.comments,
                decided_at=decision.decided_at,
            ),
        )
    except IdempotencyConflictError:
        stored_approval = get_approval(settings.database_path, ticket_id)
        if stored_approval is None or (
            stored_approval.decision != request.decision
            or stored_approval.approver != request.approver
            or (stored_approval.comments or "") != request.comments
        ):
            return _json_response(
                ErrorResponse(
                    error="An approval decision was already submitted"
                ).model_dump(),
                409,
            )
        decision = ApprovalDecision(
            decision_id=stored_approval.decision_id,
            decision=stored_approval.decision,
            approver=stored_approval.approver,
            comments=stored_approval.comments or "",
            decided_at=stored_approval.decided_at,
        )
    append_audit_event(
        settings.database_path,
        AuditEventRecord(
            event_id=f"audit:{ticket_id}:decision-received",
            ticket_id=ticket_id,
            event_type="ApprovalDecisionReceived",
            event_data=json.dumps(
                {"decision": decision.decision, "approver": decision.approver},
                sort_keys=True,
            ),
            dedupe_key=f"{ticket_id}:decision-received",
            occurred_at=decided_at,
        ),
    )
    await client.raise_event(
        ticket.workflow_instance_id,
        "ApprovalDecision",
        decision.model_dump(mode="json"),
    )
    return _json_response(decision.model_dump(mode="json"), 202)


@api_blueprint.route(route="tickets/{ticket_id}/decision", methods=["POST"])
@api_blueprint.durable_client_input(client_name="client")
async def submit_decision(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    return await submit_decision_request(req, client)


def arm_fail_next_assignment_request(req: func.HttpRequest) -> func.HttpResponse:
    settings = AppSettings.from_environment()
    arm_fail_next_assignment(settings.database_path)
    return _json_response({"status": "armed", "action": "assignment"}, 200)


@api_blueprint.route(route="demo/fail-next-action", methods=["POST"])
def arm_fail_next_assignment_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    return arm_fail_next_assignment_request(req)


def _runtime_status_value(runtime_status) -> str:
    return str(getattr(runtime_status, "value", runtime_status))


async def get_workflow_status_request(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    instance_id = req.route_params.get("instance_id", "")
    status = await client.get_status(instance_id)
    if status is None:
        return _json_response(ErrorResponse(error="Workflow not found").model_dump(), 404)

    response = WorkflowStatusResponse(
        instance_id=status.instance_id,
        runtime_status=_runtime_status_value(status.runtime_status),
        custom_status=status.custom_status,
        created_time=status.created_time,
        updated_time=status.last_updated_time,
        output=status.output,
    )
    return _json_response(response.model_dump(mode="json"), 200)


@api_blueprint.route(route="workflows/{instance_id}", methods=["GET"])
@api_blueprint.durable_client_input(client_name="client")
async def get_workflow_status(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    return await get_workflow_status_request(req, client)