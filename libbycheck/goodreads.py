"""Read a Goodreads shelf from its RSS feed or from the CSV library export.

Goodreads retired its public API in 2020, so these are the two supported routes.

NOTE: unlike the OverDrive side, these parsers have not been run against real Goodreads
output (goodreads.com was not reachable from the environment this was written in, and the
RSS feed needs a private per-user key). Field and column names follow Goodreads' documented
export format and are looked up tolerantly; if a required field is missing the loader raises
rather than guessing, so a format change cannot silently produce an empty report.
"""

from __future__ import annotations

import csv
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from .isbn import to_isbn13
from .models import Book

USER_AGENT = "libbycheck/0.1 (personal library availability checker)"


class GoodreadsError(Exception):
    pass


def redact(text: str) -> str:
    """Hide the private RSS key so it never lands in logs or error messages."""
    return re.sub(r"(key=)[^&\s'\"]+", r"\1***", text)


def _isbns(*raw: str | None) -> frozenset[str]:
    return frozenset(i for i in (to_isbn13(r) for r in raw) if i)


# -- CSV export ------------------------------------------------------------


def load_csv(path: str | Path, shelf: str = "to-read") -> list[Book]:
    path = Path(path)
    try:
        # utf-8-sig: Goodreads/Excel exports may start with a BOM.
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            missing = [c for c in ("Title", "Author") if c not in fields]
            if missing:
                raise GoodreadsError(
                    f"{path}: missing expected column(s) {missing}. Found: {fields}. "
                    "Is this the Goodreads 'Export Library' CSV?"
                )
            books = []
            for row in reader:
                if not _on_shelf(row, shelf):
                    continue
                title = (row.get("Title") or "").strip()
                if not title:
                    continue
                extra = row.get("Additional Authors") or ""
                books.append(
                    Book(
                        title=title,
                        author=(row.get("Author") or "").strip(),
                        gr_id=(row.get("Book Id") or "").strip(),
                        isbns=_isbns(row.get("ISBN13"), row.get("ISBN")),
                        additional_authors=tuple(a.strip() for a in extra.split(",") if a.strip()),
                        date_added=(row.get("Date Added") or "").strip(),
                    )
                )
    except OSError as e:
        raise GoodreadsError(f"cannot read {path}: {e}") from e
    return books


def _on_shelf(row: dict, shelf: str) -> bool:
    if "Exclusive Shelf" not in row and "Bookshelves" not in row:
        return True  # export without shelf info: take everything
    if (row.get("Exclusive Shelf") or "").strip() == shelf:
        return True
    return shelf in [s.strip() for s in (row.get("Bookshelves") or "").split(",")]


# -- RSS feed --------------------------------------------------------------


def _with_params(url: str, **params) -> str:
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    for k, v in params.items():
        if k == "page":
            query[k] = str(v)  # pagination always overrides
        else:
            query.setdefault(k, str(v))  # other params only fill gaps in the user's URL
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _fetch(url: str, timeout: float, opener: Callable) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        hint = " (private profile, or wrong user id/key?)" if e.code in (401, 403, 404) else ""
        raise GoodreadsError(f"Goodreads returned HTTP {e.code} for {redact(url)}{hint}") from e
    except OSError as e:
        raise GoodreadsError(f"could not fetch {redact(url)}: {redact(str(e))}") from e


def parse_rss(xml_text: str) -> list[Book]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise GoodreadsError(
            "Goodreads did not return an RSS feed (got something that is not XML — "
            "often a login page when the profile is private or the key is wrong)."
        ) from e
    if root.tag != "rss":
        raise GoodreadsError(f"unexpected feed format: root element <{root.tag}>, expected <rss>")
    books = []
    for item in root.iter("item"):
        def text(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        title = text("title")
        if not title:
            continue
        books.append(
            Book(
                title=title,
                author=text("author_name"),
                gr_id=text("book_id"),
                isbns=_isbns(text("isbn13"), text("isbn")),
                date_added=text("user_date_added"),
            )
        )
    return books


def load_rss(
    url: str,
    shelf: str = "to-read",
    max_pages: int = 30,
    timeout: float = 30,
    opener: Callable = urllib.request.urlopen,
) -> list[Book]:
    """Fetch a shelf feed, following ``page=N`` until a page adds no new books.

    Stopping on "no new books" (rather than on a short page) means a feed that ignores
    ``page`` ends after page 2 instead of looping, and a feed that returns exactly the
    cap still gets its next page requested.
    """
    url = _with_params(url, shelf=shelf)
    seen: set[str] = set()
    books: list[Book] = []
    for page in range(1, max_pages + 1):
        page_books = parse_rss(_fetch(_with_params(url, page=page), timeout, opener))
        new = [b for b in page_books if (b.gr_id or b.title + "|" + b.author) not in seen]
        if not new:
            break
        for b in new:
            seen.add(b.gr_id or b.title + "|" + b.author)
        books.extend(new)
    else:
        raise GoodreadsError(
            f"stopped after {max_pages} pages without the feed running out; "
            "raise max_pages or use the CSV export"
        )
    return books
