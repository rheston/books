import json
import urllib.error
from pathlib import Path

from libbycheck.models import Listing

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_listings(library: str = "nypl") -> list[Listing]:
    """Listings parsed from a REAL Thunder API response (see the fixture's _note)."""
    raw = json.loads((FIXTURES / "nypl_overstory_search.json").read_text())
    return [Listing.from_api(item, library) for item in raw["items"]]


class FakeResponse:
    def __init__(self, body: str | bytes):
        self._body = body.encode() if isinstance(body, str) else body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    import email.message

    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("https://example.invalid/x", code, "err", msg, None)


class ScriptedOpener:
    """urlopen stand-in that plays back a list of responses/exceptions and records URLs."""

    def __init__(self, script):
        self.script = list(script)
        self.urls: list[str] = []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return FakeResponse(item)
