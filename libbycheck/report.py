"""Turn results into rows and render them as a table, TSV (paste into Google Sheets), CSV or JSON."""

from __future__ import annotations

import csv
import io
import json

from .models import BookResult
from .pipeline import STATUS_RANK, best_offer

SUMMARY_COLUMNS = [
    "Title", "Author", "Status", "Library", "Format", "Est. wait (days)", "Holds",
    "Copies available", "Copies owned", "Match", "Notes", "Library link", "Goodreads link",
]
DETAIL_COLUMNS = [
    "Title", "Author", "Library", "Format", "Status", "Est. wait (days)", "Holds",
    "Copies available", "Copies owned", "Lucky Day copies available", "Match", "Catalog title", "Library link",
]
MATCH_LABEL = {
    "isbn": "isbn",
    "exact": "title+author",
    "main-title": "title+author (subtitle differs)",
    "tail": "title+author (franchise prefix)",
    "fuzzy": "title+author (fuzzy)",
}
STATUS_LABEL = {
    "available": "Available now",
    "wait": "Wait list",
    "unavailable": "Owned, not holdable",
    "none": "Not found",
    "error": "Lookup failed",
}


def _n(v) -> str | int:
    """Blank for counts the API did not report (e.g. unlimited-access titles)."""
    return "" if v is None else v


def _status_key(r: BookResult, prefer: list[str]) -> str:
    best = best_offer(r.offers, prefer)
    if best:
        return best.listing.status
    # A failed lookup must not be reported as "not in the catalog".
    return "error" if r.errors else "none"


def summary_rows(results: list[BookResult], prefer: list[str], sort: str = "status") -> list[list]:
    keyed = []
    for idx, r in enumerate(results):
        status = _status_key(r, prefer)
        best = best_offer(r.offers, prefer)
        notes = []
        if best:
            l = best.listing
            if l.pre_release:
                notes.append("pre-release")
            if l.unlimited:
                notes.append("unlimited access")
            if l.copies_reserved_for_holds:
                notes.append(f"{l.available_copies} free copies but {l.holds} holds: likely reserved for the queue")
            if l.lucky_day_available:
                notes.append(f"Lucky Day copies available: {l.lucky_day_available}")
        if r.errors and (best or status == "error"):
            notes.append("lookup failed: " + "; ".join(r.errors))
        l = best.listing if best else None
        wait = "" if (not l or l.status == "available" or l.est_wait_days is None) else l.est_wait_days
        row = [
            r.book.title, r.book.author, STATUS_LABEL[status],
            l.library if l else "", l.media_type if l else "", wait,
            l.holds if l else "", _n(l.available_copies) if l else "", _n(l.owned_copies) if l else "",
            MATCH_LABEL[best.match] if best else "", "; ".join(notes), l.url if l else "", r.book.url,
        ]
        rank = {"available": 0, "wait": 1, "unavailable": 2, "error": 3, "none": 4}[status]
        wait_key = wait if wait != "" else 0
        sort_key = {
            "status": (rank, wait_key, idx),
            "title": (r.book.title.lower(), idx),
        }[sort]
        keyed.append((sort_key, row))
    return [row for _, row in sorted(keyed, key=lambda kr: kr[0])]


def detail_rows(results: list[BookResult]) -> list[list]:
    rows = []
    for r in results:
        offers = sorted(
            r.offers,
            key=lambda o: (STATUS_RANK[o.listing.status], o.listing.library, o.listing.media_type),
        )
        for o in offers:
            l = o.listing
            rows.append([
                r.book.title, r.book.author, l.library, l.media_type, STATUS_LABEL[l.status],
                "" if l.status == "available" or l.est_wait_days is None else l.est_wait_days,
                l.holds, _n(l.available_copies), _n(l.owned_copies), l.lucky_day_available,
                MATCH_LABEL[o.match], l.title + (f": {l.subtitle}" if l.subtitle else ""), l.url,
            ])
    return rows


def _clean(v) -> str:
    # Tabs/newlines inside a cell would break TSV pasting into Sheets.
    return str(v).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def render(rows: list[list], columns: list[str], fmt: str) -> str:
    if fmt == "json":
        return json.dumps([dict(zip(columns, r)) for r in rows], indent=2, ensure_ascii=False)
    if fmt in ("tsv", "csv"):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter="\t" if fmt == "tsv" else ",", lineterminator="\n")
        w.writerow(columns)
        w.writerows([[_clean(c) for c in r] for r in rows])
        return buf.getvalue()
    if fmt == "table":
        return _table(rows, columns)
    raise ValueError(f"unknown format: {fmt}")


def _table(rows: list[list], columns: list[str], max_width: int = 38) -> str:
    # Console view: drop the long link/notes columns; use --format tsv/csv/json for everything.
    skip = {"Library link", "Goodreads link", "Notes", "Catalog title"}
    keep = [i for i, c in enumerate(columns) if c not in skip]

    def cell(v) -> str:
        s = _clean(v)
        return s if len(s) <= max_width else s[: max_width - 1] + "…"

    data = [[columns[i] for i in keep]] + [[cell(r[i]) for i in keep] for r in rows]
    widths = [max(len(row[j]) for row in data) for j in range(len(keep))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in data]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)
