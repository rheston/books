import unittest

from libbycheck.isbn import to_isbn13
from libbycheck.match import author_key, authors_match, main_title, match, title_match
from libbycheck.models import Book, Listing

from helpers import fixture_listings


def listing(title, creators, subtitle="", isbns=()):
    return Listing(library="x", media_id="1", title=title, subtitle=subtitle, creators=list(creators), isbns=list(isbns))


class IsbnTests(unittest.TestCase):
    def test_isbn10_converts_to_isbn13(self):
        self.assertEqual(to_isbn13("0306406152"), "9780306406157")

    def test_formatting_and_goodreads_csv_wrapper(self):
        self.assertEqual(to_isbn13('="978-0-393-63553-9"'), "9780393635539")

    def test_bad_checksum_and_junk_rejected(self):
        self.assertEqual(to_isbn13("9780393635522"), "9780393635522")  # valid checksum: accepted as-is
        self.assertIsNone(to_isbn13("9780393635523"))  # last digit changed: bad checksum
        self.assertIsNone(to_isbn13("not an isbn"))
        self.assertIsNone(to_isbn13(""))
        self.assertIsNone(to_isbn13(None))


class TitleTests(unittest.TestCase):
    def test_series_bracket_and_article_ignored(self):
        self.assertEqual(title_match("The Left Hand of Darkness (Hainish Cycle, #4)", "Left Hand of Darkness"), "exact")

    def test_subtitle_in_either_place(self):
        gr = "Braiding Sweetgrass: Indigenous Wisdom, Scientific Knowledge and the Teachings of Plants"
        sub = "Indigenous Wisdom, Scientific Knowledge and the Teachings of Plants"
        self.assertEqual(title_match(gr, "Braiding Sweetgrass", sub), "exact")
        self.assertEqual(title_match(gr, "Braiding Sweetgrass"), "exact")  # long Goodreads subtitle, catalog omits it
        self.assertEqual(title_match("Braiding Sweetgrass", gr), "main-title")  # but not the other way round

    def test_subtitle_already_in_catalog_title_not_doubled(self):
        self.assertEqual(title_match("Dune: Deluxe Edition", "Dune: Deluxe Edition", "Deluxe Edition"), "exact")

    def test_different_subtitles_do_not_match_on_main_title(self):
        self.assertIsNone(title_match("Star Wars: Thrawn", "Star Wars", "Thrawn Alliances"))

    def test_descriptive_subtitles_do_not_change_the_book(self):
        # Real NYPL record: title "The Overstory", subtitle "A Novel"
        self.assertEqual(title_match("The Overstory", "The Overstory", "A Novel"), "exact")
        self.assertEqual(title_match("The Overstory: A Novel", "The Overstory"), "exact")
        self.assertEqual(title_match("Dune", "Dune", "50th Anniversary Edition"), "exact")
        self.assertEqual(title_match("Emma", "Emma", "Unabridged"), "exact")

    def test_series_continuation_subtitles_do_change_the_book(self):
        self.assertEqual(title_match("Thrawn", "Thrawn: Treason"), "main-title")
        self.assertEqual(title_match("Thrawn: Treason", "Thrawn"), "main-title")
        # A real NYPL record: a radio play, not the novel
        self.assertEqual(title_match("The Left Hand of Darkness", "The Left Hand of Darkness",
                                     "BBC Radio 4 full-cast dramatisation"), "main-title")

    def test_franchise_prefix_on_one_side_only(self):
        # Real catalog titles seen live: "Thrawn", "Thrawn: Treason", "Thrawn: Alliances" (all Timothy Zahn)
        self.assertEqual(title_match("Star Wars: Thrawn", "Thrawn"), "tail")
        self.assertEqual(title_match("Thrawn", "Star Wars: Thrawn"), "tail")
        self.assertIsNone(title_match("Star Wars: Thrawn", "Thrawn: Alliances"))
        self.assertIsNone(title_match("Star Wars: Thrawn", "Star Wars", "Thrawn Ascendancy (Book III: Lesser Evil)"))

    def test_generic_subtitle_is_not_a_title(self):
        self.assertIsNone(title_match("Foo: A Novel", "A Novel"))

    def test_plain_title_is_ambiguous_with_series_subtitle_titles(self):
        # Documented weakness the pipeline compensates for by preferring exact matches.
        self.assertEqual(title_match("Thrawn", "Thrawn: Alliances"), "main-title")

    def test_unrelated_titles(self):
        self.assertIsNone(title_match("The Overstory", "The End of the Ocean"))

    def test_main_title_for_queries(self):
        self.assertEqual(main_title("Piranesi (Hardcover)"), "Piranesi")
        self.assertEqual(main_title("Braiding Sweetgrass: Indigenous Wisdom"), "Braiding Sweetgrass")


class QuerySurnameTests(unittest.TestCase):
    def test_keeps_case_and_handles_orderings(self):
        from libbycheck.match import query_surname

        self.assertEqual(query_surname("Richard Powers"), "Powers")
        self.assertEqual(query_surname("Powers, Richard"), "Powers")
        self.assertEqual(query_surname("Martin Luther King Jr."), "King")
        self.assertEqual(query_surname("Gabriel García Márquez"), "Márquez")
        self.assertEqual(query_surname(""), "")


class AuthorTests(unittest.TestCase):
    def test_name_orderings(self):
        self.assertEqual(author_key("Powers, Richard"), author_key("Richard Powers"))
        self.assertEqual(author_key("J.R.R. Tolkien"), author_key("J. R. R. Tolkien"))
        self.assertEqual(author_key("Gabriel García Márquez"), author_key("Gabriel Garcia Marquez"))
        self.assertEqual(author_key("Martin Luther King Jr."), author_key("Martin Luther King"))

    def test_match_and_mismatch(self):
        self.assertTrue(authors_match(("Richard Powers",), ["Richard Powers", "Someone Narrator"]))
        self.assertTrue(authors_match(("Anonymous",), ["Anonymous"]))
        self.assertTrue(authors_match(("X Y", "Ursula K. Le Guin"), ["Le Guin, Ursula K."]))  # any author
        self.assertFalse(authors_match(("Stephen King",), ["Joe King"]))
        self.assertFalse(authors_match(("Richard Powers",), ["Maja Lunde"]))
        self.assertFalse(authors_match((), ["Maja Lunde"]))


class MatchTests(unittest.TestCase):
    def test_isbn_beats_title(self):
        book = Book("Totally Different Title", "Nobody", isbns=frozenset({"9780393635539"}))
        self.assertEqual(match(book, listing("The Overstory", ["Richard Powers"], isbns=["9780393635539"])), "isbn")

    def test_title_and_author_required(self):
        book = Book("The Overstory", "Richard Powers")
        self.assertEqual(match(book, listing("The Overstory", ["Richard Powers"])), "exact")
        self.assertIsNone(match(book, listing("The Overstory", ["Some Other Writer"])))  # same title, wrong author
        self.assertIsNone(match(book, listing("The Gold Bug Variations", ["Richard Powers"])))  # same author, wrong book

    def test_real_catalog_response_only_true_editions_match(self):
        """The real search for 'the overstory powers' also returned an unrelated novel by another
        author and other Richard Powers books. Only the ebook and audiobook of The Overstory should match."""
        book = Book("The Overstory", "Richard Powers")
        matched = [(l.media_type, l.title, match(book, l)) for l in fixture_listings() if match(book, l)]
        # The ebook record carries the subtitle "A Novel"; it must still be an exact match.
        self.assertEqual(sorted(matched), [("audiobook", "The Overstory", "exact"), ("ebook", "The Overstory", "exact")])


if __name__ == "__main__":
    unittest.main()
