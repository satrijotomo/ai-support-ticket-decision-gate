PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Tickets (
    ticket_id TEXT PRIMARY KEY,
    workflow_instance_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    affected_service TEXT,
    customer_impact TEXT,
    submitted_by TEXT,
    status TEXT NOT NULL,
    recommendation_json TEXT,
    assigned_team TEXT,
    action_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS AgentResults (
    result_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES Tickets(ticket_id),
    agent_name TEXT NOT NULL,
    result_json TEXT NOT NULL,
    confidence REAL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    UNIQUE(ticket_id, agent_name)
);

CREATE TABLE IF NOT EXISTS Approvals (
    approval_id TEXT PRIMARY KEY,
    decision_id TEXT UNIQUE NOT NULL,
    ticket_id TEXT NOT NULL REFERENCES Tickets(ticket_id),
    approver TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approve', 'reject')),
    comments TEXT,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS AuditEvents (
    event_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES Tickets(ticket_id),
    event_type TEXT NOT NULL,
    event_data TEXT,
    dedupe_key TEXT UNIQUE NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS IX_Tickets_Status ON Tickets(status);
CREATE INDEX IF NOT EXISTS IX_AgentResults_TicketId ON AgentResults(ticket_id);
CREATE INDEX IF NOT EXISTS IX_Approvals_TicketId ON Approvals(ticket_id);
CREATE INDEX IF NOT EXISTS IX_AuditEvents_TicketId_OccurredAt
    ON AuditEvents(ticket_id, occurred_at);