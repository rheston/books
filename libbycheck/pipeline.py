"""Look every book up at every library and verify the hits."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable

from .match import STRONG, main_title, match, query_surname
from .models import Book, BookResult, Listing, Offer
from .overdrive import ApiError, Client

STATUS_RANK = {"available": 0, "wait": 1, "unavailable": 2}


def queries_for(book: Book) -> list[str]:
    """Search queries to try, most specific first. The search is free text only (a
    ``title:`` field syntax returned nothing), and an ISBN only finds the one edition
    that carries it, so ISBN is the last resort rather than the first."""
    title = main_title(book.title)
    last = query_surname(book.author)
    queries = []
    if title and last:
        queries.append(f"{title} {last}")
    if title:
        queries.append(title)
    queries.extend(sorted(book.isbns))
    seen = set()
    return [q for q in queries if not (q in seen or seen.add(q))]


def find_offers(
    client: Client,
    library: str,
    book: Book,
    types: Iterable[str],
    languages: Iterable[str],
) -> list[Offer]:
    types, languages = set(types), set(languages)
    weak: list[Offer] = []
    for query in queries_for(book):
        offers = []
        for listing in client.search(library, query):
            if not listing.in_collection:
                continue
            if types and listing.media_type not in types:
                continue
            if languages and listing.languages and not languages.intersection(listing.languages):
                continue
            kind = match(book, listing)
            if kind:
                offers.append(Offer(listing, kind))
        # Prefer exact/ISBN matches. Partial matches are only a fallback: a library that has
        # "Thrawn" must not also offer "Thrawn: Alliances" just because the titles overlap.
        strong = [o for o in offers if o.match in STRONG]
        if strong:
            return strong
        weak = weak or offers
    return weak


def check_books(
    books: list[Book],
    libraries: list[str],
    client: Client,
    types: Iterable[str],
    languages: Iterable[str],
    workers: int = 4,
    progress: Callable[[int, int], None] | None = None,
) -> list[BookResult]:
    """Results come back in the same order as ``books``. A failed lookup is recorded on
    the result, not raised, so one flaky library cannot sink the whole run."""
    results = [BookResult(b) for b in books]
    tasks = [(i, lib) for i in range(len(books)) for lib in libraries]
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(find_offers, client, lib, books[i], types, languages): (i, lib) for i, lib in tasks}
        for fut in as_completed(futures):
            i, lib = futures[fut]
            try:
                results[i].offers.extend(fut.result())
            except ApiError as e:
                results[i].errors.append(f"{lib}: {e}")
            done += 1
            if progress:
                progress(done, len(tasks))
    return results


def best_offer(offers: list[Offer], prefer: list[str]) -> Offer | None:
    """Available now beats any wait; then shortest estimated wait; then the preferred
    format order; then most copies owned."""
    if not offers:
        return None

    def key(o: Offer):
        l: Listing = o.listing
        fmt_rank = prefer.index(l.media_type) if l.media_type in prefer else len(prefer)
        wait = l.est_wait_days if l.est_wait_days is not None else 10**6
        return (STATUS_RANK[l.status], 0 if l.status == "available" else wait, fmt_rank, -(l.owned_copies or 0), l.library)

    return min(offers, key=key)
