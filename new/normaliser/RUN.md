# Running the Normaliser & Wiring It Into Orchestrate

## 1. Install

```bash
pip install fastapi uvicorn pydantic python-dateutil
```

`pandas`, `prestodb` and `ibm-watsonx-ai` are **not** needed to run the
normaliser — it touches no database and calls no LLM. They are only required by
`grammar/probe.py`, which imports the real services for testing.

## 2. Build the vocabulary (once)

```bash
cd d:\CRM\new\normaliser
python src/vocab_build.py
```

~22 seconds over 1.35M rows. Writes `src/vocabulary.json` — canonical projects,
products, sources, owners and statuses read from the real CSVs.

Re-run whenever the source data changes materially (new products, new sources).

## 3. Start the service

```bash
uvicorn api:app --host 0.0.0.0 --port 8100 --app-dir src
```

`--host 0.0.0.0` matters — `127.0.0.1` will not be reachable through a tunnel.

Verify:

```bash
curl http://localhost:8100/health
# {"status":"healthy","vocabulary_loaded":true,...}
```

If `vocabulary_loaded` is `false`, step 2 did not run.

## 4. Expose it

Orchestrate needs a public HTTPS URL. Any tunnel works:

```bash
ngrok http 8100
# or
cloudflared tunnel --url http://localhost:8100
```

Confirm the public URL works before importing:

```bash
curl https://<your-public-url>/health
```

## 5. Import into Orchestrate

The OpenAPI spec is served at:

```
https://<your-public-url>/openapi.json
```

In Orchestrate: **Skills / Tools → Add tool → Import from OpenAPI**, and give it
that URL.

It registers one operation:

| Operation ID | Method | Path |
|---|---|---|
| `normalise_crm_query` | POST | `/normalise` |

**Add this tool to the master agent (`The Wave Group - CRM`) only.** The
CRM-Data and CRM-Funnel agents must not have it — they receive an already
normalised plan.

> If Orchestrate rejects the spec, it is usually the OpenAPI version. Fetch
> `/openapi.json`, save it locally, set `"openapi": "3.0.3"` at the top, and
> upload the file instead. FastAPI emits 3.1 by default, which some importers
> reject.

## 6. Wire the agents

```
The Wave Group - CRM   (master, user-facing)
├── tool:  normalise_crm_query
├── agent: CRM-Data      ← lead, opportunity, event, task, case, targetsVSactuals
└── agent: CRM-Funnel    ← the 7 funnel tools
```

Behaviour instructions:

| Agent | File |
|---|---|
| The Wave Group - CRM | `new/behavior/master_agent_behavior.md` |
| CRM-Data | `new/behavior/crm_data_agent_behavior.md` |

The master calls the normaliser first, delegates each returned call to the named
agent, then presents. Collaborators execute and return data only.

---

## The contract

Request:

```json
{ "query": "Total leads for wave city between 1 April 2026 and 30 June 2026" }
```

Response:

```json
{
  "ok": true,
  "call_count": 1,
  "agents": ["CRM-Data"],
  "decomposed": false,
  "calls": [{
    "tool": "lead_report",
    "agent": "CRM-Data",
    "metric": "total_leads",
    "canonical_text": "total leads for Wave City 1 April 2026 to 30 June 2026",
    "start_date": "2026-04-01",
    "end_date": "2026-06-30",
    "comparison": "none",
    "groupings": [],
    "filters": {"project": ["Wave City"]}
  }],
  "warnings": [],
  "must_state": []
}
```

Field meanings:

| Field | Use |
|---|---|
| `ok` | `false` → call nothing, show `clarification` |
| `calls[].tool` | which backend to invoke |
| `calls[].canonical_text` | send as the tool's `question`, **byte for byte** |
| `calls[].start_date` / `end_date` | resolved dates; pass as parameters where supported |
| `calls[].filters` | canonical entity values; rows outside these must not be shown |
| `must_state` | the response is required to tell the user these |
| `warnings` | caveats worth surfacing |

**`canonical_text` must not be edited.** Its wording is chosen to match what each
parser accepts — `April, May and June 2026` works, `April, May, June 2026`
silently resolves to the wrong period.

---

## Testing before you wire it up

```bash
# coverage over 374 real prompts
python tests/run_corpus.py --csv out.csv

# 29 contract tests: every emitted form still parses in the real backends
python -m pytest tests/ -q

# one-off, from the CLI
python src/normaliser.py "total sales for eden last fy" --today 2026-08-26
```

`tests/` needs the full dependency set, since it imports the real services.

---

## What to expect in Orchestrate testing

Three backend defects will show up as failures that are **not** the normaliser's
fault. Detail in `grammar/DATE_GRAMMAR.md`.

**Event Report returns HTTP 500 on most dated queries.**
`event_report.py` uses `is_qoq` at line 1829 without assigning it —
`lead_report.py:2844` has the missing line. 8 of 12 real event prompts fail.
Expect event queries to fail until that line is added.

**Case Report answers the wrong year.** It discards the year when a query names
2+ months and substitutes the current FY. The normaliser works around this by
sending one month per call — so if you compare against the raw tool, the
normalised path will look *different and correct*.

**targetsVSactuals cannot parse date ranges** — 20 of 21 forms resolve wrongly.
Treat any dated targets result as unverified until its parser is fixed.

## Two open decisions

**`Q2` is off by one quarter.** All services return Jul–Sep (fiscal), matching
`behavior.md:429`. UAT prompt #131 reads `Q2 2026 (April to June)` — calendar
Q2. Every `Q<n>` query answers a different quarter than the user expects. The
resolver follows the fiscal definition; confirm which is authoritative.

**Comparison plus explicit range.** `"tasks year on year between 1 April and
30 June 2026"` — backends discard the window and return FY2020→FY2026 (FY2018
for opportunity). ~20 UAT prompts hit this. The normaliser flags the conflict;
decide whether the window or the qualifier wins.
