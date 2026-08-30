# Audit observability

The web, agent action, Sentinel worker, and MCP roles emit one-line JSON audit events through
Python logging. Cloud Run captures stdout/stderr automatically, while the JSON body remains
portable to local tests and other log collectors.

## Correlation

- Web-to-ADK turns receive a fresh opaque `execution_id`. The web logs it and sends it to ADK
  only as trusted `custom_metadata`; `session_id` and `turn_number` connect successive turns.
- A successful `request_field_analysis` uses the returned `analysis_id` as its `execution_id`.
  The Sentinel worker uses that same value through every stage, selected-scene, completion, or
  failure event.
- MCP calls receive a fresh opaque `execution_id`. MCP arguments are deliberately excluded;
  tool name and public Sentinel scene IDs are the only operation details retained.

The main event names are:

| Component | Events |
| --- | --- |
| Web gateway | `agent_gateway.turn.started`, `.completed`, `.failed` |
| ADK action | `agent_action.request_field_analysis` |
| Sentinel worker | `sentinel_pipeline.started`, `.stage`, `.scenes_selected`, `.completed`, `.failed`, `.skipped` |
| MCP | `mcp.tool.started`, `.completed`, `.failed` |

Example worker event:

```json
{"analysis_id":"6a358ec8-92af-4c94-94ef-7c4676ec597e","component":"worker","event":"sentinel_pipeline.stage","execution_id":"6a358ec8-92af-4c94-94ef-7c4676ec597e","percent":45,"schema_version":"1.0","severity":"INFO","stage":"computing_indices","status":"running","timestamp":"2026-08-30T01:23:45Z"}
```

## Data boundary

`agriculture.observability.audit_event` is fail-closed. It accepts only an explicit list of
machine-readable identifiers, status values, public scene IDs, tool/agent names, booleans, and
counters. Unknown fields, nested objects, free-form strings, and values containing whitespace
or control characters are discarded before serialization.

The application must never log:

- user questions, model answers, prompts, or provider response bodies;
- polygons, coordinates, bounding boxes, or other geometry;
- authorization headers, identity tokens, API keys, passwords, or secrets;
- exception messages or tracebacks that may contain any of the above.

Failures therefore expose only a controlled `error_code` and the exception class in
`error_type`. Tests use `caplog` with secret-like prompts, exception messages, and geometry to
enforce this boundary.

## Cloud Logging queries

Cloud Run may store the emitted line under `textPayload` or parse it as `jsonPayload`, depending
on the runtime collector. Search by the exact event name or execution identifier in either
representation. Useful investigations are:

- one conversational turn: `execution_id=<turn execution ID>`;
- one analysis from ADK request through completion: `analysis_id=<analysis UUID>`;
- failed processing: `event=sentinel_pipeline.failed`;
- invoked MCP tools: `event=mcp.tool.completed`.

Retention, sinks, alerting, and access to logs remain project-level Google Cloud policy. Logs
are operational evidence, not a database for agricultural results or conversation history.
