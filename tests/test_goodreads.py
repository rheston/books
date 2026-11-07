import tempfile
import unittest
from pathlib import Path

from libbycheck import goodreads

from helpers import ScriptedOpener, http_error

# SYNTHETIC samples written from Goodreads' documented export format, not captured from a real account.
CSV_TEXT = (
    "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,Average Rating,Publisher,Binding,"
    "Number of Pages,Year Published,Original Publication Year,Date Read,Date Added,Bookshelves,"
    "Bookshelves with positions,Exclusive Shelf,My Review\n"
    '1,"The Overstory","Richard Powers","Powers, Richard","","=""0393635554""","=""9780393635539""",0,4.1,Norton,Paperback,'
    "502,2019,2018,,2024/03/01,to-read,to-read (#1),to-read,\n"
    '2,"Piranesi (Hardcover)","Susanna Clarke","Clarke, Susanna","","","",0,4.2,Bloomsbury,Hardcover,'
    "272,2020,2020,,2024/03/02,\"favorites, to-read\",,currently-reading,\n"
    '3,"Already Read","Someone Else","Else, Someone","A. Translator, B. Illustrator","=""""","=""""",5,3.0,X,Hardcover,'
    "100,2001,2001,2024/01/01,2023/01/01,,,read,\n"
)

RSS_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Bookshelf</title>{items}</channel></rss>"""
RSS_ITEM = (
    "<item><title><![CDATA[{title}]]></title><book_id>{id}</book_id><author_name>{author}</author_name>"
    "<isbn>{isbn}</isbn><user_date_added>Sat, 17 Oct 2020 01:22:34 -0700</user_date_added></item>"
)


def rss(*books):
    return RSS_PAGE.format(items="".join(RSS_ITEM.format(title=t, id=i, author=a, isbn=n) for t, i, a, n in books))


class CsvTests(unittest.TestCase):
    def write(self, text, bom=True):
        d = tempfile.mkdtemp()
        p = Path(d) / "export.csv"
        p.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
        return p

    def test_filters_to_shelf_and_parses_fields(self):
        books = goodreads.load_csv(self.write(CSV_TEXT), "to-read")
        self.assertEqual([b.title for b in books], ["The Overstory", "Piranesi (Hardcover)"])  # 2nd via Bookshelves
        b = books[0]
        self.assertEqual((b.author, b.gr_id), ("Richard Powers", "1"))
        # ISBN-10 is converted to ISBN-13; it is a different (print) edition from the ...539 one, so both are kept.
        self.assertEqual(b.isbns, frozenset({"9780393635539", "9780393635553"}))
        self.assertEqual(b.url, "https://www.goodreads.com/book/show/1")
        self.assertEqual(books[1].isbns, frozenset())

    def test_additional_authors_kept(self):
        books = goodreads.load_csv(self.write(CSV_TEXT), "read")
        self.assertEqual(books[0].authors, ("Someone Else", "A. Translator", "B. Illustrator"))

    def test_works_without_bom(self):
        self.assertEqual(len(goodreads.load_csv(self.write(CSV_TEXT, bom=False), "to-read")), 2)

    def test_wrong_file_is_an_error_not_an_empty_list(self):
        with self.assertRaisesRegex(goodreads.GoodreadsError, "missing expected column"):
            goodreads.load_csv(self.write("name,thing\na,b\n"), "to-read")

    def test_missing_file(self):
        with self.assertRaises(goodreads.GoodreadsError):
            goodreads.load_csv("/nonexistent/file.csv")


class RssTests(unittest.TestCase):
    def test_parse(self):
        books = goodreads.parse_rss(rss(("The Overstory", "1", "Richard Powers", "0393635554")))
        self.assertEqual(len(books), 1)
        self.assertEqual((books[0].title, books[0].author, books[0].gr_id), ("The Overstory", "Richard Powers", "1"))
        self.assertEqual(books[0].isbns, frozenset({"9780393635553"}))  # ISBN-10 0393635554 -> ISBN-13

    def test_empty_isbn_is_fine(self):
        self.assertEqual(goodreads.parse_rss(rss(("Piranesi", "2", "Susanna Clarke", "")))[0].isbns, frozenset())

    def test_html_login_page_is_a_clear_error(self):
        with self.assertRaisesRegex(goodreads.GoodreadsError, "not an RSS feed|not XML"):
            goodreads.parse_rss("<html><body>Sign in</body></html>x<")
        with self.assertRaisesRegex(goodreads.GoodreadsError, "unexpected feed format"):
            goodreads.parse_rss("<html><body>Sign in</body></html>")

    def test_paginates_until_no_new_books(self):
        p1 = rss(("A", "1", "X", ""), ("B", "2", "X", ""))
        p2 = rss(("C", "3", "X", ""))
        opener = ScriptedOpener([p1, p2, p2])
        books = goodreads.load_rss("https://www.goodreads.com/review/list_rss/9?key=SECRET", opener=opener)
        self.assertEqual([b.title for b in books], ["A", "B", "C"])
        self.assertEqual(len(opener.urls), 3)
        self.assertIn("page=1", opener.urls[0])
        self.assertIn("page=3", opener.urls[2])
        self.assertIn("shelf=to-read", opener.urls[0])

    def test_feed_that_ignores_page_does_not_loop(self):
        same = rss(("A", "1", "X", ""))
        opener = ScriptedOpener([same, same])
        self.assertEqual(len(goodreads.load_rss("https://www.goodreads.com/x?key=K", opener=opener)), 1)
        self.assertEqual(len(opener.urls), 2)

    def test_user_supplied_shelf_param_is_respected(self):
        opener = ScriptedOpener([rss(("A", "1", "X", "")), rss()])
        goodreads.load_rss("https://www.goodreads.com/x?key=K&shelf=custom", shelf="to-read", opener=opener)
        self.assertIn("shelf=custom", opener.urls[0])

    def test_http_error_hides_the_private_key(self):
        opener = ScriptedOpener([http_error(403)])
        with self.assertRaises(goodreads.GoodreadsError) as cm:
            goodreads.load_rss("https://www.goodreads.com/review/list_rss/9?key=SUPERSECRET", opener=opener)
        self.assertNotIn("SUPERSECRET", str(cm.exception))
        self.assertIn("403", str(cm.exception))

    def test_runaway_feed_is_an_error(self):
        pages = [rss((f"T{i}", str(i), "X", "")) for i in range(5)]
        with self.assertRaisesRegex(goodreads.GoodreadsError, "stopped after 3 pages"):
            goodreads.load_rss("https://www.goodreads.com/x?key=K", max_pages=3, opener=ScriptedOpener(pages))


if __name__ == "__main__":
    unittest.main()
