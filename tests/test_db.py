import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db import (
    BUSY_TIMEOUT_MILLISECONDS,
    IdempotencyConflictError,
    append_audit_event,
    consume_demo_control,
    database_connection,
    get_approval,
    get_ticket,
    initialize_database,
    list_agent_results,
    list_audit_events,
    record_approval,
    record_assignment,
    set_demo_control,
    update_ticket_status,
    upsert_agent_result,
    upsert_ticket,
)
from app.models import (
    AgentResultRecord,
    ApprovalRecord,
    AuditEventRecord,
    TicketRecord,
    TicketStatus,
)


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def test_schema_initialization_is_repeatable_and_exact(database_path: Path) -> None:
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert tables == {
        "Tickets",
        "AgentResults",
        "Approvals",
        "AuditEvents",
        "DemoControls",
    }
    assert {
        "IX_Tickets_Status",
        "IX_AgentResults_TicketId",
        "IX_Approvals_TicketId",
        "UX_Approvals_TicketId",
        "IX_AuditEvents_TicketId_OccurredAt",
    } <= indexes
    assert journal_mode == "wal"
    assert busy_timeout == BUSY_TIMEOUT_MILLISECONDS
    assert foreign_keys == 1


def test_ticket_creation_is_idempotent_and_parameterized(
    database_path: Path, ticket: TicketRecord
) -> None:
    ticket = ticket.model_copy(
        update={"title": "Robert'); DROP TABLE Tickets;--"}
    )
    first = upsert_ticket(database_path, ticket)
    second = upsert_ticket(database_path, ticket)

    assert first == second
    assert get_ticket(database_path, ticket.ticket_id).title == ticket.title


def test_status_update_changes_only_requested_state(
    database_path: Path, ticket: TicketRecord
) -> None:
    upsert_ticket(database_path, ticket)
    updated = update_ticket_status(
        database_path,
        ticket.ticket_id,
        TicketStatus.ANALYZING,
        NOW + timedelta(minutes=1),
        recommendation_json='{"priority":"P2"}',
    )

    assert updated.status is TicketStatus.ANALYZING
    assert updated.recommendation_json == '{"priority":"P2"}'
    assert updated.title == ticket.title


def test_agent_result_upsert_uses_ticket_and_agent_key(
    database_path: Path, ticket: TicketRecord
) -> None:
    upsert_ticket(database_path, ticket)
    original = AgentResultRecord(
        result_id="result-1",
        ticket_id=ticket.ticket_id,
        agent_name="support-triage-agent",
        result_json='{"priority":"P3"}',
        confidence=0.6,
        started_at=NOW,
        completed_at=NOW,
    )
    upsert_agent_result(database_path, original)
    replacement = original.model_copy(
        update={"result_id": "result-2", "result_json": '{"priority":"P2"}', "confidence": 0.9}
    )
    stored = upsert_agent_result(database_path, replacement)

    assert stored.result_id == "result-1"
    assert stored.result_json == '{"priority":"P2"}'
    assert len(list_agent_results(database_path, ticket.ticket_id)) == 1


def test_duplicate_approval_returns_original_record(
    database_path: Path, ticket: TicketRecord
) -> None:
    upsert_ticket(database_path, ticket)
    original = ApprovalRecord(
        approval_id="approval-1",
        decision_id="decision-1",
        ticket_id=ticket.ticket_id,
        approver="Bambang",
        decision="approve",
        comments="Approved",
        decided_at=NOW,
    )
    first = record_approval(database_path, original)
    duplicate = original.model_copy(
        update={"approval_id": "approval-2", "comments": "Changed"}
    )
    second = record_approval(database_path, duplicate)

    assert second == first
    assert get_approval(database_path, ticket.ticket_id) == original


def test_second_approval_for_ticket_is_rejected(
    database_path: Path, ticket: TicketRecord
) -> None:
    upsert_ticket(database_path, ticket)
    original = ApprovalRecord(
        approval_id="approval-1",
        decision_id="decision-1",
        ticket_id=ticket.ticket_id,
        approver="Bambang",
        decision="approve",
        decided_at=NOW,
    )
    record_approval(database_path, original)

    with pytest.raises(IdempotencyConflictError):
        record_approval(
            database_path,
            original.model_copy(
                update={
                    "approval_id": "approval-2",
                    "decision_id": "decision-2",
                    "decision": "reject",
                }
            ),
        )

    assert get_approval(database_path, ticket.ticket_id) == original


def test_audit_event_deduplicates_and_orders_events(
    database_path: Path, ticket: TicketRecord
) -> None:
    upsert_ticket(database_path, ticket)
    later = AuditEventRecord(
        event_id="event-2",
        ticket_id=ticket.ticket_id,
        event_type="Analyzing",
        dedupe_key="ticket-001:analyzing",
        occurred_at=NOW + timedelta(minutes=1),
    )
    earlier = AuditEventRecord(
        event_id="event-1",
        ticket_id=ticket.ticket_id,
        event_type="Submitted",
        dedupe_key="ticket-001:submitted",
        occurred_at=NOW,
    )
    append_audit_event(database_path, later)
    append_audit_event(database_path, earlier)
    duplicate = earlier.model_copy(update={"event_id": "event-3", "event_data": "changed"})

    assert append_audit_event(database_path, duplicate) == earlier
    assert [event.event_id for event in list_audit_events(database_path, ticket.ticket_id)] == [
        "event-1",
        "event-2",
    ]


def test_concurrent_audit_retries_create_one_event(
    database_path: Path, ticket: TicketRecord
) -> None:
    upsert_ticket(database_path, ticket)

    def append_retry(number: int) -> AuditEventRecord:
        return append_audit_event(
            database_path,
            AuditEventRecord(
                event_id=f"event-{number}",
                ticket_id=ticket.ticket_id,
                event_type="Submitted",
                dedupe_key="ticket-001:concurrent-submitted",
                occurred_at=NOW,
            ),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        stored = list(executor.map(append_retry, range(4)))

    assert len({event.event_id for event in stored}) == 1
    assert len(list_audit_events(database_path, ticket.ticket_id)) == 1


def test_assignment_action_is_idempotent_and_conflicts_are_rejected(
    database_path: Path, ticket: TicketRecord
) -> None:
    upsert_ticket(database_path, ticket)
    first = record_assignment(
        database_path, ticket.ticket_id, "assign:ticket-001", "Identity", NOW
    )
    repeated = record_assignment(
        database_path,
        ticket.ticket_id,
        "assign:ticket-001",
        "Identity",
        NOW + timedelta(minutes=1),
    )

    assert repeated == first
    with pytest.raises(IdempotencyConflictError):
        record_assignment(
            database_path,
            ticket.ticket_id,
            "assign:ticket-001:other",
            "ServiceDesk",
            NOW,
        )


def test_demo_control_is_consumed_exactly_once_under_concurrency(
    database_path: Path,
) -> None:
    set_demo_control(database_path, "fail-next-assignment", armed=True)

    with ThreadPoolExecutor(max_workers=4) as executor:
        consumed = list(
            executor.map(
                lambda _: consume_demo_control(
                    database_path, "fail-next-assignment"
                ),
                range(4),
            )
        )

    assert consumed.count(True) == 1
    assert consumed.count(False) == 3


def test_foreign_key_failure_rolls_back(database_path: Path) -> None:
    result = AgentResultRecord(
        result_id="result-1",
        ticket_id="missing-ticket",
        agent_name="support-triage-agent",
        result_json="{}",
        started_at=NOW,
        completed_at=NOW,
    )

    with pytest.raises(sqlite3.IntegrityError):
        upsert_agent_result(database_path, result)
    assert list_agent_results(database_path, "missing-ticket") == []