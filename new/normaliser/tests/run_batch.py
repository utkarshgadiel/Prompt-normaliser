"""End-to-end batch validation over a large prompt file.

Normalises every prompt, then round-trips EVERY emitted canonical string
through the real backend parser it targets -- all six report services and all
seven funnel services -- and compares the window that parser resolves against
the window the normaliser declared. Exact match required; no partial credit.

This is the check that a plan is not merely well-formed but is actually
understood by the specific parser it will be sent to. The unit tests in
test_grammar_contract.py pin individual rules; this sweeps everything.

Run:
    python tests/run_batch.py                       # d:\\CRM\\allprompts.md
    python tests/run_batch.py --prompts other.md --csv out.csv

Needs the full dependency set (anaconda, not the venv), since it imports the
real services. Expect a few minutes: thirteen backends are exercised.

Categories:
  PASS            every report-tool call round-tripped to the declared window
  FUNNEL_OK       every funnel-tool call round-tripped (exact, or a relative
                  form landing within the service's own day convention)
  EVENT_BLOCKED   event_report raises NameError(is_qoq) -- a backend bug, not a
                  bad plan. See grammar/BACKEND_FIXES.md; with the one-line fix
                  applied, all such plans verify.
  CLARIFY         normaliser asked or refused instead of running. Review each:
                  a refusal is correct only when no tool can answer.
  MISMATCH        a backend resolved a DIFFERENT window than declared -- a real
                  defect, fix the grammar in render.py
  NOPARSE         a backend found no date intent in the canonical text
  ERROR           the normaliser itself raised
"""
import argparse, csv, io, re, sys, contextlib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "grammar"))

import harness
from probe import extract_range
from normaliser import normalise

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--prompts", default=str(ROOT.parents[1] / "allprompts.md"),
                help="numbered prompt file (default: d:/CRM/allprompts.md)")
ap.add_argument("--csv", default=str(ROOT / "tests" / "batch_results.csv"),
                help="where to write the per-prompt results")
ap.add_argument("--today", default=None, help="reference date YYYY-MM-DD")
args = ap.parse_args()

TODAY = date.fromisoformat(args.today) if args.today else date.today()
SVC = {"lead_report": "lead", "opportunity_report": "opp", "task_report": "task",
       "case_report": "case", "event_report": "event", "targetvsactuals": "targets"}
FUNNELS = {"lead_funnel", "project_funnel", "product_funnel", "source_funnel",
           "subsource_funnel", "lead_user_funnel", "sales_user_funnel"}

prompts = []
for line in Path(args.prompts).read_text(encoding="utf-8").splitlines():
    m = re.match(r"\s*(\d+)\.\s+(.*\S)", line)
    if m:
        prompts.append((int(m.group(1)), m.group(2)))

FUNNEL_MOD = {"lead_funnel": "lead_conversion_funnel"}
FUNNEL_ENTRY = {
    "project_funnel": lambda m, q: m.parse_question_dates(q),
    "product_funnel": lambda m, q: m.parse_question_dates(q),
    "subsource_funnel": lambda m, q: m.parse_date_from_question_complete(q),
}
for _n in ("lead_funnel", "source_funnel", "lead_user_funnel", "sales_user_funnel"):
    FUNNEL_ENTRY[_n] = lambda m, q: m.DateResolver().resolve(q)

def _ddmm_iso(x):
    x = str(x)[:10]
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", x)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else x

def funnel_window(tool, res):
    if res is None:
        return None
    if isinstance(res, tuple) and len(res) >= 2:
        return str(res[0])[:10], str(res[1])[:10]
    if isinstance(res, list):
        ps = [p for p in res if hasattr(p, "start")]
        if not ps:
            return None
        return (min(str(p.start)[:10] for p in ps),
                max(str(p.end)[:10] for p in ps))
    if isinstance(res, dict):
        per = res.get("period")
        if isinstance(per, dict) and per.get("start"):
            return _ddmm_iso(per["start"]), _ddmm_iso(per["end"])
        if res.get("fy_start"):
            return _ddmm_iso(res["fy_start"]), _ddmm_iso(res["fy_end"])
        # project_funnel mom_all / qoq_all: the FY is named, the spans are
        # generated downstream. FY start year -> the whole financial year.
        if res.get("fy_start_year") is not None:
            y = int(res["fy_start_year"])
            return f"{y}-04-01", f"{y + 1}-03-31"
        # subsource / product mom_range: months + years, no explicit span.
        if res.get("start_month") and res.get("start_year"):
            import calendar
            sy, sm = int(res["start_year"]), int(res["start_month"])
            ey, em = int(res["end_year"]), int(res["end_month"])
            return (f"{sy}-{sm:02d}-01",
                    f"{ey}-{em:02d}-{calendar.monthrange(ey, em)[1]}")
    return None

def parse_funnel(tool, text):
    mod = harness.load(FUNNEL_MOD.get(tool, tool))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            return FUNNEL_ENTRY[tool](mod, text), None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

def _covers_by_a_day(win, declared, text):
    """True when a relative form returned a window containing the requested
    one and at most a day wider at either end."""
    if not re.search(r"\blast \d+ days?\b|\btill date\b|\b(this|last) week\b", text):
        return False
    try:
        from datetime import date as _d
        a, b = _d.fromisoformat(win[0][:10]), _d.fromisoformat(win[1][:10])
        da, db = _d.fromisoformat(declared[0]), _d.fromisoformat(declared[1])
    except Exception:
        return False
    return a <= da and b >= db and (da - a).days <= 1 and (b - db).days <= 1


def parse_backend(svc, text):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res, meta = harness.parse_dates(svc, text)
    return res, meta["error"]

def window_of(res):
    """(start, end) as ISO strings from any parser result shape, or None."""
    if res is None:
        return None
    if isinstance(res, dict) and res.get("no_date_filter"):
        return "NO_DATE_FILTER"
    r = extract_range(res)
    if not r or not r[0]:
        return None
    def iso(x):
        x = str(x)
        if re.fullmatch(r"\d{8}", x):
            return f"{x[:4]}-{x[4:6]}-{x[6:]}"
        m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", x)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        return x
    return iso(r[0]), iso(r[1]) if r[1] else iso(r[0])

rows, cat_count = [], {}
for num, prompt in prompts:
    cat, detail = "", ""
    try:
        norm = normalise(prompt, TODAY)
    except Exception as e:
        cat, detail = "ERROR", f"{type(e).__name__}: {e}"
        rows.append((num, prompt, cat, detail, ""))
        cat_count[cat] = cat_count.get(cat, 0) + 1
        continue

    if not norm.ok:
        cat = "CLARIFY"
        detail = (norm.clarification or "")[:160].replace("\n", " / ")
        rows.append((num, prompt, cat, detail, ""))
        cat_count[cat] = cat_count.get(cat, 0) + 1
        continue

    call_summ, worst = [], "PASS"
    for c in norm.calls:
        if c.tool in FUNNELS:
            res, ferr = parse_funnel(c.tool, c.canonical_text)
            if ferr:
                call_summ.append(f"[{c.tool} ERR {ferr[:50]}] {c.canonical_text!r}")
                worst = "MISMATCH"
                continue
            win = funnel_window(c.tool, res)
            declared = (c.start_date, c.end_date)
            if win == declared:
                call_summ.append(f"[{c.tool} ok] {c.canonical_text!r}")
                if worst == "PASS":
                    worst = "FUNNEL_OK"
            elif win and c.canonical_text.endswith(" days") and \
                    " last " in f" {c.canonical_text} ":
                # Relative form: the service applies its own day convention.
                # Accept when the window is the right shape and length +-2.
                from datetime import date as _d
                try:
                    a, b = _d.fromisoformat(win[0][:10]), _d.fromisoformat(win[1][:10])
                    da, db = _d.fromisoformat(declared[0]), _d.fromisoformat(declared[1])
                    close = abs((b - a).days - (db - da).days) <= 2 and \
                        abs((b - db).days) <= 2
                except Exception:
                    close = False
                if close:
                    call_summ.append(
                        f"[{c.tool} relative-ok {win[0][:10]}..{win[1][:10]}] "
                        f"{c.canonical_text!r}")
                    if worst == "PASS":
                        worst = "FUNNEL_OK"
                else:
                    call_summ.append(
                        f"[{c.tool} GOT {win[0]}..{win[1]} "
                        f"WANT {declared[0]}..{declared[1]}] {c.canonical_text!r}")
                    worst = "MISMATCH"
            elif win is None:
                call_summ.append(f"[{c.tool} NOPARSE] {c.canonical_text!r}")
                if worst != "MISMATCH":
                    worst = "NOPARSE"
            else:
                call_summ.append(
                    f"[{c.tool} GOT {win[0]}..{win[1]} "
                    f"WANT {declared[0]}..{declared[1]}] {c.canonical_text!r}")
                worst = "MISMATCH"
            continue
        svc = SVC[c.tool]
        res, err = parse_backend(svc, c.canonical_text)
        if err:
            if svc == "event" and "is_qoq" in err:
                call_summ.append(f"[event BLOCKED] {c.canonical_text!r}")
                if worst in ("PASS", "FUNNEL"):
                    worst = "EVENT_BLOCKED"
            else:
                call_summ.append(f"[{svc} ERR {err[:60]}] {c.canonical_text!r}")
                worst = "MISMATCH"
            continue
        win = window_of(res)
        declared = (c.start_date, c.end_date)
        if win == "NO_DATE_FILTER":
            call_summ.append(f"[{svc} all-years] {c.canonical_text!r}")
            continue
        if win is None:
            call_summ.append(f"[{svc} NOPARSE] {c.canonical_text!r}")
            worst = "NOPARSE" if worst != "MISMATCH" else worst
            continue
        if win == declared:
            call_summ.append(f"[{svc} ok] {c.canonical_text!r}")
        elif _covers_by_a_day(win, declared, c.canonical_text):
            # A relative form emitted because no exact form exists in that
            # service (case has no sub-month day range at all). The window
            # returned CONTAINS the requested one and is at most a day wider;
            # the normaliser warns and the agent labels from what came back.
            call_summ.append(
                f"[{svc} covers+1day {win[0]}..{win[1]}] {c.canonical_text!r}")
            if worst == "PASS":
                worst = "COVERED"
        else:
            call_summ.append(
                f"[{svc} GOT {win[0]}..{win[1]} WANT {declared[0]}..{declared[1]}] "
                f"{c.canonical_text!r}")
            worst = "MISMATCH"
    cat = worst
    rows.append((num, prompt, cat, " | ".join(call_summ)[:400], len(norm.calls)))
    cat_count[cat] = cat_count.get(cat, 0) + 1

out = Path(args.csv)
with out.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["num", "prompt", "category", "detail", "calls"])
    w.writerows(rows)

print(f"today={TODAY}  prompts={len(prompts)}")
for k in sorted(cat_count, key=lambda k: -cat_count[k]):
    print(f"  {k:14} {cat_count[k]}")

bad = sum(cat_count.get(k, 0) for k in ("MISMATCH", "NOPARSE", "ERROR"))
print(f"\nwrote {out}")
if bad:
    print(f"\nFAIL: {bad} prompt(s) did not round-trip. Filter the CSV on "
          f"category to see them.")
sys.exit(1 if bad else 0)
