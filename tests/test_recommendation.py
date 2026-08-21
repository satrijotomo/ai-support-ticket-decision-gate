import pytest

from app.recommendation import (
    KNOWLEDGE_AGENT,
    RISK_AGENT,
    TRIAGE_AGENT,
    build_recommendation,
)


def valid_results() -> list[dict]:
    return [
        {
            "agent_name": TRIAGE_AGENT,
            "result": {
                "category": "IdentityAccess",
                "priority": "P2",
                "recommended_team": "Identity",
                "summary": "Users receive authorization failures.",
                "reasoning": ["Multiple users receive 403 responses."],
                "confidence": 0.9,
            },
        },
        {
            "agent_name": KNOWLEDGE_AGENT,
            "result": {
                "likely_issue": "Authorization configuration changed.",
                "suggested_steps": ["Confirm the affected user scope."],
                "missing_information": ["Deployment change details"],
                "draft_response": "We are reviewing the authorization failure.",
                "confidence": 0.75,
            },
        },
        {
            "agent_name": RISK_AGENT,
            "result": {
                "risk_level": "medium",
                "approval_required": True,
                "concerns": ["A configuration change may affect access."],
                "required_escalation": "Identity",
                "review_notes": "Review before changing authorization settings.",
                "confidence": 0.85,
            },
        },
    ]


def test_build_recommendation_uses_each_agent_owned_field() -> None:
    recommendation = build_recommendation(reversed(valid_results()))

    assert recommendation.model_dump(mode="json") == {
        "category": "IdentityAccess",
        "priority": "P2",
        "recommended_team": "Identity",
        "draft_response": "We are reviewing the authorization failure.",
        "suggested_steps": ["Confirm the affected user scope."],
        "approval_required": True,
        "concerns": ["A configuration change may affect access."],
        "risk_level": "medium",
    }


def test_build_recommendation_rejects_missing_or_duplicate_agents() -> None:
    with pytest.raises(ValueError, match="missing"):
        build_recommendation(valid_results()[:-1])

    with pytest.raises(ValueError, match="duplicate"):
        build_recommendation([*valid_results(), valid_results()[0]])