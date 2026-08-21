from types import SimpleNamespace

import pytest

import app.foundry_client as foundry_client


class ContextValue:
    def __init__(self, value, events: list[str], name: str) -> None:
        self.value = value
        self.events = events
        self.name = name

    def __enter__(self):
        self.events.append(f"enter:{self.name}")
        return self.value

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.events.append(f"exit:{self.name}")


def test_invoke_prompt_agent_uses_named_agent_and_returns_output(
    monkeypatch,
) -> None:
    events: list[str] = []
    calls: dict[str, object] = {}

    class Responses:
        def create(self, **kwargs):
            calls["response"] = kwargs
            return SimpleNamespace(output_text='{"confidence": 0.9}')

    openai_client = SimpleNamespace(responses=Responses())

    class Project:
        def get_openai_client(self, **kwargs):
            calls["agent"] = kwargs
            return ContextValue(openai_client, events, "openai")

    def credential_factory():
        return ContextValue(object(), events, "credential")

    def project_factory(**kwargs):
        calls["project"] = kwargs
        return ContextValue(Project(), events, "project")

    monkeypatch.setattr(foundry_client, "DefaultAzureCredential", credential_factory)
    monkeypatch.setattr(foundry_client, "AIProjectClient", project_factory)

    output = foundry_client.invoke_prompt_agent(
        "https://example.services.ai.azure.com/api/projects/demo",
        "configured-agent",
        '{"ticket":{"title":"Access failure"}}',
    )

    assert output == '{"confidence": 0.9}'
    assert calls["agent"] == {"agent_name": "configured-agent"}
    assert calls["response"] == {
        "input": '{"ticket":{"title":"Access failure"}}'
    }
    assert calls["project"]["endpoint"].startswith("https://example.")
    assert calls["project"]["credential"] is not None
    assert events == [
        "enter:credential",
        "enter:project",
        "enter:openai",
        "exit:openai",
        "exit:project",
        "exit:credential",
    ]


@pytest.mark.parametrize("output_text", ["", "   ", None])
def test_invoke_prompt_agent_rejects_empty_output(monkeypatch, output_text) -> None:
    class Responses:
        def create(self, **kwargs):
            return SimpleNamespace(output_text=output_text)

    openai_client = SimpleNamespace(responses=Responses())

    class Project:
        def get_openai_client(self, **kwargs):
            return ContextValue(openai_client, [], "openai")

    monkeypatch.setattr(
        foundry_client,
        "DefaultAzureCredential",
        lambda: ContextValue(object(), [], "credential"),
    )
    monkeypatch.setattr(
        foundry_client,
        "AIProjectClient",
        lambda **kwargs: ContextValue(Project(), [], "project"),
    )

    with pytest.raises(foundry_client.FoundryClientError) as raised:
        foundry_client.invoke_prompt_agent("https://example", "agent", "prompt")

    assert str(raised.value) == "Foundry agent returned no output."


def test_invoke_prompt_agent_sanitizes_sdk_errors(monkeypatch) -> None:
    secret = "do-not-expose-this-token"

    def fail_credential():
        raise RuntimeError(f"authentication failed with {secret}")

    monkeypatch.setattr(foundry_client, "DefaultAzureCredential", fail_credential)

    with pytest.raises(foundry_client.FoundryClientError) as raised:
        foundry_client.invoke_prompt_agent("https://example", "agent", "prompt")

    assert str(raised.value) == "Foundry agent invocation failed."
    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None
