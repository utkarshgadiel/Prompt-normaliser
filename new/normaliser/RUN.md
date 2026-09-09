# Run and upload this release

## Service

Deploy `src/` with `src/vocabulary.json` and the runtime dependencies in
`requirements.txt`. The normaliser needs no CRM credentials, database driver or
LLM client. Test dependencies are separate from runtime dependencies.

```text
python -m pip install -r requirements.txt
uvicorn api:app --host 0.0.0.0 --port 8100 --app-dir src
```

Alternatively, `python src/api.py` reads Code Engine's `PORT` environment
variable (8100 by default). Verify `/health` returns HTTP 200 and
`vocabulary_loaded: true`. Missing vocabulary returns HTTP 503 and query plans
are blocked; a running process alone is not readiness.

Keep the vocabulary refreshed from approved CRM exports using
`python src/vocab_build.py`. Unknown names are clarification requests, never
permission to run across every entity.

## Orchestrate import

Use `openapi_orchestrate.json` or `openapi_orchestrate.yaml`. Both are OpenAPI
3.0.3 exports of the API contract. Replace `servers.url` with your Code Engine
URL before import, as in your existing upload workflow. The repository's
current URL has been left unchanged.

| Operation | Path | Attach to |
|---|---|---|
| normalise_crm_query | POST /normalise | Master only |
| format_funnel_tables | POST /format_funnel | CRM-Funnel only |
| normaliser_health | GET /health | Readiness/operations |

CRM-Data retains the six report tools. CRM-Funnel retains the seven funnel
tools. Both collaborators retain the chart builder, Query SOP and web search.

Replace the complete behavior content for all three active agents together:

- `../behavior/master_agent_behavior.md`
- `../behavior/crm_data_agent_behavior.md`
- `../behavior/crm_funnel_agent_behavior.md`

Import the updated schemas with the normaliser release so `rank`,
`collaborator_message`, `defer_chart`, IDs, `blocked_reason`, `include_totals`
and `period_column` are visible to the agents. Existing labeled-line messages
remain accepted, but new plans use the complete serialized call.

For future API changes, run `python export_openapi.py` with PyYAML installed in
the development environment. It preserves each specification's deployment
server list and converts nullable schemas to OpenAPI 3.0 form; changing only
the version string of a 3.1 schema is insufficient.

Run the checks in PRODUCTION_CHECKS.md against the deployed configuration before
production sign-off. Local tests cannot certify Orchestrate attachment,
transport limits, model compliance, live data correctness or deployed parser
versions. Do not modify reference services to make the local checks pass.
