# Demo preflight

Run the read-only preflight before recording or presenting the demonstration:

```bash
python manage.py demo_preflight
```

It validates the Django production safeguards, registered system checks, selected repository,
artifact, task, boundary and Sentinel adapters, the server-side agent gateway, and the Gemini
3.5+/ADK configuration. A missing agent gateway is a warning locally and a failure in production.
Missing remote probe URLs are warnings so the command remains useful during local development. A
failed required check exits nonzero.

Pass service origins to include live probes:

```bash
python manage.py demo_preflight \
  --web-url=https://WEB.run.app \
  --agent-url=https://AGENT.run.app \
  --mcp-url=https://MCP.run.app
```

The web probe calls `/live` and `/ready`; the agent probe verifies `/list-apps`; the MCP probe
performs a read-only `initialize` handshake. For `run.app` endpoints, the command obtains a
short-lived Google ID token through Application Default Credentials. Explicit audiences can be
set with `--agent-audience` and `--mcp-audience`. Local HTTP services do not require a token.

The same inputs can be provided as `PREFLIGHT_WEB_URL`, `PREFLIGHT_AGENT_URL`, and
`PREFLIGHT_MCP_URL`. Existing `AGENT_API_URL`, `AGENT_MCP_URL`, and `MCP_URL` variables are also
recognized. Use `--json` for automation:

```bash
python manage.py demo_preflight --json
```

JSON output reports `ok`, the environment, pass/warning/failure counts, and every check. The
command never prints configured passwords, hashes, secrets, bearer tokens, or private keys.
