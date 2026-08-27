# CRM Prompt Normaliser — CRM-Data agent

Turns a free-form user query into a structured, validated plan plus the exact
canonical text each backend tool was **measured** to parse correctly.

Covers the six CRM-Data tools: **lead, opportunity, event, task, case (service
request), targets-vs-actuals**. Funnel tools belong to the CRM-Funnel agent and
are out of scope here.

---

## Why the canonical form is measured, not designed

The obvious approach — pick a tidy canonical phrasing and emit it everywhere —
breaks these backends. Worked example, from the client's own normalisation
proposal:

| Form | Result in `product_funnel.py` |
|---|---|
| `April, May and June 2026` | parses correctly |
| `april, may, june 2026` | **falls through to the current FY** |

The multi-month branch is gated on the literal string `" and "`
([product_funnel.py:1255](../../code/old/product_funnel.py#L1255)). The tidier
form is the broken one, and it fails silently.

So every rule in `src/render.py` is backed by an executed probe recorded in
[grammar/DATE_GRAMMAR.md](grammar/DATE_GRAMMAR.md), and locked by
`tests/test_grammar_contract.py`. **Do not "clean up" the emitted strings.**

---

## Layout

```
grammar/
  harness.py                 imports the real services with watsonx/Presto stubbed
  probe.py                   sweeps phrasing variants -> date_grammar.json
  DATE_GRAMMAR.md            findings: what each parser accepts, and what it breaks on
src/
  vocab_build.py             scans the 5 CSVs -> vocabulary.json
  vocabulary.json            generated; canonical entity values + aliases
  dates.py                   ONE date resolver, replacing twelve
  intents.py                 metric registry + deterministic tool routing
  render.py                  canonical surface forms, per tool
  normaliser.py              entry point
tests/
  run_corpus.py              runs new.md + crmprompts.md, reports coverage
  test_grammar_contract.py   asserts emitted forms still parse in the real tools
```

## Use

```bash
python src/vocab_build.py                       # once, ~22s over 1.35M rows
python src/normaliser.py "Total leads for wave city between 1 April 2026 and 30 June 2026"
python tests/run_corpus.py --csv out.csv        # coverage over 374 real prompts
python -m pytest tests/ -q                      # 29 contract tests
```

```python
from normaliser import normalise
r = normalise("total cases for eden in wave city for April, May and June 2026")
for c in r.calls:
    c.tool, c.canonical_text, c.start_date, c.end_date, c.filters
```

## Output

`normalise()` returns a `NormalisedQuery`:

| Field | Meaning |
|---|---|
| `ok` | whether it produced an executable plan |
| `calls[]` | one `ToolCall` per backend invocation |
| `clarification` | why it stopped, when `ok` is false |
| `warnings[]` | things the agent must state or verify |
| `decomposed` | whether the query was split |
| `agents[]` | which agents the calls belong to |

Each `ToolCall` carries `tool`, `canonical_text`, resolved `start_date` /
`end_date`, `comparison`, `groupings` and canonical `filters`.

## Behaviour

**Deterministic.** Lookup tables and one date resolver do the rewriting. No LLM
rewrites free text — a rewriter that turns "not interested" into "interested",
or drops the second project, yields a confident wrong number with no error.

**Decomposition is structural.** Multiple entities or periods split into
separate calls automatically. Users never type `separately`.

**Defaults are declared, never silent.** When no date is given the current FY is
applied per `behavior.md:433`, with a warning that the response must say so.

**It refuses.** Unsupported concepts (turnaround time, averages, forecasting)
and conversational fragments ("it need to be from lead report") return a
clarification instead of a fabricated query.

## Coverage

374 real prompts from `new.md` (UAT) and `crmprompts.md`; 286 in scope for
CRM-Data after excluding funnel queries.

| | count |
|---|---|
| normalised | 277 / 286 — **96.9%** |
| correctly refused | 9 |

The 9 refusals are genuine: unsupported metrics, funnel-ratio requests, and
context-dependent follow-ups. Coverage is deliberately *not* measured as "did it
produce output" — an earlier revision scored 98.3% by turning a turnaround-time
question into a lead count, which is worse than refusing.

## Known backend defects this cannot fix

Input normalisation cannot reach these. Detail in
[grammar/DATE_GRAMMAR.md](grammar/DATE_GRAMMAR.md).

| | Impact |
|---|---|
| `event_report.py` raises `NameError: is_qoq` on every dated query | **HTTP 500 on 8 of 12 real event prompts.** One missing line — `lead_report.py:2844` has it |
| `case_report.py` discards the year on any 2+ month range | Returns current-FY data for a historical question; whole-FY ranges invert and return **zero rows** |
| `targetvsactuals.py` cannot parse ranges | 20 of 21 forms resolve incorrectly |
| YoY start year is hardcoded and differs per service | FY2020 in lead/task, FY2018 in opportunity |
| Comparison qualifiers discard the requested window | `yoy ... 1 Apr–30 Jun` returns nine fiscal years |

## Open decision

`Q2 2026` resolves to **Jul–Sep** (fiscal) in all services, matching
`behavior.md:429`. UAT prompt #131 reads `Q2 2026 (April to June)` — the client
means **calendar** Q2. Every `Q<n>` query is currently one quarter off from user
expectation. This needs a product decision before `Q<n>` can be normalised
correctly; the resolver follows the fiscal definition today.
