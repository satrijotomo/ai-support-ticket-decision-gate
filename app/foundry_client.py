from contextlib import ExitStack

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


class FoundryClientError(RuntimeError):
    """A sanitized failure raised by the Foundry transport boundary."""


def invoke_prompt_agent(
    project_endpoint: str,
    agent_name: str,
    prompt: str,
) -> str:
    try:
        with ExitStack() as stack:
            credential = stack.enter_context(DefaultAzureCredential())
            project_client = stack.enter_context(
                AIProjectClient(endpoint=project_endpoint, credential=credential)
            )
            openai_client = stack.enter_context(
                project_client.get_openai_client(agent_name=agent_name)
            )
            response = openai_client.responses.create(input=prompt)
            output_text = response.output_text
    except FoundryClientError:
        raise
    except Exception:
        raise FoundryClientError("Foundry agent invocation failed.") from None

    if not isinstance(output_text, str) or not output_text.strip():
        raise FoundryClientError("Foundry agent returned no output.")
    return output_text
