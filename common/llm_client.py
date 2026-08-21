"""The single shared Azure AI Foundry chat client.

ONE model deployment backs all four agents. Every agent module does:

    from common.llm_client import get_shared_chat_client

and passes the result as `client=` when constructing its Agent. The result is
cached, so all four agents share ONE client against ONE Foundry model
deployment. No agent file constructs a chat client of its own -- that is the
whole point of this module, and it is what the design brief means by "single
LLM endpoint".

Auth uses DefaultAzureCredential, so `az login` works locally and a managed
identity works unchanged once these agents are hosted in Foundry.

Required environment variables (export them in your shell; a .env file is
optional and only for convenience):

    AZURE_AI_PROJECT_ENDPOINT       e.g. https://<resource>.services.ai.azure.com/api/projects/<project>
    AZURE_AI_MODEL_DEPLOYMENT_NAME  e.g. gpt-4o

SDK NOTE: the Foundry integration package is `agent-framework-foundry` and the
client class is `FoundryChatClient` (earlier previews shipped this as
`agent-framework-azure-ai` / `AzureAIAgentClient`). Its own native env vars are
FOUNDRY_PROJECT_ENDPOINT / FOUNDRY_MODEL; we read the AZURE_AI_* names the
brief specifies and pass them explicitly, accepting the FOUNDRY_* names as a
fallback so either convention works.
"""

from __future__ import annotations

import os
from functools import lru_cache

from agent_framework_foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

# Optional convenience only -- the demo must run from exported shell vars alone.
load_dotenv(override=False)

# Ceiling on tool calls within a single agent run -- see get_shared_chat_client.
MAX_FUNCTION_CALLS_PER_RUN = 30

ENDPOINT_VARS = ("AZURE_AI_PROJECT_ENDPOINT", "FOUNDRY_PROJECT_ENDPOINT")
MODEL_VARS = ("AZURE_AI_MODEL_DEPLOYMENT_NAME", "FOUNDRY_MODEL")


class MissingConfigurationError(RuntimeError):
    """Raised when the Foundry endpoint/deployment env vars are not set."""


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _require(names: tuple[str, ...], purpose: str, example: str) -> str:
    value = _first_env(names)
    if value:
        return value
    raise MissingConfigurationError(
        f"Missing {purpose}. Export {names[0]} before running, for example:\n"
        f"    export {names[0]}='{example}'\n"
        f"(The Foundry SDK's own name, {names[1]}, is also accepted.)"
    )


@lru_cache(maxsize=1)
def get_shared_chat_client() -> FoundryChatClient:
    """Build the one chat client, once, and hand the same instance to everyone.

    Cached, so importing this from four agent modules still yields a single
    client against a single Foundry model deployment.
    """
    endpoint = _require(
        ENDPOINT_VARS,
        "Azure AI Foundry project endpoint",
        "https://<resource>.services.ai.azure.com/api/projects/<project>",
    )
    model = _require(MODEL_VARS, "Azure AI Foundry model deployment name", "gpt-4o")

    return FoundryChatClient(
        project_endpoint=endpoint,
        model=model,
        credential=DefaultAzureCredential(),
        # Backstop against a runaway tool loop. The framework's own default is
        # 40 model round-trips with UNLIMITED calls per round-trip, so a model
        # that gets stuck re-issuing one tool can rack up hundreds of identical
        # calls before anything stops it. The full demo flow needs well under 30
        # calls in a single run, so this bounds the damage without constraining
        # anything legitimate. Raise it if a longer flow is added.
        function_invocation_configuration={"max_function_calls": MAX_FUNCTION_CALLS_PER_RUN},
    )


def describe_configuration() -> dict[str, str | bool | None]:
    """Report what the client is configured with (no secrets) -- used by run_devui."""
    endpoint = _first_env(ENDPOINT_VARS)
    model = _first_env(MODEL_VARS)
    return {
        "endpoint": endpoint,
        "modelDeployment": model,
        "configured": bool(endpoint and model),
        "credential": "DefaultAzureCredential",
    }
