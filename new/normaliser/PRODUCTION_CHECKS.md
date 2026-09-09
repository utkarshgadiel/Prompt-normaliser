# Production verification

## Local regression coverage

The automated suite checks date grammar against unchanged reference parsers,
API validation and schema parity, serialized delegation, preserved ranking and
scope, independent metric/date pairing, and deterministic funnel rendering.
Additional boundary tests cover impossible/reversed dates, single-day requests,
unknown names, excluded predicates, lost ratios/stages, duplicate/zero rows,
large numbers, conflicting totals and chart suppression.

`run_stress.py` distinguishes executable phrasing variants from known Event
limitations. `run_batch.py` distinguishes exact report/funnel date matches,
clarifications, blocked backend capabilities and failures. Review category
counts; a clarification or blocked call is not a successful data retrieval.
`run_corpus.py` measures planning coverage on UAT wording, not returned figures.

## Recorded local results ? 9 September 2026

- Unit/contract suite: 164 passed. The unchanged reference modules emitted
  Pydantic deprecation warnings.
- Phrasing stress: 406 checked; 356 executable, 50 known Event limitations,
  zero invariant failures.
- Strict 1,000-prompt batch: 636 exact report matches, 162 exact funnel matches,
  159 backend limitations, 43 clarifications; zero emitted date mismatches or
  parser failures. Results: `tests/batch_verification.csv`.
- UAT planning corpus: 374 prompts; 263 normalized, 61 backend limitations,
  50 clarifications. Results: `tests/corpus_verification.csv`.
- JSON/YAML import specifications agree; tracked diff whitespace checks pass.

These figures describe local tests. They are not a percentage accuracy claim
for the deployed agent, CRM figures, metric predicates or filters. The batch
harness also logged optional NumPy-extension import diagnostics in the local
Anaconda environment; parser runs completed successfully. Runtime deployment
and its pinned dependencies still need the acceptance checks below.

## Immutable-backend limitations

- Event's dated paths can raise `is_qoq`; its YoY parser expands explicit year
  ranges to the served floor through current FY. Only a matching declared
  window is allowed. Do not relabel the extra years as the requested subset.
- Case explicit day ranges can collapse to one day, historical month forms can
  substitute the current year, and month fragments in filter names can alter
  the window. Its rolling-day convention can include an unwanted extra day.
- Targets do not reliably obey explicit range parameters. Verified whole-month
  and whole-FY forms remain available; unsupported combinations are blocked.
- Several funnel services cannot preserve arbitrary day ranges or period
  breakdowns. Some stop when no leads exist, although other independent stages
  could have activity. Empty output must not be interpreted as zero sales.

A behavior file cannot repair these service implementations. New workarounds
must first match requested dates, grain and filters in reference-parser tests,
then actual deployed responses. Preserve honest failures until that evidence
exists. No backend modifications are part of this release.

## Deployment acceptance cases

Run these against the deployed agents and inspect their actual tool traces.
Use a fixed historical period and compare figures with the complete raw CRM
response, not an earlier model-written answer.

| Scenario | Required evidence |
|---|---|
| Overall lead funnel, last FY | Correct funnel tool; both formatted tables; five adjacent ratios; actual chart call carrying stage values |
| Eden historical funnel | Product scope and applied dates match; no-data is a scoped sentence; no inferred zero sales |
| Product/sub-source breakdown | Every row, zero and duplicate survives; total equals backend total, not row sum |
| Three requested financial years | Every call ID is accounted for; no invented period; combined rows retain provenance |
| Leads last FY and sales this month | Exactly the independently paired metric/date calls, no four-call cross product |
| Top five products by sales | Ranking survives delegation; final chart matches the selected rows; no overall Total |
| Project-wise leads for Wave City and Wave Estate | Both named filters survive grouping and decomposition |
| Follow-up ?2? / ?and last month?? | Master resolves context before normalization; funnel stays funnel |
| Unknown owner / excluding junk / invalid date | Clarification, no unfiltered or broader CRM query |
| Event restricted year window / unsafe Case range | Explicit backend limitation; no altered-date retry |
| Missing ratio or failed formatter | No fabricated ratio or manual fallback table; diagnose against original response |
| Chart failure / no URL / no chart requested | Valid tables remain; no invented link; no title-only chart call |
| SOP plus market benchmark | Separate origins; every external number has publisher, year and link from current search |

Repeat representative funnel, ranking and follow-up cases multiple times; one
successful agent trace does not establish consistent behavior. Reject a release
if a displayed number, scope, period, Total or URL cannot be tied to its actual
source response. Do not claim production accuracy from local parser coverage.
