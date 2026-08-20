import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.models import (
    AgentResultRecord,
    ApprovalRecord,
    AuditEventRecord,
    TicketRecord,
    TicketStatus,
)


ROOT = Path(__file__).parents[1]
SCHEMA_PATH = ROOT / "sql" / "schema.sql"
BUSY_TIMEOUT_MILLISECONDS = 5_000


class IdempotencyConflictError(RuntimeError):
    pass


def _timestamp(value: datetime) -> str:
    return value.isoformat()


@contextmanager
def database_connection(database_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MILLISECONDS / 1_000)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MILLISECONDS}")
        yield connection
    finally:
        connection.close()


def initialize_database(database_path: str | Path) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with database_connection(database_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(schema)


def _ticket_from_row(row: sqlite3.Row | None) -> TicketRecord | None:
    return TicketRecord.model_validate(dict(row)) if row is not None else None


def upsert_ticket(database_path: str | Path, ticket: TicketRecord) -> TicketRecord:
    values = ticket.model_dump(mode="json")
    with database_connection(database_path) as connection, connection:
        connection.execute(
            """
            INSERT INTO Tickets (
                ticket_id, workflow_instance_id, title, description,
                affected_service, customer_impact, submitted_by, status,
                recommendation_json, assigned_team, action_id, error_message,
                created_at, updated_at
            ) VALUES (
                :ticket_id, :workflow_instance_id, :title, :description,
                :affected_service, :customer_impact, :submitted_by, :status,
                :recommendation_json, :assigned_team, :action_id, :error_message,
                :created_at, :updated_at
            )
            ON CONFLICT(ticket_id) DO UPDATE SET
                workflow_instance_id = excluded.workflow_instance_id,
                title = excluded.title,
                description = excluded.description,
                affected_service = excluded.affected_service,
                customer_impact = excluded.customer_impact,
                submitted_by = excluded.submitted_by,
                status = excluded.status,
                recommendation_json = excluded.recommendation_json,
                assigned_team = excluded.assigned_team,
                action_id = excluded.action_id,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            """,
            values,
        )
    stored = get_ticket(database_path, ticket.ticket_id)
    if stored is None:
        raise RuntimeError("ticket UPSERT did not produce a row")
    return stored


def get_ticket(database_path: str | Path, ticket_id: str) -> TicketRecord | None:
    with database_connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM Tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
    return _ticket_from_row(row)


def list_tickets(database_path: str | Path) -> list[TicketRecord]:
    with database_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM Tickets ORDER BY created_at DESC"
        ).fetchall()
    return [TicketRecord.model_validate(dict(row)) for row in rows]


def update_ticket_status(
    database_path: str | Path,
    ticket_id: str,
    status: TicketStatus,
    updated_at: datetime,
    *,
    recommendation_json: str | None = None,
    error_message: str | None = None,
) -> TicketRecord:
    with database_connection(database_path) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE Tickets
            SET status = ?, updated_at = ?,
                recommendation_json = COALESCE(?, recommendation_json),
                error_message = COALESCE(?, error_message)
            WHERE ticket_id = ?
            """,
            (
                status.value,
                _timestamp(updated_at),
                recommendation_json,
                error_message,
                ticket_id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"ticket not found: {ticket_id}")
    stored = get_ticket(database_path, ticket_id)
    if stored is None:
        raise RuntimeError("ticket status update did not produce a row")
    return stored


def upsert_agent_result(
    database_path: str | Path, result: AgentResultRecord
) -> AgentResultRecord:
    values = result.model_dump(mode="json")
    with database_connection(database_path) as connection, connection:
        connection.execute(
            """
            INSERT INTO AgentResults (
                result_id, ticket_id, agent_name, result_json, confidence,
                started_at, completed_at
            ) VALUES (
                :result_id, :ticket_id, :agent_name, :result_json, :confidence,
                :started_at, :completed_at
            )
            ON CONFLICT(ticket_id, agent_name) DO UPDATE SET
                result_json = excluded.result_json,
                confidence = excluded.confidence,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at
            """,
            values,
        )
        row = connection.execute(
            "SELECT * FROM AgentResults WHERE ticket_id = ? AND agent_name = ?",
            (result.ticket_id, result.agent_name),
        ).fetchone()
    if row is None:
        raise RuntimeError("agent-result UPSERT did not produce a row")
    return AgentResultRecord.model_validate(dict(row))


def list_agent_results(
    database_path: str | Path, ticket_id: str
) -> list[AgentResultRecord]:
    with database_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM AgentResults WHERE ticket_id = ? ORDER BY agent_name",
            (ticket_id,),
        ).fetchall()
    return [AgentResultRecord.model_validate(dict(row)) for row in rows]


def record_approval(
    database_path: str | Path, approval: ApprovalRecord
) -> ApprovalRecord:
    values = approval.model_dump(mode="json")
    with database_connection(database_path) as connection, connection:
        connection.execute(
            """
            INSERT INTO Approvals (
                approval_id, decision_id, ticket_id, approver,
                decision, comments, decided_at
            ) VALUES (
                :approval_id, :decision_id, :ticket_id, :approver,
                :decision, :comments, :decided_at
            )
            ON CONFLICT(decision_id) DO NOTHING
            """,
            values,
        )
        row = connection.execute(
            "SELECT * FROM Approvals WHERE decision_id = ?",
            (approval.decision_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("approval insert did not produce a row")
    return ApprovalRecord.model_validate(dict(row))


def get_approval(
    database_path: str | Path, ticket_id: str
) -> ApprovalRecord | None:
    with database_connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM Approvals WHERE ticket_id = ? ORDER BY decided_at DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
    return ApprovalRecord.model_validate(dict(row)) if row is not None else None


def append_audit_event(
    database_path: str | Path, event: AuditEventRecord
) -> AuditEventRecord:
    values = event.model_dump(mode="json")
    with database_connection(database_path) as connection, connection:
        connection.execute(
            """
            INSERT INTO AuditEvents (
                event_id, ticket_id, event_type, event_data,
                dedupe_key, occurred_at
            ) VALUES (
                :event_id, :ticket_id, :event_type, :event_data,
                :dedupe_key, :occurred_at
            )
            ON CONFLICT(dedupe_key) DO NOTHING
            """,
            values,
        )
        row = connection.execute(
            "SELECT * FROM AuditEvents WHERE dedupe_key = ?", (event.dedupe_key,)
        ).fetchone()
    if row is None:
        raise RuntimeError("audit-event insert did not produce a row")
    return AuditEventRecord.model_validate(dict(row))


def list_audit_events(
    database_path: str | Path, ticket_id: str
) -> list[AuditEventRecord]:
    with database_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM AuditEvents
            WHERE ticket_id = ?
            ORDER BY occurred_at, event_id
            """,
            (ticket_id,),
        ).fetchall()
    return [AuditEventRecord.model_validate(dict(row)) for row in rows]


def record_assignment(
    database_path: str | Path,
    ticket_id: str,
    action_id: str,
    assigned_team: str,
    updated_at: datetime,
) -> TicketRecord:
    with database_connection(database_path) as connection, connection:
        row = connection.execute(
            "SELECT * FROM Tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"ticket not found: {ticket_id}")
        existing = TicketRecord.model_validate(dict(row))
        if existing.action_id is not None:
            if existing.action_id == action_id and existing.assigned_team == assigned_team:
                return existing
            raise IdempotencyConflictError(
                f"ticket {ticket_id} already has action {existing.action_id}"
            )
        cursor = connection.execute(
            """
            UPDATE Tickets
            SET action_id = ?, assigned_team = ?, updated_at = ?
            WHERE ticket_id = ? AND action_id IS NULL
            """,
            (action_id, assigned_team, _timestamp(updated_at), ticket_id),
        )
        if cursor.rowcount != 1:
            current_row = connection.execute(
                "SELECT * FROM Tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            current = TicketRecord.model_validate(dict(current_row))
            if current.action_id == action_id and current.assigned_team == assigned_team:
                return current
            raise IdempotencyConflictError(
                f"ticket {ticket_id} already has action {current.action_id}"
            )
    stored = get_ticket(database_path, ticket_id)
    if stored is None:
        raise RuntimeError("assignment update did not produce a row")
    return stored