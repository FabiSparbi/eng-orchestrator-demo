"""Launch DevUI with all four agents registered.

    python run_devui.py

Each agent is registered as its own entity, so you can exercise the three
specialists standalone *and* drive the whole thing through the orchestrator --
DevUI shows the orchestrator's calls into the specialists in its trace view.

The Geometry Agent's apply_modification_workflow tool is registered with
approval_mode="always_require", so DevUI renders an Approve/Reject prompt
before any geometry change is applied. That prompt is the human-in-the-loop
gate; no extra UI code is involved.
"""

from __future__ import annotations

import argparse
import logging
import sys

from common.llm_client import MissingConfigurationError, describe_configuration

DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"


def _preflight() -> None:
    """Fail fast with a readable message if the Foundry env vars are missing."""
    config = describe_configuration()
    if config["configured"]:
        print(f"  Foundry endpoint : {config['endpoint']}")
        print(f"  Model deployment : {config['modelDeployment']}")
        print(f"  Credential       : {config['credential']}")
        return

    print("ERROR: Azure AI Foundry configuration is missing.\n", file=sys.stderr)
    print("Export both of these before running:\n", file=sys.stderr)
    print("    export AZURE_AI_PROJECT_ENDPOINT='https://<resource>.services.ai.azure.com/api/projects/<project>'", file=sys.stderr)
    print("    export AZURE_AI_MODEL_DEPLOYMENT_NAME='<your-deployment>'\n", file=sys.stderr)
    print("Then authenticate with:  az login", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Vehicle Design Copilot demo in DevUI.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to serve on (default {DEFAULT_PORT}).")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Host to bind (default {DEFAULT_HOST}).")
    parser.add_argument("--auth", action="store_true", help="Require a bearer token (auto-generated and logged).")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window automatically.")
    parser.add_argument("--tracing", action="store_true", help="Enable OpenTelemetry instrumentation in DevUI.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("Vehicle Design Copilot -- multi-agent demo")
    print("=" * 60)
    _preflight()

    # Imported after the preflight check so a missing env var produces the
    # message above rather than a stack trace from deep inside the SDK.
    from agent_framework_devui import serve  # noqa: PLC0415

    from agents.geometry.agent import agent as geometry_agent  # noqa: PLC0415
    from agents.orchestrator.agent import agent as orchestrator_agent  # noqa: PLC0415
    from agents.part_search.agent import agent as part_search_agent  # noqa: PLC0415
    from agents.simulation.agent import agent as simulation_agent  # noqa: PLC0415

    entities = [
        orchestrator_agent,   # start here for the full walk-through
        part_search_agent,
        simulation_agent,
        geometry_agent,
    ]

    print("=" * 60)
    print("Registered agents:")
    for entity in entities:
        print(f"  - {entity.name}")
    print()
    print("All four share ONE FoundryChatClient against ONE model deployment.")
    print("Geometry changes pause for Approve/Reject in the DevUI panel.")
    print(f"\nOpening DevUI at http://{args.host}:{args.port}\n")

    serve(
        entities=entities,
        host=args.host,
        port=args.port,
        auto_open=not args.no_browser,
        # Loopback-only local demo: DevUI explicitly supports no-auth here.
        # --auth turns on bearer-token auth (token is generated and logged).
        auth_enabled=args.auth,
        instrumentation_enabled=args.tracing,
        ui_enabled=True,
    )


if __name__ == "__main__":
    main()
