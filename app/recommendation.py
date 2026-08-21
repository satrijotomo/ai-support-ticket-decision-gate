from collections.abc import Iterable
from typing import Any

from app.models import (
    KnowledgeAgentResult,
    Recommendation,
    RiskAgentResult,
    TriageAgentResult,
)


TRIAGE_AGENT = "support-triage-agent"
KNOWLEDGE_AGENT = "support-knowledge-agent"
RISK_AGENT = "support-risk-reviewer-agent"
REQUIRED_AGENTS = {TRIAGE_AGENT, KNOWLEDGE_AGENT, RISK_AGENT}


def build_recommendation(agent_results: Iterable[dict[str, Any]]) -> Recommendation:
    results_by_agent: dict[str, dict[str, Any]] = {}
    for envelope in agent_results:
        agent_name = envelope.get("agent_name")
        result = envelope.get("result")
        if not isinstance(agent_name, str) or not isinstance(result, dict):
            raise ValueError("agent result envelopes require agent_name and result")
        if agent_name in results_by_agent:
            raise ValueError(f"duplicate agent result: {agent_name}")
        results_by_agent[agent_name] = result

    missing = REQUIRED_AGENTS - results_by_agent.keys()
    unexpected = results_by_agent.keys() - REQUIRED_AGENTS
    if missing or unexpected:
        raise ValueError(
            f"agent result set mismatch; missing={sorted(missing)}, "
            f"unexpected={sorted(unexpected)}"
        )

    triage = TriageAgentResult.model_validate(results_by_agent[TRIAGE_AGENT])
    knowledge = KnowledgeAgentResult.model_validate(results_by_agent[KNOWLEDGE_AGENT])
    risk = RiskAgentResult.model_validate(results_by_agent[RISK_AGENT])

    return Recommendation(
        category=triage.category,
        priority=triage.priority,
        recommended_team=triage.recommended_team,
        draft_response=knowledge.draft_response,
        suggested_steps=knowledge.suggested_steps,
        approval_required=risk.approval_required,
        concerns=risk.concerns,
        risk_level=risk.risk_level,
    )