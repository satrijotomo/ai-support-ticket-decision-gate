AI Support Ticket Decision Gate
This repository contains a Python proof of concept using:

Python 3.11
Azure Functions v4
Azure Durable Functions using the Python v2 decorator model
Microsoft Foundry Prompt Agents
SQLite
Pydantic v2
Vanilla HTML, CSS, and JavaScript
pytest
Azurite for local storage
The complete application specification is in: docs/implementation-spec.md

Engineering rules
Read docs/implementation-spec.md before planning or implementing features.
Implement only the phase explicitly requested by the user.
Do not create placeholder code, TODO implementations, or pseudocode.
Keep orchestrator functions deterministic.
Never perform database, filesystem, Foundry, current-time, random, or network operations inside an orchestrator.
Perform all I/O in activity functions or HTTP-trigger functions.
Use Pydantic for API and agent-output validation.
Use parameterized SQLite queries.
Make database writes and final business actions idempotent.
Never hardcode credentials or Azure resource identifiers.
Add or update tests for each implementation phase.
Run tests after changes.
Do not proceed to the next phase until the current phase passes validation.
Summarize changed files, tests executed, assumptions, and remaining risks.


