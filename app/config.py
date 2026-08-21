import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError


class AppSettings(BaseModel):
    """Validated settings available only to HTTP and activity boundaries."""

    model_config = ConfigDict(frozen=True)

    foundry_project_endpoint: HttpUrl | None = None
    triage_agent_name: str = Field(default="support-triage-agent", min_length=1)
    knowledge_agent_name: str = Field(default="support-knowledge-agent", min_length=1)
    risk_agent_name: str = Field(default="support-risk-reviewer-agent", min_length=1)
    foundry_mock_mode: bool = True
    database_path: str = Field(default="./data/support_gate.db", min_length=1)
    approval_timeout_minutes: int = Field(default=4320, gt=0)
    durable_task_scheduler_connection_string: str | None = None

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "AppSettings":
        values = os.environ if environment is None else environment
        return cls.model_validate(
            {
                "foundry_project_endpoint": values.get("FOUNDRY_PROJECT_ENDPOINT") or None,
                "triage_agent_name": values.get(
                    "TRIAGE_AGENT_NAME", "support-triage-agent"
                ),
                "knowledge_agent_name": values.get(
                    "KNOWLEDGE_AGENT_NAME", "support-knowledge-agent"
                ),
                "risk_agent_name": values.get(
                    "RISK_AGENT_NAME", "support-risk-reviewer-agent"
                ),
                "foundry_mock_mode": values.get("FOUNDRY_MOCK_MODE", "true"),
                "database_path": values.get(
                    "DATABASE_PATH", "./data/support_gate.db"
                ),
                "approval_timeout_minutes": values.get(
                    "APPROVAL_TIMEOUT_MINUTES", "4320"
                ),
                "durable_task_scheduler_connection_string": values.get(
                    "DURABLE_TASK_SCHEDULER_CONNECTION_STRING"
                )
                or None,
            }
        )


__all__ = ["AppSettings", "ValidationError"]