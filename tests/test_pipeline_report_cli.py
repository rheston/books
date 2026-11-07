import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from libbycheck import cli, config, report
from libbycheck.models import Book, BookResult, Listing, Offer
from libbycheck.overdrive import ApiError, LibraryNotFound
from libbycheck.pipeline import best_offer, check_books, find_offers, queries_for

from helpers import fixture_listings

OVERSTORY = Book("The Overstory", "Richard Powers", gr_id="1", isbns=frozenset({"9780393635539"}))


class FakeClient:
    """Stands in for overdrive.Client. `catalog` maps (library, query) -> listings."""

    def __init__(self, catalog=None, fail=()):
        self.catalog, self.fail, self.calls = catalog or {}, set(fail), []

    def search(self, library, query, per_page=24):
        self.calls.append((library, query))
        if library in self.fail:
            raise ApiError(f"HTTP 500 for {library}")
        return self.catalog.get((library, query), [])

    def library_info(self, key):
        if key == "bad":
            raise LibraryNotFound("404")
        return {"key": key}


def L(library="a", mtype="ebook", avail=False, holdable=True, wait=10, holds=None, owned=2, availc=0, title="The Overstory",
      lang=("en",), owned_flag=True, creators=("Richard Powers",), atype="normal", subtitle=""):
    if holds is None:  # consistent defaults: an available copy normally has no queue; a waitlisted one does
        holds = 0 if avail else 3
    return Listing(library=library, media_id="9", title=title, subtitle=subtitle, creators=list(creators), media_type=mtype,
                   languages=list(lang), is_owned=owned_flag, is_available=avail, is_holdable=holdable,
                   est_wait_days=wait, holds=holds, owned_copies=owned, available_copies=availc, availability_type=atype)


class QueryTests(unittest.TestCase):
    def test_order_and_dedupe(self):
        self.assertEqual(queries_for(OVERSTORY), ["The Overstory Powers", "The Overstory", "9780393635539"])
        self.assertEqual(queries_for(Book("Dune", "")), ["Dune"])


class FindOffersTests(unittest.TestCase):
    def test_stops_at_first_query_with_a_verified_match(self):
        c = FakeClient({("a", "The Overstory Powers"): fixture_listings("a")})
        offers = find_offers(c, "a", OVERSTORY, {"ebook", "audiobook"}, {"en"})
        self.assertEqual(sorted(o.listing.media_type for o in offers), ["audiobook", "ebook"])
        self.assertEqual(len(c.calls), 1)

    def test_falls_back_to_title_then_isbn(self):
        c = FakeClient({("a", "9780393635539"): [L()]})
        offers = find_offers(c, "a", OVERSTORY, {"ebook"}, {"en"})
        self.assertEqual(len(offers), 1)
        self.assertEqual([q for _, q in c.calls], ["The Overstory Powers", "The Overstory", "9780393635539"])

    def test_not_found_when_only_lookalikes_returned(self):
        c = FakeClient({("a", "The Overstory Powers"): [L(title="The Gold Bug Variations")]})
        self.assertEqual(find_offers(c, "a", OVERSTORY, {"ebook"}, {"en"}), [])

    def test_type_language_and_ownership_filters(self):
        listings = [L(mtype="audiobook"), L(lang=("es",)), L(owned_flag=False), L(mtype="ebook")]
        c = FakeClient({("a", "The Overstory Powers"): listings})
        offers = find_offers(c, "a", OVERSTORY, {"ebook"}, {"en"})
        self.assertEqual([(o.listing.media_type, o.listing.languages) for o in offers], [("ebook", ["en"])])
        # empty filters mean "anything"
        self.assertEqual(len(find_offers(c, "a", OVERSTORY, set(), set())), 3)  # unowned still excluded


class MatchTierTests(unittest.TestCase):
    ZAHN = ("Timothy Zahn",)

    def catalog(self):
        # Titles as seen live in the SPL/NYPL/KCLS catalogs for "Star Wars Thrawn Zahn".
        return [L(title="Thrawn: Alliances", creators=self.ZAHN, avail=True, availc=5, holds=0),
                L(title="Thrawn", creators=self.ZAHN, avail=False, availc=0, holds=9),
                L(title="Thrawn: Treason", creators=self.ZAHN, avail=True, availc=2, holds=0)]

    def test_exact_match_suppresses_partial_matches(self):
        # Goodreads "Thrawn (Star Wars: Thrawn, #1)": only the book titled "Thrawn" may be offered,
        # not the free sequels that merely share a prefix (that would be a false "Available now").
        book = Book("Thrawn (Star Wars: Thrawn, #1)", "Timothy Zahn")
        c = FakeClient({("a", "Thrawn Zahn"): self.catalog()})
        offers = find_offers(c, "a", book, {"ebook"}, {"en"})
        self.assertEqual([(o.listing.title, o.match) for o in offers], [("Thrawn", "exact")])

    def test_franchise_prefixed_goodreads_title_still_found(self):
        book = Book("Star Wars: Thrawn", "Timothy Zahn")
        c = FakeClient({("a", "Star Wars Zahn"): self.catalog(), ("a", "Star Wars"): self.catalog()})
        offers = find_offers(c, "a", book, {"ebook"}, {"en"})
        self.assertEqual([(o.listing.title, o.match) for o in offers], [("Thrawn", "tail")])

    def test_partial_match_used_when_nothing_better_exists(self):
        book = Book("Thrawn", "Timothy Zahn")
        c = FakeClient({("a", "Thrawn Zahn"): [L(title="Thrawn: Alliances", creators=self.ZAHN)]})
        offers = find_offers(c, "a", book, {"ebook"}, {"en"})
        self.assertEqual([o.match for o in offers], ["main-title"])  # labelled so the report shows lower confidence

    def test_later_query_can_find_a_strong_match_after_a_weak_first_query(self):
        book = Book("Thrawn", "Timothy Zahn")
        c = FakeClient({("a", "Thrawn Zahn"): [L(title="Thrawn: Alliances", creators=self.ZAHN)],
                        ("a", "Thrawn"): [L(title="Thrawn", creators=self.ZAHN)]})
        self.assertEqual([o.match for o in find_offers(c, "a", book, {"ebook"}, {"en"})], ["exact"])


class SubtitleRegressionTests(unittest.TestCase):
    """Regression: an exact audiobook match used to hide the ebook record titled "The Overstory: A Novel"."""

    def test_ebook_with_descriptive_subtitle_is_kept_alongside_exact_audiobook(self):
        c = FakeClient({("a", "The Overstory Powers"): [
            L(mtype="audiobook", title="The Overstory"),
            L(mtype="ebook", title="The Overstory", subtitle="A Novel", avail=True, availc=4)]})
        offers = find_offers(c, "a", OVERSTORY, {"ebook", "audiobook"}, {"en"})
        self.assertEqual(sorted((o.listing.media_type, o.match) for o in offers), [("audiobook", "exact"), ("ebook", "exact")])

    def test_radio_dramatisation_is_only_a_fallback(self):
        book = Book("The Left Hand of Darkness (Hainish Cycle, #4)", "Ursula K. Le Guin")
        guin = ("Ursula K. Le Guin",)
        drama = L(mtype="audiobook", title="The Left Hand of Darkness", subtitle="BBC Radio 4 full-cast dramatisation",
                  creators=guin, avail=True, availc=7, owned=14)
        novel = L(mtype="ebook", title="The Left Hand of Darkness", creators=guin, holds=48, owned=12)
        both = find_offers(FakeClient({("a", "The Left Hand of Darkness Guin"): [drama, novel]}), "a", book, {"ebook", "audiobook"}, {"en"})
        self.assertEqual([o.listing.media_type for o in both], ["ebook"])  # novel wins; the radio play is dropped
        only = find_offers(FakeClient({("a", "The Left Hand of Darkness Guin"): [drama]}), "a", book, {"ebook", "audiobook"}, {"en"})
        self.assertEqual([o.match for o in only], ["main-title"])  # still offered, but labelled as lower confidence


class StatusTests(unittest.TestCase):
    """Numbers below were observed live on 2026-11-07 (Thunder API) unless noted."""

    def test_free_copies_but_longer_queue_is_not_available(self):
        # SPL Project Hail Mary ebook: isAvailable true, 3 free, 37 holds, est. wait 2 days
        self.assertEqual(L(avail=True, availc=3, holds=37, owned=350, wait=2).status, "wait")

    def test_free_copies_beyond_the_queue_is_available(self):
        # SPL Fourth Wing audiobook: 71 free, 7 holds
        self.assertEqual(L(avail=True, availc=71, holds=7, owned=79).status, "available")
        # NYPL Tomorrow, and Tomorrow, and Tomorrow audiobook: 1 free, 1 hold -> nothing spare
        self.assertEqual(L(avail=True, availc=1, holds=1, owned=162).status, "wait")

    def test_unlimited_access_has_no_counts_but_is_available(self):
        # Seen live: availabilityType "always" records omit ownedCopies/availableCopies entirely
        l = Listing.from_api({"id": 1, "title": "Tom Swift and His Motor Boat", "isOwned": True, "isAvailable": True,
                              "isHoldable": True, "holdsCount": 0, "availabilityType": "always", "type": {"id": "ebook"}}, "spl")
        self.assertEqual((l.owned_copies, l.available_copies), (None, None))
        self.assertTrue(l.in_collection)
        self.assertEqual(l.status, "available")

    def test_missing_counts_are_not_read_as_zero(self):
        self.assertEqual(L(avail=True, availc=None, owned=None, holds=0).status, "available")
        self.assertEqual(L(avail=True, availc=None, owned=None, holds=4).status, "wait")

    def test_zero_copy_stub_is_not_in_collection(self):
        # NYPL Fourth Wing ebook stub: isOwned true, isHoldable true, ownedCopies 0, no wait estimate
        self.assertFalse(L(owned=0, avail=False, wait=None, holds=0).in_collection)
        self.assertTrue(L(owned=1).in_collection)
        self.assertFalse(L(owned_flag=False).in_collection)

    def test_not_available_and_not_holdable(self):
        self.assertEqual(L(avail=False, holdable=False).status, "unavailable")
        self.assertEqual(L(avail=False, holdable=True).status, "wait")

    def test_stub_record_is_filtered_out_of_offers(self):
        c = FakeClient({("a", "The Overstory Powers"): [L(owned=0, wait=None, holds=0), L(owned=71, holds=268, wait=54)]})
        offers = find_offers(c, "a", OVERSTORY, {"ebook"}, {"en"})
        self.assertEqual([o.listing.owned_copies for o in offers], [71])


class BestOfferTests(unittest.TestCase):
    def test_available_beats_wait_then_shortest_wait_then_format(self):
        wait_short = Offer(L(library="a", wait=5), "exact")
        wait_long = Offer(L(library="b", wait=60), "exact")
        avail_audio = Offer(L(library="c", mtype="audiobook", avail=True, availc=1, wait=14), "exact")
        avail_ebook = Offer(L(library="d", mtype="ebook", avail=True, availc=1, wait=14), "exact")
        prefer = ["ebook", "audiobook"]
        self.assertIs(best_offer([wait_short, wait_long], prefer), wait_short)
        self.assertIs(best_offer([wait_short, avail_audio], prefer), avail_audio)
        self.assertIs(best_offer([avail_audio, avail_ebook], prefer), avail_ebook)
        self.assertIs(best_offer([avail_audio, avail_ebook], ["audiobook", "ebook"]), avail_audio)
        self.assertIsNone(best_offer([], prefer))

    def test_unknown_wait_sorts_last(self):
        known = Offer(L(library="a", wait=90), "exact")
        unknown = Offer(L(library="b", wait=None), "exact")
        self.assertIs(best_offer([unknown, known], ["ebook"]), known)


class CheckBooksTests(unittest.TestCase):
    def test_order_preserved_and_failures_recorded_not_raised(self):
        b2 = Book("Piranesi", "Susanna Clarke")
        c = FakeClient({("good", "The Overstory Powers"): [L(library="good")]}, fail={"flaky"})
        results = check_books([OVERSTORY, b2], ["good", "flaky"], c, {"ebook"}, {"en"}, workers=2)
        self.assertEqual([r.book.title for r in results], ["The Overstory", "Piranesi"])
        self.assertEqual(len(results[0].offers), 1)
        self.assertEqual(len(results[0].errors), 1)
        self.assertIn("flaky", results[0].errors[0])
        self.assertEqual(len(results[1].errors), 1)


class ReportTests(unittest.TestCase):
    def results(self):
        avail = BookResult(Book("Avail", "A", gr_id="1"), [Offer(L(avail=True, availc=1, holds=0), "exact")])
        wait = BookResult(Book("Waiting", "B"), [Offer(L(wait=30, holds=12), "isbn")])
        none = BookResult(Book("Nowhere", "C"))
        err = BookResult(Book("Flaky", "D"), errors=["kcls: HTTP 500"])
        return [none, err, wait, avail]  # deliberately unsorted

    def test_status_sort_and_error_is_not_reported_as_not_found(self):
        rows = report.summary_rows(self.results(), ["ebook"])
        status = [(r[0], r[2]) for r in rows]
        self.assertEqual(status, [("Avail", "Available now"), ("Waiting", "Wait list"), ("Flaky", "Lookup failed"), ("Nowhere", "Not found")])
        self.assertEqual(rows[1][5], 30)  # wait days shown for waitlisted
        self.assertEqual(rows[0][5], "")  # but not for available

    def test_title_sort(self):
        rows = report.summary_rows(self.results(), ["ebook"], sort="title")
        self.assertEqual([r[0] for r in rows], ["Avail", "Flaky", "Nowhere", "Waiting"])

    def test_rows_match_header_width_in_every_format(self):
        rows = report.summary_rows(self.results(), ["ebook"])
        self.assertTrue(all(len(r) == len(report.SUMMARY_COLUMNS) for r in rows))
        drows = report.detail_rows(self.results())
        self.assertTrue(all(len(r) == len(report.DETAIL_COLUMNS) for r in drows))
        self.assertEqual(len(drows), 2)  # only books with offers

    def test_reserved_copies_note_and_match_label(self):
        r = BookResult(Book("Project Hail Mary", "Andy Weir"), [Offer(L(avail=True, availc=3, holds=37, owned=350, wait=2), "tail")])
        row = report.summary_rows([r], ["ebook"])[0]
        cols = report.SUMMARY_COLUMNS
        self.assertEqual(row[cols.index("Status")], "Wait list")
        self.assertEqual(row[cols.index("Est. wait (days)")], 2)
        self.assertIn("likely reserved for the queue", row[cols.index("Notes")])
        self.assertEqual(row[cols.index("Match")], "title+author (franchise prefix)")

    def test_unlimited_title_shows_blank_counts_not_zero(self):
        l = L(avail=True, atype="always", availc=None, owned=None, holds=0, wait=None)
        row = report.summary_rows([BookResult(Book("Tom Swift", "X"), [Offer(l, "exact")])], ["ebook"])[0]
        cols = report.SUMMARY_COLUMNS
        self.assertEqual((row[cols.index("Copies available")], row[cols.index("Copies owned")]), ("", ""))
        self.assertIn("unlimited access", row[cols.index("Notes")])

    def test_tsv_is_sheets_pasteable(self):
        r = BookResult(Book("Has\ttab\nand newline", "A"), [Offer(L(), "exact")])
        out = report.render(report.summary_rows([r], ["ebook"]), report.SUMMARY_COLUMNS, "tsv")
        lines = out.strip("\n").split("\n")
        self.assertEqual(len(lines), 2)  # header + exactly one data row
        self.assertEqual({len(l.split("\t")) for l in lines}, {len(report.SUMMARY_COLUMNS)})

    def test_json_and_table_render(self):
        rows = report.summary_rows(self.results(), ["ebook"])
        self.assertIn('"Status": "Available now"', report.render(rows, report.SUMMARY_COLUMNS, "json"))
        table = report.render(rows, report.SUMMARY_COLUMNS, "table")
        self.assertIn("Available now", table)
        self.assertNotIn("goodreads.com", table)  # long link columns are TSV/CSV/JSON only


class ConfigTests(unittest.TestCase):
    def write(self, text):
        p = Path(tempfile.mkdtemp()) / "config.toml"
        p.write_text(text)
        return p

    def test_loads_and_has_defaults(self):
        s = config.load(self.write('[goodreads]\ncsv="x.csv"\n[libby]\nlibraries=["nypl"]\n'))
        self.assertEqual((s.csv, s.libraries, s.shelf, s.types), ("x.csv", ["nypl"], "to-read", ["ebook", "audiobook"]))

    def test_typos_are_errors(self):
        with self.assertRaisesRegex(config.ConfigError, "unknown key"):
            config.load(self.write('[libby]\nlibrarys=["nypl"]\n'))
        with self.assertRaisesRegex(config.ConfigError, "unknown section"):
            config.load(self.write("[libbby]\n"))

    def test_example_config_is_valid(self):
        s = config.load(Path(__file__).parent.parent / "config.example.toml")
        self.assertTrue(s.rss_url and s.libraries)

    def test_missing_explicit_file(self):
        with self.assertRaises(config.ConfigError):
            config.load("/no/such/config.toml")


class CliTests(unittest.TestCase):
    def run_cli(self, argv, client=None):
        out, err = io.StringIO(), io.StringIO()
        patcher = mock.patch.object(cli, "_client", return_value=client) if client else mock.MagicMock()
        with patcher, redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def csv_file(self):
        p = Path(tempfile.mkdtemp()) / "e.csv"
        p.write_text("Book Id,Title,Author,ISBN,ISBN13,Exclusive Shelf,Bookshelves\n"
                     '1,The Overstory,Richard Powers,,,to-read,to-read\n2,Piranesi,Susanna Clarke,,,to-read,to-read\n')
        return str(p)

    def test_no_source_or_libraries_is_usage_error(self):
        self.assertEqual(self.run_cli(["check"])[0], 2)
        self.assertEqual(self.run_cli(["check", "--csv", self.csv_file()])[0], 2)

    def test_unknown_library_key_fails_fast(self):
        code, _, err = self.run_cli(["check", "--csv", self.csv_file(), "-l", "bad"], FakeClient())
        self.assertEqual(code, 2)
        self.assertIn("Unknown library key 'bad'", err)

    def test_end_to_end_tsv_and_out_file(self):
        client = FakeClient({("a", "The Overstory Powers"): [L(avail=True, availc=1, holds=0)]})
        out_file = Path(tempfile.mkdtemp()) / "sub" / "report.tsv"
        code, out, err = self.run_cli(["check", "--csv", self.csv_file(), "-l", "a", "--format", "tsv", "--out", str(out_file)], client)
        self.assertEqual(code, 0, err)
        self.assertIn("The Overstory\tRichard Powers\tAvailable now\ta\tebook", out)
        self.assertIn("Piranesi\tSusanna Clarke\tNot found", out)
        self.assertEqual(out_file.read_text(), out)

    def test_only_available_filter(self):
        client = FakeClient({("a", "The Overstory Powers"): [L(avail=True, availc=1)]})
        code, out, _ = self.run_cli(["check", "--csv", self.csv_file(), "-l", "a", "--format", "tsv", "--only-available"], client)
        self.assertEqual(code, 0)
        self.assertIn("The Overstory", out)
        self.assertNotIn("Piranesi", out)

    def test_partial_failure_still_prints_results_but_exits_nonzero(self):
        client = FakeClient({("a", "The Overstory Powers"): [L(library="a")]}, fail={"b"})
        code, out, err = self.run_cli(["check", "--csv", self.csv_file(), "-l", "a", "-l", "b", "--format", "tsv"], client)
        self.assertEqual(code, 1)
        self.assertIn("Wait list", out)
        self.assertIn("Lookup failed", out)  # Piranesi: nothing found and library b errored
        self.assertIn("WARNING", err)

    def test_limit(self):
        client = FakeClient()
        code, out, _ = self.run_cli(["check", "--csv", self.csv_file(), "-l", "a", "--limit", "1", "--format", "tsv"], client)
        self.assertEqual(code, 0)
        self.assertNotIn("Piranesi", out)


if __name__ == "__main__":
    unittest.main()
