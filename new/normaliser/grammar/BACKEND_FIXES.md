# Backend fixes required — verified, one line each

Two defects in `code/old/` block correct answers that no amount of prompt or
normaliser work can reach. Both fixes are one line. Both were applied to a
copy and re-verified against the real prompt corpus, so the effect is measured,
not predicted.

Nothing in `code/old/` has been modified. Apply these to the deployed services.

---

## 1. `event_report.py` — `NameError: is_qoq` (HTTP 500)

**Impact: 162 of 1,000 corpus prompts.** Every event, meeting or appointment
question carrying a date returns HTTP 500. Only date-free questions and the
`yoy` form survive.

`detect_date_intent` (line 1780) reads `is_qoq` at lines 1829 and 2002 but
never assigns it. The name is only bound inside two unrelated module-level
functions, a different scope, so the lookup raises and the `NameError`
propagates uncaught to the endpoint handler at line 2956.

`lead_report.py:2844` contains exactly the missing line.

### The fix

In `event_report.py`, inside `detect_date_intent`, immediately **before** the
existing `is_yoy = any(...)` assignment at line 1795, insert:

```python
        is_qoq  = any(k in q for k in ["qoq","quarter over quarter","quarter-on-quarter","quarterly","quarter wise","quarter on quarter"])
```

Indentation is eight spaces — it sits beside `is_mom` and `is_yoy` in the same
method.

### Verification performed

The patch was applied to a copy (`event_report_patched.py`) and all 164 event
calls the normaliser emits across the 1,000-prompt corpus were re-parsed
against it:

```
EVENT with one-line fix applied: ok=164 mismatch=0 noparse=0 err=0
```

Every one resolved to exactly the window the normaliser declared. No other
change is needed on the event path.

`tests/test_grammar_contract.py::test_event_service_is_broken` pins the bug and
will FAIL once this lands — that failure is the signal to move `Tool.EVENT`
into the verified sets in `render.py` and re-run `grammar/probe.py`.

---

## 2. `lead_conversion_funnel.py` — unvalidated LLM dates produce inverted ranges

**Impact: intermittent, silent.** Observed in production on 27 Aug 2026: the
question `funnel fy 2026` came back with `"filter": "2026-04-01 to 2026-03-31"`
— an end date before the start date. In SQL `BETWEEN` that matches nothing, so
the service returned a **successful empty funnel** rather than an error.

The endpoint runs an LLM extraction first and uses its output directly.
`DateResolver._from_llm` (line 720) catches malformed JSON and bad ISO strings,
but nothing checks that `end >= start`. The deterministic regex fallback —
which parses `fy 2026` correctly — only runs when the LLM path yields no
periods at all, so a reversed pair is accepted instead of triggering it.

### The fix

In `_from_llm`, reject any period whose end precedes its start, so the resolver
falls through to the regex path:

```python
        for p in intent.get("periods", []):
            try:
                start = date.fromisoformat(p["start_date"])
                end   = date.fromisoformat(p["end_date"])
                if end < start:                     # LLM slip: never a real window
                    logger.warning("Discarding inverted LLM period %s", p)
                    continue
                out.append(Period(label=p["label"], start=start, end=end))
            except (KeyError, ValueError) as exc:
                logger.warning("Bad LLM period %s: %s", p, exc)
```

The other six funnel services share this architecture and should get the same
guard.

### Until it lands

`crm_funnel_agent_behavior.md` detects the inversion from the response's own
`filter` field and reports an error instead of passing empty data through, and
`master_agent_behavior.md` retries the identical call once, since the
extraction is not deterministic.

---

## Priority

Fix 1 is the cheapest accuracy available anywhere in this system: one line
recovers 162 of 1,000 prompts, and the resulting plans are already verified
correct. Fix 2 prevents a silent wrong answer, which is the more dangerous
class even though it fires less often.
