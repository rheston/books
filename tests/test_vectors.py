"""Run the shared vectors (tests/vectors.json) against the Python implementation.
web/test/core.test.js runs the same file against the JavaScript port."""

import json
import unittest
from pathlib import Path

from libbycheck import report
from libbycheck.isbn import to_isbn13
from libbycheck.match import author_key, authors_match, query_surname, title_match
from libbycheck.models import Book, BookResult, Listing, Offer
from libbycheck.pipeline import best_offer, find_offers, queries_for

V = json.loads((Path(__file__).parent / "vectors.json").read_text(encoding="utf-8"))
DEFAULTS = V["listing_defaults"]


def listing(partial: dict) -> Listing:
    return Listing(**{**DEFAULTS, **partial})


def book(d: dict) -> Book:
    return Book(title=d["title"], author=d["author"], isbns=frozenset(d.get("isbns", [])))


class FakeClient:
    def __init__(self, catalog):
        self.catalog = catalog

    def search(self, library, query, per_page=24):
        return [listing(p) for p in self.catalog.get(query, [])]


class VectorTests(unittest.TestCase):
    def test_to_isbn13(self):
        for raw, expect in V["toIsbn13"]:
            self.assertEqual(to_isbn13(raw), expect, raw)

    def test_query_surname(self):
        for name, expect in V["querySurname"]:
            self.assertEqual(query_surname(name), expect, name)

    def test_author_key(self):
        for name, expect in V["authorKey"]:
            self.assertEqual(list(author_key(name)), expect, name)

    def test_authors_match(self):
        for c in V["authorsMatch"]:
            self.assertEqual(authors_match(tuple(c["gr"]), c["od"]), c["expect"], c)

    def test_title_match(self):
        for c in V["titleMatch"]:
            self.assertEqual(title_match(c["gr"], c["od"], c.get("sub", "")), c["expect"], c)

    def test_status(self):
        for c in V["status"]:
            l = listing(c["listing"])
            self.assertEqual(l.status, c["status"], c["name"])
            self.assertEqual(l.in_collection, c["in_collection"], c["name"])
            self.assertEqual(l.copies_reserved_for_holds, c["reserved"], c["name"])

    def test_queries_for(self):
        for c in V["queriesFor"]:
            self.assertEqual(queries_for(book(c["book"])), c["expect"], c)

    def test_find_offers(self):
        for c in V["findOffers"]:
            offers = find_offers(FakeClient(c["catalog"]), "a", book(c["book"]), c.get("types", ["ebook", "audiobook"]), c.get("languages", ["en"]))
            got = [[o.listing.title, o.listing.media_type, o.match] for o in offers]
            self.assertEqual(got, c["expect"], c["name"])

    def test_best_offer(self):
        for c in V["bestOffer"]:
            offers = [Offer(listing(p), "exact") for p in c["offers"]]
            self.assertIs(best_offer(offers, c["prefer"]), offers[c["expect"]], c["name"])

    def test_report_golden(self):
        """The golden TSV/CSV was rendered by Python and pins the JS port to byte-identical output."""
        g = V["report"]
        results = [
            BookResult(
                Book(title=c["book"]["title"], author=c["book"]["author"], gr_id=c["book"]["gr_id"],
                     isbns=frozenset(c["book"]["isbns"]), additional_authors=tuple(c["book"]["additional_authors"])),
                [Offer(listing(o["listing"]), o["match"]) for o in c["offers"]], list(c["errors"]))
            for c in g["results"]]
        p = g["prefer"]
        self.assertEqual(report.render(report.summary_rows(results, p, "status"), report.SUMMARY_COLUMNS, "tsv"), g["summary_status_tsv"])
        self.assertEqual(report.render(report.summary_rows(results, p, "title"), report.SUMMARY_COLUMNS, "csv"), g["summary_title_csv"])
        self.assertEqual(report.render(report.detail_rows(results), report.DETAIL_COLUMNS, "tsv"), g["detail_tsv"])


if __name__ == "__main__":
    unittest.main()
