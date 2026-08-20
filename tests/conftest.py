from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import initialize_database
from app.models import TicketRecord, TicketStatus


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "support_gate.db"
    initialize_database(path)
    return path


@pytest.fixture
def ticket() -> TicketRecord:
    timestamp = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    return TicketRecord(
        ticket_id="ticket-001",
        workflow_instance_id="workflow-001",
        title="Users cannot access payroll portal",
        description="Multiple users receive 403 responses.",
        affected_service="Payroll Portal",
        customer_impact="40 users blocked",
        submitted_by="demo.user@contoso.com",
        status=TicketStatus.SUBMITTED,
        created_at=timestamp,
        updated_at=timestamp,
    )