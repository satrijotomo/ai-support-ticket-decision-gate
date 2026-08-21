import json
from datetime import datetime, timezone
from uuid import uuid4

import azure.durable_functions as df
import azure.functions as func
from pydantic import ValidationError

from app.config import AppSettings
from app.db import get_ticket
from app.models import (
    ApprovalDecision,
    DecisionRequest,
    ErrorResponse,
    OrchestrationInput,
    TicketAcceptedResponse,
    TicketCreateRequest,
    TicketStatus,
)


api_blueprint = df.Blueprint()


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

    decision = ApprovalDecision(
        decision_id=str(uuid4()),
        decision=request.decision,
        approver=request.approver,
        comments=request.comments,
        decided_at=datetime.now(timezone.utc),
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