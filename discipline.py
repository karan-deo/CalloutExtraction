#!/usr/bin/env python3
"""Shared sheet-name parsing and discipline classification.

Sheet ``name`` values are OCR-derived and noisy ("SHEET NUMBER: A-0.1",
"HEET NUMBER C001"). This module centralises the label-stripping + leading-code
logic so the analysis tool (``analyze_sheets.py``) and the UI's required-sheet
filter (``app.py``) classify names identically and can't drift apart.
"""

from __future__ import annotations

import re

# Strip OCR'd label noise ("SHEET NUMBER:", "DRAWING NO.", ...) so the real
# discipline code underneath is what gets classified.
_LABEL = re.compile(
    r"^(SHEET\s*(NO\.?|NUMBER)?[:.\s-]*"
    r"|H?EET\s*(NO\.?|NUMBER)?[:.\s-]*"
    r"|DRAWING\s*(NO\.?|NUMBER)?[:.\s-]*"
    r"|NUMBER[:.\s-]*"
    r"|PROJECT\s*NO[:.\s-]*)",
    re.IGNORECASE,
)

DISCIPLINE = {
    "A": "Architectural",
    "S": "Structural",
    "E": "Electrical",
    "M": "Mechanical",
    "P": "Plumbing",
    "C": "Civil",
    "G": "General",
    "L": "Landscape/LifeSafety",
    "F": "Fire",
    "I": "Interior",
}

# Required-sheet filter. Each discipline owns a list of independent regex
# patterns matched against the OCR-cleaned name (see ``clean_name``); a sheet is
# "required" if ANY pattern in ANY discipline matches. Patterns are intentionally
# granular so a rule can be added or removed as a single line.
#
# The active rules are catch-alls that capture EVERY A*/S* code (bare A#/S# plus
# all sub-series), since we don't yet know which sub-series are needed. To narrow
# later, swap a catch-all for the commented bare-code rule and/or fold in the
# explicit sub-series rules.
REQUIRED_SHEETS: dict[str, list[str]] = {
    "Architectural": [
        r"^A",  # catch-all: every A* code (A#, AE, AD, AS, AR, AV, ...). Narrow later.
        # r"^A(?![A-Z])",  # bare A# only (A-0.1, A001, A) -- swap in to drop sub-series
        # r"^AE", r"^AD", r"^AS", r"^AR", r"^AV",   # explicit sub-series, fold in as decided
    ],
    "Structural": [
        r"^S",  # catch-all: every S* code (S#, SE, SD, SS, ST, ...). Narrow later.
        # r"^S(?![A-Z])",  # bare S# only (S2, S-1) -- swap in to drop sub-series
        # r"^SE", r"^SD", r"^SS", r"^ST",           # explicit sub-series, fold in as decided
    ],
}

# Compile once at import. ``clean_name`` upper-cases, so no IGNORECASE needed.
_REQUIRED_COMPILED: dict[str, list[re.Pattern[str]]] = {
    label: [re.compile(pat) for pat in patterns]
    for label, patterns in REQUIRED_SHEETS.items()
}


def clean_name(name: str | None) -> str:
    """Strip OCR label noise, upper-case, and trim a sheet name.

    ``"SHEET NUMBER: A-0.1"`` -> ``"A-0.1"``, ``""`` / ``None`` -> ``""``.
    """
    return _LABEL.sub("", (name or "").strip().upper()).strip()


def discipline_code(name: str | None) -> str | None:
    """Return the leading alpha code of a sheet name after stripping OCR labels.

    ``"SHEET NUMBER: A-0.1"`` -> ``"A"``, ``"S2 (STEEL)"`` -> ``"S"``,
    ``"123"`` / ``""`` -> ``None``.
    """
    match = re.match(r"^([A-Z]+)", clean_name(name))
    return match.group(1) if match else None


def classify(name: str | None) -> str | None:
    """Return the required-discipline label a sheet name matches, else ``None``."""
    cleaned = clean_name(name)
    if not cleaned:
        return None
    for label, patterns in _REQUIRED_COMPILED.items():
        if any(pat.search(cleaned) for pat in patterns):
            return label
    return None


def is_required(name: str | None) -> bool:
    """True if a sheet name matches any ``REQUIRED_SHEETS`` pattern."""
    return classify(name) is not None
