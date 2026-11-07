import json
import tempfile
import unittest

from libbycheck.overdrive import ApiError, Client, LibraryNotFound

from helpers import FIXTURES, ScriptedOpener, fixture_listings, http_error

SEARCH_BODY = (FIXTURES / "nypl_overstory_search.json").read_text()


def make(script, **kw):
    sleeps = []
    opener = ScriptedOpener(script)
    kw.setdefault("min_interval", 0)
    client = Client(opener=opener, sleep=sleeps.append, **kw)
    return client, opener, sleeps


class ParseTests(unittest.TestCase):
    def test_listing_parsed_from_real_response(self):
        by_type = {l.media_type: l for l in fixture_listings() if l.title == "The Overstory"}
        ebook, audio = by_type["ebook"], by_type["audiobook"]
        self.assertEqual(ebook.media_id, "3360345")
        self.assertEqual((ebook.owned_copies, ebook.available_copies, ebook.holds, ebook.est_wait_days), (16, 0, 77, 69))
        self.assertEqual(ebook.status, "wait")
        self.assertIn("9780393635539", ebook.isbns)
        self.assertIn("9781501982026", audio.isbns)
        self.assertEqual(ebook.languages, ["en"])
        self.assertEqual(ebook.creators, ["Richard Powers"])
        self.assertTrue(any(l.status == "available" for l in fixture_listings()))  # the Gold Bug ebook had a copy


class RetryTests(unittest.TestCase):
    def test_retries_transient_errors_then_succeeds(self):
        client, opener, sleeps = make([http_error(503), http_error(429, {"Retry-After": "7"}), SEARCH_BODY])
        listings = client.search("nypl", "the overstory powers")
        self.assertEqual(len(listings), 5)
        self.assertEqual(len(opener.urls), 3)
        self.assertEqual(sleeps, [2.0, 7.0])  # backoff, then Retry-After wins over the 4s backoff

    def test_network_error_retried(self):
        client, opener, _ = make([ConnectionResetError("reset"), SEARCH_BODY])
        self.assertEqual(len(client.search("nypl", "q")), 5)

    def test_gives_up_with_clear_error(self):
        client, opener, _ = make([http_error(500)] * 3, retries=2)
        with self.assertRaisesRegex(ApiError, r"HTTP 500.*gave up after 3 attempts"):
            client.search("nypl", "q")
        self.assertEqual(len(opener.urls), 3)

    def test_client_errors_not_retried(self):
        client, opener, _ = make([http_error(400)])
        with self.assertRaises(ApiError):
            client.search("nypl", "q")
        self.assertEqual(len(opener.urls), 1)

    def test_unknown_library_is_distinct_error(self):
        client, _, _ = make([http_error(404)])
        with self.assertRaises(LibraryNotFound):
            client.library_info("nope")

    def test_garbage_body_is_retried_then_reported(self):
        client, _, _ = make(["<html>maintenance</html>"] * 2, retries=1)
        with self.assertRaisesRegex(ApiError, "JSONDecodeError"):
            client.search("nypl", "q")


class CacheTests(unittest.TestCase):
    def test_second_call_served_from_cache_and_refresh_bypasses(self):
        d = tempfile.mkdtemp()
        client, opener, _ = make([SEARCH_BODY], cache_dir=d)
        first = client.search("nypl", "overstory")
        again = client.search("nypl", "overstory")
        self.assertEqual(len(opener.urls), 1)
        self.assertEqual([l.to_dict() for l in first], [l.to_dict() for l in again])

        client2, opener2, _ = make([SEARCH_BODY], cache_dir=d, refresh=True)
        client2.search("nypl", "overstory")
        self.assertEqual(len(opener2.urls), 1)

    def test_expired_cache_refetches(self):
        d = tempfile.mkdtemp()
        client, opener, _ = make([SEARCH_BODY, SEARCH_BODY], cache_dir=d, availability_ttl=-1)
        client.search("nypl", "overstory")
        client.search("nypl", "overstory")
        self.assertEqual(len(opener.urls), 2)

    def test_distinct_queries_and_libraries_do_not_collide(self):
        d = tempfile.mkdtemp()
        client, opener, _ = make([SEARCH_BODY] * 3, cache_dir=d)
        client.search("nypl", "a")
        client.search("nypl", "b")
        client.search("kcls", "a")
        self.assertEqual(len(opener.urls), 3)

    def test_corrupt_cache_file_is_a_miss(self):
        d = tempfile.mkdtemp()
        client, opener, _ = make([SEARCH_BODY, SEARCH_BODY], cache_dir=d)
        client.search("nypl", "overstory")
        for f in __import__("pathlib").Path(d).glob("*.json"):
            f.write_text("{not json")
        client.search("nypl", "overstory")
        self.assertEqual(len(opener.urls), 2)


class DirectoryTests(unittest.TestCase):
    def test_pages_until_total_reached(self):
        def page(keys, total):
            return json.dumps({"items": [{"preferredKey": k, "name": k.upper(), "status": "Live"} for k in keys], "totalItems": total})

        client, opener, _ = make([page(["a", "b"], 3), page(["c"], 3)])
        libs = client.directory()
        self.assertEqual([l["key"] for l in libs], ["a", "b", "c"])
        self.assertIn("page=2", opener.urls[1])


if __name__ == "__main__":
    unittest.main()
