"""Decide whether an OverDrive catalog record is the same book as a Goodreads entry.

Catalog search is fuzzy (a query for one novel returns unrelated titles by the same
author), so every search hit is verified here. We prefer a missed match to a wrong one:
a wrong "available now" wastes the reader's time, while a missed one is visible in the
"Not found" bucket and can be checked by hand.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

from .models import Book, Listing

_BRACKETS = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
_SUBTITLE_SPLIT = re.compile(r"\s*[:—–]\s+|\s+-\s+")
_ARTICLES = {"the", "a", "an"}
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}
FUZZY_TITLE_THRESHOLD = 0.92
# A subtitle this generic says nothing about which book it is.
_GENERIC_TAILS = {"novel", "memoir", "stories", "poems", "essays", "thriller", "mystery", "romance", "a story"}
# Subtitles that only describe the edition or genre ("A Novel", "Unabridged", "50th Anniversary
# Edition") do not change which book it is, unlike series continuations ("Alliances", "Treason").
_BENIGN_SUBTITLE = re.compile(
    r"\b(novel|novella|memoir|stories|poems|poetry|essays|thriller|mystery|romance|edition|"
    r"unabridged|abridged|anniversary|reissue|reprint|annotated|illustrated|revised|expanded)\b"
)
# A Goodreads subtitle this long is descriptive prose, not a series installment name.
_LONG_SUBTITLE_WORDS = 4
# Match kinds, strongest first. Only these two are trusted when anything better exists.
STRONG = ("isbn", "exact")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str) -> str:
    s = _strip_accents(s).lower().replace("&", " and ").replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _drop_article(n: str) -> str:
    parts = n.split()
    return " ".join(parts[1:]) if len(parts) > 1 and parts[0] in _ARTICLES else n


def title_forms(title: str) -> tuple[str, str, str]:
    """(full, main, tail) normalised forms. Series/edition brackets are removed; ``main`` is
    the part before the subtitle and ``tail`` the subtitle ("" if none). Leading articles are
    ignored (OverDrive sorts without them)."""
    t = _BRACKETS.sub("", title)
    parts = _SUBTITLE_SPLIT.split(t, maxsplit=1)
    full = _drop_article(norm(t))
    main = _drop_article(norm(parts[0]))
    tail = _drop_article(norm(parts[1])) if len(parts) > 1 else ""
    return full, main, tail


def main_title(title: str) -> str:
    """Human-readable title without series brackets or subtitle, for building search queries."""
    t = _BRACKETS.sub("", title)
    return _SUBTITLE_SPLIT.split(t, maxsplit=1)[0].strip()


def title_match(gr_title: str, od_title: str, od_subtitle: str = "") -> str | None:
    """Return "exact", "main-title", "tail", "fuzzy" or None."""
    od_full_text = od_title
    if od_subtitle and norm(od_subtitle) not in norm(od_title):
        od_full_text = f"{od_title}: {od_subtitle}"
    g_full, g_main, g_tail = title_forms(gr_title)
    o_full, o_main, o_tail = title_forms(od_full_text)
    if not g_full or not o_full:
        return None
    if g_full == o_full:
        return "exact"
    if g_main == o_main:
        g_plain = not g_tail or bool(_BENIGN_SUBTITLE.search(g_tail))
        o_plain = not o_tail or bool(_BENIGN_SUBTITLE.search(o_tail))
        if g_plain and o_plain:
            return "exact"  # differ only by a descriptive subtitle, e.g. "The Overstory" / "The Overstory: A Novel"
        if g_tail and not o_tail and len(g_tail.split()) >= _LONG_SUBTITLE_WORDS:
            return "exact"  # Goodreads spells out a long subtitle the catalog record omits
        # Otherwise only trust the main title when at most one side has a subtitle; if both have
        # different ones, "Star Wars: Thrawn" would match "Star Wars: Thrawn Alliances".
        if not (g_tail and o_tail):
            return "main-title"
    # Franchise prefix on one side only: Goodreads "Star Wars: Thrawn" vs catalog "Thrawn".
    if g_tail and not o_tail and g_tail == o_full and g_tail not in _GENERIC_TAILS:
        return "tail"
    if o_tail and not g_tail and o_tail == g_full and o_tail not in _GENERIC_TAILS:
        return "tail"
    if difflib.SequenceMatcher(None, g_full, o_full).ratio() >= FUZZY_TITLE_THRESHOLD:
        return "fuzzy"
    return None


def author_key(name: str) -> tuple[str, str]:
    """(last name, first initial) from "First Last", "Last, First" or "J.R.R. Tolkien"."""
    name = _strip_accents(name).replace("'", "").replace("’", "")
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first} {last}"
    tokens = [t for t in re.sub(r"[^A-Za-z]+", " ", name).lower().split() if t not in _NAME_SUFFIXES]
    if not tokens:
        return "", ""
    first_initial = tokens[0][0] if len(tokens) > 1 else ""
    return tokens[-1], first_initial


def query_surname(name: str) -> str:
    """Surname as written (case and accents kept) for use in a search query."""
    if "," in name:
        return name.partition(",")[0].strip()
    tokens = [t for t in name.replace(".", " ").split() if t.lower().strip(",") not in _NAME_SUFFIXES]
    return tokens[-1] if tokens else ""


def authors_match(gr_authors: tuple[str, ...], od_creators: list[str]) -> bool:
    for g in gr_authors:
        g_last, g_init = author_key(g)
        if not g_last:
            continue
        for o in od_creators:
            o_last, o_init = author_key(o)
            if g_last == o_last and (not g_init or not o_init or g_init == o_init):
                return True
    return False


def match(book: Book, listing: Listing) -> str | None:
    """How (if at all) ``listing`` matches ``book``: "isbn", or a title kind
    ("exact", "main-title", "tail", "fuzzy") that also had a matching author, or None."""
    if book.isbns and book.isbns.intersection(listing.isbns):
        return "isbn"
    kind = title_match(book.title, listing.title, listing.subtitle)
    if kind and authors_match(book.authors, listing.creators):
        return kind
    return None
