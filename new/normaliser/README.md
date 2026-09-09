# CRM normaliser and funnel formatter

This service plans queries for all six report tools and seven funnel tools. It
resolves intent and dates, canonicalises known entity values, and emits the exact
text the reference parsers accept. It does not query CRM, run an LLM, or prove
that a deployed backend applied the requested predicates.

The three active behaviors are in `../behavior/`. The reference services in
`../../code/old/` are read-only inputs to the grammar tests.

## Execution contract

Call `POST /normalise` with `query`; omit `today` in production and leave
`decompose` true. Relative dates use Asia/Kolkata. Financial years run April to
March; numeric day-first dates use DD/MM/YYYY, and ISO YYYY-MM-DD is supported.
An absent period retains the existing current-FY default. Invalid dates,
unknown explicit scopes and unsupported predicates must not silently broaden
that default.

A successful response includes `plan_id`, `call_count` and `calls`. Each call
contains a distinct `call_id`, tool/agent, canonical text, resolved dates,
metric, grouping, canonical filters, optional ranking, `defer_chart` and a
complete serialized `collaborator_message`. The master copies that message.
The content IDs support tracing; they are not security signatures.

When `ok` is false, `calls` is empty. `clarification` explains the reason;
`blocked_reason` distinguishes a measured backend limitation. Do not execute a
replacement period or remove a filter to get a result. Each whole plan has a
128-call limit; it is never silently truncated. Known unsupported forms are
blocked before execution, and collaborators still validate actual responses.

A report with at least two ordinary rows is charted after validation. A
nonempty funnel also qualifies because it has multiple stages. Ranking defers
the chart until the master has selected the final rows. User requests to omit
charts survive in `defer_chart`. A chart failure does not discard valid data.

## Why /format_funnel remains

The incidents in CONTEXT.md include repeated missing ratio tables, dropped
MD:SD values, fabricated totals and malformed no-data tables. The deterministic
`POST /format_funnel` operation addresses those display failures within this
normaliser service. Removing it would return the table split to model behavior
without evidence that the recurring failures are fixed.

CRM-Funnel sends the complete response to `format_funnel_tables`. It supplies
both tables by default, fixed column order and Indian digit grouping. Totals
come only from the backend. Missing fields, malformed numbers, conflicting
totals, multiple wrapped responses or upstream errors cannot claim successful
rendering. `include_totals=false` handles ranked/filtered/assembled rows;
`period_column` labels time-series rows. Complete raw values remain separate
from display formatting and chart projections.

This endpoint is retained, not required by any CRM backend. Folding the same
renderer into an execution adapter could eliminate a model-controlled tool
step, but that is outside the requested normaliser/behavior scope.

## Verification

From this directory, using an environment with the test dependencies:

```text
python -m pytest tests/ -q
python tests/run_stress.py
python tests/run_batch.py --csv tests/batch_verification.csv
python tests/run_corpus.py --csv tests/corpus_verification.csv
```

The grammar harness imports unchanged reference parsers with database and LLM
access stubbed. These are parser-contract tests, not live CRM accuracy tests.
The batch check requires exact date windows: no one-day or two-day tolerance,
no success for an emitted Event call that crashes, and no success for a missing
date filter. Supported results, clarifications and backend limitations are
reported separately. Review every refusal category as well as passing queries.
A date-window check does not prove metric/filter execution or series shape.

See [PRODUCTION_CHECKS.md](PRODUCTION_CHECKS.md) for the release checklist and
[RUN.md](RUN.md) for deployment and tool attachment. Older grammar findings are
historical evidence; the current capability gate and regression tests define
which forms this release will execute.
