#!/usr/bin/env python3
"""Analyze the discipline distribution of sheet names.

Reads every ``pdfs/requests/<id>/worksheets_metadata.json`` and reports how
sheet ``name`` values break down by discipline (the leading letter code), so we
can decide how to filter the UI down to Architectural (A) and Structural (S)
sheets.

Key gotchas this surfaces (see the README of findings in chat history):
  - ``name`` is OCR-derived and noisy ("SHEET NUMBER: A-0.1", "HEET NUMBER C001").
  - ``category`` is a takeoff/measurement category, NOT a discipline.
  - ~a third of entries have a blank name; most are non-workable.
  - first-letter A/S over-captures sibling codes (AE, AS, SS=sanitary sewer, ...).

Usage::

    python analyze_sheets.py                 # scan pdfs/requests
    python analyze_sheets.py --dir some/path # scan a different requests root
    python analyze_sheets.py --workable-only # restrict to is_workable sheets
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os

from discipline import DISCIPLINE, discipline_code


def iter_sheets(requests_dir: str):
    """Yield each sheet dict from every worksheets_metadata.json under the root."""
    pattern = os.path.join(requests_dir, "*", "worksheets_metadata.json")
    for path in glob.glob(pattern):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        for sheet in data:
            yield sheet


def analyze(requests_dir: str, workable_only: bool) -> None:
    total = 0
    blank = 0
    workable = 0
    no_alpha = 0
    status = collections.Counter()
    first_letter = collections.Counter()
    a_sub = collections.Counter()
    s_sub = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)

    for sheet in iter_sheets(requests_dir):
        total += 1
        status[sheet.get("status")] += 1
        is_workable = bool(sheet.get("is_workable"))
        if is_workable:
            workable += 1
        if workable_only and not is_workable:
            continue

        name = (sheet.get("name") or "").strip()
        if not name:
            blank += 1
            continue

        code = discipline_code(name)
        if code is None:
            no_alpha += 1
            continue

        letter = code[0]
        first_letter[letter] += 1
        if len(examples[letter]) < 5:
            examples[letter].append(name)

        # Second-character breakdown for A and S to expose sub-series (AE, SS...).
        if letter in ("A", "S"):
            rest = code[1:]
            bucket = f"{letter}{rest[0]}*" if rest else f"{letter} (bare/num)"
            (a_sub if letter == "A" else s_sub)[bucket] += 1

    scope = "is_workable only" if workable_only else "all sheets"
    print("=" * 72)
    print(f"Sheet discipline analysis  ({scope})")
    print(f"  requests root: {requests_dir}")
    print("=" * 72)
    print(f"total entries:        {total}")
    print(f"  is_workable=True:   {workable} ({pct(workable, total)})")
    print(f"  blank name:         {blank} ({pct(blank, total)})")
    print(f"  no alpha code:      {no_alpha} ({pct(no_alpha, total)})")
    print(f"  status counts:      {dict(status.most_common())}")

    classified = sum(first_letter.values())
    print()
    print(f"=== Discipline by leading code  (n={classified}) ===")
    for letter, count in first_letter.most_common():
        label = DISCIPLINE.get(letter, "")
        print(
            f"  {letter}  {count:>5} ({pct(count, classified)})  {label:<20}"
            f"  e.g. {examples[letter][:3]}"
        )

    a_total = first_letter["A"]
    s_total = first_letter["S"]
    print()
    print(f"A + S = {a_total + s_total} / {classified} = {pct(a_total + s_total, classified)}")

    print("\n=== A* sub-series ===")
    for bucket, count in a_sub.most_common():
        print(f"  {bucket:>10}  {count}")
    print("\n=== S* sub-series ===")
    for bucket, count in s_sub.most_common():
        print(f"  {bucket:>10}  {count}")


def pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "0.0%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default=os.path.join(os.path.dirname(__file__), "pdfs", "requests"),
        help="requests root holding <id>/worksheets_metadata.json (default: pdfs/requests)",
    )
    parser.add_argument(
        "--workable-only",
        action="store_true",
        help="restrict to sheets with is_workable=True",
    )
    args = parser.parse_args()
    analyze(args.dir, args.workable_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
