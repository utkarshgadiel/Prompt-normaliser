"""
Run the normaliser over the real prompt corpora and report coverage.

Corpora:
  new.md        -- 262 client UAT prompts
  crmprompts.md -- 130 earlier production prompts

Funnel prompts belong to the CRM-Funnel agent and are out of scope for this
phase; they are counted separately rather than as failures.

Run: python tests/run_corpus.py [--csv out.csv]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from normaliser import normalise  # noqa: E402

CRM_ROOT = ROOT.parents[1]
TODAY = date(2026, 8, 26)

FUNNEL = re.compile(r"\bfunnel\b|\bconversion\b", re.I)


def load(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        s = re.sub(r"^\d+[\.\)]?\s+", "", s)          # strip list numbering
        if s and not s.startswith("#"):
            out.append(s)
    return out


def classify(q: str):
    """Return (bucket, result). Buckets are mutually exclusive."""
    if FUNNEL.search(q):
        return "funnel_out_of_scope", None
    r = normalise(q, TODAY)
    if not r.ok:
        return "needs_clarification", r
    if r.unknown_entities:
        return "unknown_entity", r
    return "normalised", r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    corpora = {
        "new.md (UAT)": load(CRM_ROOT / "new.md"),
        "crmprompts.md": load(CRM_ROOT / "crmprompts.md"),
    }

    rows = []
    grand = Counter()

    for name, prompts in corpora.items():
        counts = Counter()
        tools = Counter()
        warn = Counter()
        for q in prompts:
            bucket, r = classify(q)
            counts[bucket] += 1
            grand[bucket] += 1
            if r and r.ok:
                for c in r.calls:
                    tools[c.tool] += 1
                for w in r.warnings:
                    warn[w.split(";")[0][:60]] += 1
            rows.append({
                "corpus": name, "prompt": q, "bucket": bucket,
                "calls": len(r.calls) if r else 0,
                "tools": "|".join(sorted({c.tool for c in r.calls})) if r else "",
                "decomposed": bool(r and r.decomposed),
                "canonical": r.calls[0].canonical_text if (r and r.calls) else "",
                "clarification": (r.clarification or "") if r else "",
            })

        total = len(prompts)
        in_scope = total - counts["funnel_out_of_scope"]
        print(f"\n=== {name} — {total} prompts ===")
        print(f"  funnel (other agent)   {counts['funnel_out_of_scope']:4d}")
        print(f"  in scope               {in_scope:4d}")
        if in_scope:
            ok = counts["normalised"]
            print(f"    normalised           {ok:4d}   {ok / in_scope * 100:5.1f}%")
            print(f"    needs clarification  {counts['needs_clarification']:4d}   "
                  f"{counts['needs_clarification'] / in_scope * 100:5.1f}%")
        print("  tool distribution: " + ", ".join(
            f"{t.replace('_report','')}={n}" for t, n in tools.most_common()))
        if warn:
            print("  top warnings:")
            for w, n in warn.most_common(3):
                print(f"    {n:4d}  {w}")

    print(f"\n=== combined ===")
    tot = sum(grand.values())
    ins = tot - grand["funnel_out_of_scope"]
    print(f"  {tot} prompts, {ins} in scope for CRM-Data")
    if ins:
        print(f"  normalised {grand['normalised']}/{ins} = "
              f"{grand['normalised'] / ins * 100:.1f}%")

    if args.csv:
        out = Path(args.csv)
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
