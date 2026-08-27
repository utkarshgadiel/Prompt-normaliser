# The Wave Group CRM Agent — Full Context

Everything built, everything found, and why. Written as a handover: someone who
has never seen this work should be able to read it and continue.

Last updated 26 August 2026.

---

## 1. THE STARTING POINT

The Wave Group runs a CRM analytics agent on IBM watsonx Orchestrate. It sits on
Salesforce-derived data in watsonx.data, queried through Presto.

It ran at roughly 70 to 80 percent accuracy. The same business intent succeeded
or failed depending on phrasing, punctuation and word order. Users had learned
workarounds, most notably typing the word "separately" to force the agent to
break a query into parts.

The original assumption was that this was a prompt problem, and that a better
`behavior.md` would fix it.

**It was not a prompt problem.** That conclusion is the foundation of everything
that follows, and it was reached by measurement, not opinion.

### What existed before

- `behavior/old/behavior.md` — 2,501 lines of agent instructions
- `code/old/` — 13 FastAPI services, roughly 35,000 lines
- `logics.md` — business metric definitions
- `crmprompts.md` — 130 real production prompts
- `new.md` — 263 client UAT prompts
- `data/` — five CSV exports, 1.35M rows, 215MB

---

## 2. WHAT THE INVESTIGATION FOUND

Every finding below was produced by running code against the real services and
the real data, not by reading and inferring.

### 2.1 The knowledge base contradicted the data

`behavior.md:1966` declared five projects. `Project__c` contains three:
`Wave City`, `Wave Estate`, `WMCC Sec 32`.

`Wave Amore` and `Wave Executive Floors` are **products**, not projects. Worse,
`product_funnel.py:265` hard-codes `exclude_projects = ["wave executive floors",
"wave amore"]`. The prompt routed those queries to a tool engineered to discard
them. **No prompt quality could ever have made those queries work.**

Product names were misspelled against reality throughout:

| Knowledge base said | Data actually holds |
|---|---|
| `VASILLA` | `VASILIA` |
| `WAVEFLOOR 85` | `WAVE FLOOR 85` |
| `VERIDIA-3` … `VERIDIA-7` | do not exist at all |

And entities present in the data were missing from the KB entirely, including
`TRUCIA` (which appears in the client's own prompt list), `SMS Campaign`
(14,106 leads, the second-largest source) and `Events / Exhibitions`.

### 2.2 The agent was doing the database's job

`behavior.md` Section 15 instructed the agent to *exact-match* returned rows
against names in the user's query. But `project_funnel.py:274` emits
`display_name = val.title()`, turning `WMCC Sec 32` into `Wmcc Sec 32`.

The user types "WMCC". The exact match fails. The agent discards the only
correct row and returns an empty table. Mechanical, reproducible, invisible.

### 2.3 Twelve independently written date parsers

Each service carried its own, between 500 and 1,300 lines. "Last quarter" was
implemented twelve times, differently. Verified by probing all of them with one
shared corpus.

### 2.4 A live production bug taking down the Event tool

`event_report.py:1780` defines `detect_date_intent`, which uses `is_qoq` at
lines 1829 and 2002 but **never assigns it**. The name is only bound inside two
unrelated module-level functions, a different scope. `lead_report.py:2844`
contains exactly the missing line.

The `NameError` propagates uncaught to the endpoint handler at line 2956 and
becomes **HTTP 500**.

Measured on real UAT prompts: **8 of 12 event queries fail**. The only survivors
are those with no date reference at all.

### 2.5 Case Report silently returns the wrong year

The most dangerous defect found, because it returns plausible numbers rather
than failing.

`case_report.extract_specific_months_from_query` fires whenever **two or more
month names** appear, keeps only the month *numbers*, and substitutes the
**current** financial year. Its own log admits it:

```
extract_specific_months_from_query: Found 2 unique months: [4, 6]
Specific months [4, 6] detected for FY 2026-2027: 2026-04-01 to 2026-06-30
```

| Sent to case_report | Returns | Correct? |
|---|---|---|
| `15 April 2024 to 28 September 2025` | 2026-04-01 → 2026-09-30 | no, both years replaced |
| `1 April 2025 to 31 March 2026` | 2027-03-01 → 2026-04-30 | **inverted, matches zero rows** |
| `June 2024` | 2024-06-01 → 2024-06-30 | yes |
| `fy 2024` | 2024-04-01 → 2025-03-31 | yes |

An inverted range in SQL `BETWEEN` returns nothing, so historical case queries
came back empty.

**A methodology lesson worth keeping.** The first probe missed this entirely
because it tested against 2026, which is the current FY, so the substituted year
coincided with the expected one. Any date probe must use a **past** year.

### 2.6 Comparison qualifiers discard the requested window

With `yoy` or `qoq` present, the explicit period is thrown away. Worse, the
replacement differs per service: year-on-year starts at **FY2020** in
`lead_report` and `task_report` but **FY2018** in `opportunity_report`. The same
question against two tools returned different spans.

### 2.7 targetvsactuals cannot parse date ranges

**1 of 21** tested forms resolved correctly. Only a single whole month works.

### 2.8 Other defects catalogued

- Owner filter kept only if **both** LLM and regex found it, and the regex
  required a capitalised name, so "for owner ambuj" silently returned everyone
  (`lead_report.py:2671-2686`)
- `extract_project_filter` returns only the **first** match, so multi-project
  questions silently narrowed to one (`project_funnel.py:143`)
- Unescaped f-string interpolation of LLM-derived values into SQL, 68 sites
- `date_parse` without `TRY()` in funnel services, so one malformed row fails
  the whole query, while report services use `TRY()` and silently undercount
- `list(set(...))` on filter values, producing nondeterministic SQL between
  identical runs

---

## 3. DECISIONS THE CLIENT LOCKED

These were raised with evidence, decided, and are treated as **defined business
semantics**. They are not defects and are not to be relitigated.

**Sales done** = opportunities where `Sales_Order_Number__c` is non-blank,
counted on `Created_Date__c`, blanks excluded.

Measured impact, recorded so the number is understood: 31,144 rows qualify.
Of the 27,967 that also carry a `Sales_Order_Date__c`, **79.5 percent fall in a
different financial year** under this rule. The client was shown this and chose
to keep `Created_Date__c`.

**Funnel stages stay period-independent.** No `lead_id_c` cohort join. Each
stage is counted independently within the period, so a sale counted in a period
did not necessarily originate from a lead created in it. Ratios keep their
current meaning.

**No metric definition in `logics.md` changes.**

**Data coverage floors are system policy**, not data-derived: FY2020 for leads,
tasks, service requests and events; FY2018 for opportunities. Year on year means
that start year through the current one.

> The raw data actually reaches further back in several tables — leads to
> FY2019-20 with 17,447 rows, opportunities to FY2017-18 with 21,319 rows, and
> events only begin FY2021-22. The client chose the stated floors, which match
> the backends' own hardcoded behaviour. Recorded here for awareness only.

---

## 4. THE ARCHITECTURE

Four agents in Orchestrate, with a deterministic normaliser in front of the
data path.

```
User
 └── The Wave Group - CRM          master, user-facing, holds all context
      ├── normalise_crm_query      deterministic HTTP service (not an LLM)
      ├── CRM-Data                 6 tools: lead, opportunity, event,
      │                            task, case, targetsVSactuals
      ├── CRM-Funnel               7 tools: lead, project, product, source,
      │                            subsource, lead-user, sales-user funnels
      └── CRM-Other Tools          Query SOP + websearch:web_search
```

### Why a normaliser rather than a bigger prompt

The client's own instinct — normalise every query into a canonical form before
it reaches the tools — was correct and is the core of the design. But testing
their worked example exposed the constraint that shapes everything:

**The canonical form cannot be invented. It must be reverse-engineered from
what each parser actually accepts.**

Their proposal normalised `"April, May and June 2026"` to
`"april, may, june 2026"`. The only multi-month branch in `product_funnel.py`
is line 1255:

```python
if " and " in q and not re.search(r'\b(to|till|from|-)\b', q):
```

It is gated on the **literal string `" and "`**. The tidier form falls through
to the current financial year, silently. The same guard rejects `-`, and
`\b-\b` matches the hyphen inside `month-on-month`, so hyphenation alone changes
which branch fires.

The cleaner-reading form is the broken one. That is why every rule in
`render.py` is backed by an executed probe, and why
`tests/test_grammar_contract.py` exists.

---

## 5. WHAT WAS BUILT

All under `d:\CRM\new\`. Nothing in `behavior/old/` or `code/old/` was modified.

### 5.1 Grammar discovery

**`grammar/harness.py`** imports the real services with watsonx, Presto,
matplotlib and ibm_boto3 stubbed, so pure parsing functions can be exercised
offline with no credentials. It records whether a stubbed LLM or DB was reached,
so a result that secretly depended on an LLM call is visible rather than
silently treated as deterministic.

**`grammar/probe.py`** sweeps controlled phrasing variants, changing one
dimension at a time so every failure has a single identifiable cause.

**`grammar/DATE_GRAMMAR.md`** is the findings document: what each parser
accepts, what it rejects, and what it accepts *wrongly*. The silent-failure
class is called out separately because it is the dangerous one.

### 5.2 The verified canonical grammar

| Form emitted | lead | opp | task | case | targets |
|---|---|---|---|---|---|
| `between 1 April 2026 and 30 June 2026` | ok | ok | ok | **1-day collapse** | **1-day collapse** |
| **`1 April 2026 to 30 June 2026`** | **ok** | **ok** | **ok** | **ok** | wrong |
| `from 1 April 2026 to 30 June 2026` | ok | ok | ok | **ends today** | wrong |
| `01-04-2026 to 30-06-2026` | **full FY** | ok | **full FY** | ok | **full FY** |
| **`April, May and June 2026`** | **ok** | **ok** | **ok** | **ok** | April only |
| `April 2026, May 2026 and June 2026` | **drops April** | **drops April** | **drops April** | ok | **drops April** |
| **`fy 2025`** | **ok** | **ok** | **ok** | **ok** | — |

**Emit:** bare-`to` ranges, month lists with the year stated once at the end,
and `fy <year>` for whole financial years.

**Never emit:** `between X and Y`, `from X to Y`, `dd-mm-yyyy` numerics, or the
year repeated per month. That last form appears in roughly 15 UAT prompts and
was silently under-counting by one month everywhere.

### 5.3 The normaliser

`src/` — deterministic throughout. Lookup tables and one date resolver do the
rewriting. No LLM rewrites free text, because a rewriter that turns
"not interested" into "interested", or drops the second project, produces a
confident wrong number with no error.

| File | Role |
|---|---|
| `vocab_build.py` | scans the five CSVs, emits `vocabulary.json` |
| `dates.py` | one date resolver, replacing twelve |
| `intents.py` | metric registry, tool routing, funnel resolution, coverage floors |
| `render.py` | canonical surface forms, per tool |
| `normaliser.py` | orchestration, decomposition, validation |
| `api.py` | FastAPI wrapper for Orchestrate |

**Vocabulary** is generated from the data, never transcribed from the prompt.
It merges spelling variants under the most frequent form and classifies facets:
those with more than 500 distinct values are **free text** and are exact-match
only. This matters — `subject` has 77,905 distinct values and one of them is
`'We were unable to contact you_Wave City'`, occurring 12,291 times and
containing a project name. Loose alias matching there would misfire constantly.

**Decomposition is structural.** Multiple entities, periods, metrics or
comparisons split into separate calls automatically. Users never type
"separately" again.

**Defaults are declared, never silent.** No date given applies the current
financial year, and the response states which period it used.

**It refuses.** Unsupported concepts such as turnaround time, averages and
forecasting return a clarification rather than a fabricated answer, as do
conversational fragments like "it need to be from lead report".

### 5.4 Bugs found and fixed while building

Each was found by testing, not review.

| Bug | Symptom | Fix |
|---|---|---|
| `unqualified leads` matched `qualified` | wrong metric | negative lookbehind `(?<!un)` |
| `task cancelled` missed | fell to generic tasks | both word orders in patterns |
| `lead source` matched the leads metric | spurious second tool call | negative lookahead |
| `Q2 2026 (April to June)` dropped May | wrong period | year-optional month range |
| Only first comparison survived | YoY silently dropped | `detect_comparisons` returns a list |
| Calls built per tool | `opportunities and sales` lost one | build per **metric** |
| Multi-period collapsed | `last fy and current fy` lost one | `detect_multi_periods` |
| Entities applied to all metrics | cross-contamination | bind to nearest metric by span |
| Funnels routed to `lead_report` | never reached funnel tools | funnel metric + `resolve_funnel_tool` |
| `from 2015 till now` → only FY2015 | wrong period | bare-year till-date branch |
| Quadratic vocab merge | 7+ minute build | precompute keys, now 22s |

### 5.5 Guards built in

**Funnel volume.** A funnel row carries 8 stage counts and up to 10 ratios.
Broken down by product that is 66 rows, sub-source 81, user 127 — and multiplied
across 12 months it is unreadable. The normaliser estimates output size before
running and asks the user to narrow, offering concrete numbered choices.

| Request | Estimate | Behaviour |
|---|---|---|
| funnel for Wave City, Q1 2025 | 1 | runs |
| source wise funnel, last FY | 20 | runs |
| month on month lead funnel | 12 | runs |
| product wise funnel MoM | 66+ | asks |
| sub source wise funnel | 81 | asks |
| user wise funnel | ambiguous | asks sales or lead |

**Coverage floors.** A period entirely below a tool's first year never becomes
a call — it returns numbered options instead of an empty table. Periods that
straddle the floor are trimmed silently.

---

## 6. THE FOUR BEHAVIOR FILES

In `behavior/`, written as plain prose. GPT-OSS 120B follows continuous
instructions more reliably than heavily formatted ones, so decorative markdown
was deliberately removed. The only markup retained is what the agent should
*emit*.

### master_agent_behavior.md

Classifies every message before acting: conversation, display-only request,
data question, process or market question, comparison question, or out of scope.
Only a data question reaches the normaliser.

Holds all conversation context, because collaborators are stateless. Rewrites
follow-ups into standalone questions before normalising.

Key rules earned from observed failures:

- **Never edit `canonical_text`.** Section 3 lists the exact rewrites that break
  things and why.
- **Label tables from returned data, not the requested period.** A screenshot
  showed a heading reading FY2024-2027 above rows running 2020-21 to 2026-27.
  Correct the heading silently; add no caveat.
- **Never print diagnostics.** An earlier version dumped
  "No period given; year on year defaulted to..." at the top of answers.
- **Time series are chronological, oldest first.** The backend's
  `enforce_descending_order` sorts years by value, producing 2023-24, 2022-23,
  2021-22, 2024-25.
- **Every table gets insights**, including single-value tables, and every bullet
  must trace to a cell. No "on target", "in line with SOP" or invented causes.
- **Ambiguous grain always asks.** "Yearly leads" means either this year's total
  or a per-year series. Ask before normalising, because the normaliser will
  happily resolve it to a valid-looking default.
- **Numbered options, never bullets**, so a one-character reply works.

### crm_data_agent_behavior.md

Executes the six CRM-Data tools. Routes on the `tool` field as a lookup. Passes
`canonical_text` byte for byte. Validates period, filters, grouping and metric,
and returns an explicit status with `returned_period` describing what actually
came back. Documents the known backend defects so failures are reported rather
than worked around.

### crm_funnel_agent_behavior.md

Executes the seven funnel tools. Same fidelity discipline. Additionally handles
the `.title()` scope mangling, the first-match-only project extractor, and the
excluded names. Never calculates a ratio.

### crm_other_tools_agent_behavior.md

Query SOP for internal process, `websearch:web_search` for the outside world.

This file exists because of a real incident. Asked to compare performance to
competitors, the master produced a table containing "Key competitor A — 610
sales — Latest annual report" and cited "Wave-Research, 2026" — a tool it had
never called and which had already been removed. Every figure was invented.

The rules that followed:

- Every external figure must come from a search result received **this turn**
- Placeholder sources are banned by name: "Competitor A", "a leading developer",
  "a recent industry report"
- A tool is not a publisher; the source is the organisation, with year and URL
- **Only compare what is comparable.** 564 sales cannot be benchmarked against
  another firm's sales count without knowing its size and inventory. Only rates
  and ratios benchmark.

---

## 7. RESULTS

**374 real prompts** from `new.md` (263 client UAT) and `crmprompts.md` (111
production). 286 in scope for CRM-Data after excluding funnel queries.

| | count |
|---|---|
| normalised | **277 / 286 = 96.9%** |
| correctly refused | 9 |

**29 grammar contract tests pass.**

Coverage is deliberately not measured as "did it produce output". An earlier
revision scored 98.3 percent by turning a turnaround-time question into a lead
count. That is worse than refusing, so the guards were added and the score
correctly fell.

---

## 8. KNOWN OUTSTANDING ISSUES

Not fixable from the normaliser. Detail in `grammar/DATE_GRAMMAR.md`.

**Event Report returns HTTP 500** on most dated queries. One missing line;
`lead_report.py:2844` has it. This is the cheapest accuracy available and should
be fixed first. `test_grammar_contract.py` pins the bug so the fix is noticed.

**Case Report discards the year** on any 2+ month range. The normaliser works
around it by sending one month per call, so the normalised path is correct while
the raw tool is not.

**targetvsactuals cannot parse ranges** — treat any dated result as unverified.

**Year-on-year floors are hardcoded** and differ per service.

**Backend hygiene** — SQL injection surface, `date_parse` without `TRY()`,
`list(set(...))` nondeterminism, funnel services pulling raw rows into pandas
with no `LIMIT`.

---

## 9. OPEN DECISIONS

**Fiscal versus calendar quarters.** All services return `Q2` as Jul–Sep,
matching `behavior.md:429`. UAT prompt #131 reads `Q2 2026 (April to June)` —
the client means calendar Q2. Every `Q<n>` query is one quarter off from user
expectation. The resolver follows the fiscal definition; this needs a ruling.

**Comparison plus explicit range.** `tasks year on year between 1 April and
30 June 2026` — backends discard the window. Roughly 20 UAT prompts hit this.
The normaliser flags the conflict; someone must decide whether the window or
the qualifier wins.

**Data variants.** `WAVE FLOOR` / `WAVE FLOORS` / `WAVE FLOOORS`, `SCO` /
`SCO.`, `NEW PLOTS` / `NEW  PLOTS` are stored separately and appear as separate
rows. Merging changes reported numbers, so it is the client's call.

**Blank project on events.** 46 percent of event rows have no project, so
project-wise meeting counts under-report.

**Unknown projects.** `Sun City` (3,963 opportunities) and `Wave One` (1,054)
exist in the data but no layer of the system knows them.

---

## 10. RUNNING IT

```bash
pip install fastapi uvicorn pydantic python-dateutil

cd d:\CRM\new\normaliser
python src/vocab_build.py                                # once, ~22s
uvicorn api:app --host 0.0.0.0 --port 8100 --app-dir src
```

`--host 0.0.0.0` matters; `127.0.0.1` is not reachable through a tunnel. The
normaliser needs no database and no LLM.

Expose with `ngrok http 8100`, then import
`normaliser/openapi_orchestrate.yaml` into Orchestrate. Use the **file**, not
the live `/openapi.json`, which serves OpenAPI 3.1 and is often rejected; the
file is deliberately 3.0.3.

Give the tool to the **master only**. Collaborators receive an already
normalised plan.

### Testing

```bash
python tests/run_corpus.py --csv out.csv    # 374 real prompts
python -m pytest tests/ -q                  # 29 contract tests
python src/normaliser.py "total sales for eden last fy" --today 2026-08-26
```

`tests/` needs the full dependency set, since it imports the real services.

### If ngrok restarts

The URL changes and is hardcoded in `servers` in the spec. Edit that line and
re-import. A reserved domain avoids repeated re-imports.

---

## 11. PRINCIPLES WORTH KEEPING

**Measure, do not infer.** Every rule here came from executing code against real
services and real data. The findings that mattered most — the `is_qoq` crash,
the case-report year substitution, the `" and "` gate — were all invisible to
code review.

**Probe with a past year.** Testing date handling against the current financial
year hides any bug that substitutes the current financial year.

**Silent wrong answers are worse than errors.** Most of what was found returns
plausible numbers rather than failing. That is why validation, honest refusal
and truthful table headings run through every layer.

**A number without provenance is not a number.** This applies to backend results
and external benchmarks equally.

**The tidier form is often the broken one.** Do not clean up `canonical_text`.
`test_grammar_contract.py` exists to catch exactly that.
