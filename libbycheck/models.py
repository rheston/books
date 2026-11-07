from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .isbn import to_isbn13


@dataclass(frozen=True)
class Book:
    """A book on the Goodreads shelf."""

    title: str
    author: str
    gr_id: str = ""
    isbns: frozenset[str] = frozenset()  # normalised ISBN-13s
    additional_authors: tuple[str, ...] = ()
    date_added: str = ""

    @property
    def authors(self) -> tuple[str, ...]:
        return tuple(a for a in (self.author, *self.additional_authors) if a)

    @property
    def url(self) -> str:
        return f"https://www.goodreads.com/book/show/{self.gr_id}" if self.gr_id else ""


@dataclass
class Listing:
    """One catalog record (a single format of a title) at one library.

    Field names mirror what the Thunder API returns, trimmed to what we use so the
    on-disk cache stays small. JSON-serialisable on purpose.
    """

    library: str
    media_id: str
    title: str
    subtitle: str = ""
    creators: list[str] = field(default_factory=list)
    media_type: str = ""  # "ebook" | "audiobook" | ...
    languages: list[str] = field(default_factory=list)
    isbns: list[str] = field(default_factory=list)  # normalised ISBN-13s
    is_owned: bool = True
    is_available: bool = False
    is_holdable: bool = False
    owned_copies: int | None = None  # None = not reported (e.g. unlimited-access titles)
    available_copies: int | None = None
    holds: int = 0
    est_wait_days: int | None = None
    lucky_day_available: int = 0
    pre_release: bool = False
    availability_type: str = ""

    @classmethod
    def from_api(cls, raw: dict, library: str) -> "Listing":
        creators = [c["name"] for c in raw.get("creators") or [] if c.get("name")]
        if not creators and raw.get("firstCreatorName"):
            creators = [raw["firstCreatorName"]]
        isbns: set[str] = set()
        for fmt in raw.get("formats") or []:
            for ident in fmt.get("identifiers") or []:
                if ident.get("type") in ("ISBN", "LibraryISBN"):
                    isbn = to_isbn13(ident.get("value"))
                    if isbn:
                        isbns.add(isbn)
        return cls(
            library=library,
            media_id=str(raw["id"]),
            title=raw.get("title") or "",
            subtitle=raw.get("subtitle") or "",
            creators=creators,
            media_type=(raw.get("type") or {}).get("id", ""),
            languages=[l["id"] for l in raw.get("languages") or [] if l.get("id")],
            isbns=sorted(isbns),
            is_owned=raw.get("isOwned") is not False,
            is_available=bool(raw.get("isAvailable")),
            is_holdable=bool(raw.get("isHoldable")),
            owned_copies=raw.get("ownedCopies"),
            available_copies=raw.get("availableCopies"),
            holds=raw.get("holdsCount") or 0,
            est_wait_days=raw.get("estimatedWaitDays"),
            lucky_day_available=raw.get("luckyDayAvailableCopies") or 0,
            pre_release=bool(raw.get("isPreReleaseTitle")),
            availability_type=raw.get("availabilityType") or "",
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Listing":
        return cls(**d)

    @property
    def unlimited(self) -> bool:
        """Unlimited-access titles (availabilityType "always") report no copy counts."""
        return self.availability_type == "always"

    @property
    def in_collection(self) -> bool:
        """False for records the library does not actually hold: not owned, or an explicit
        zero copies (seen live: a stub ebook record with isOwned true but ownedCopies 0)."""
        return self.is_owned and (self.unlimited or self.owned_copies != 0)

    @property
    def status(self) -> str:
        """"available", "wait" or "unavailable".

        The API's isAvailable flag alone is not trusted: live data showed records with
        isAvailable true but more holds than free copies (e.g. 3 free, 37 holds, wait 2 days),
        where the free copies are presumably earmarked for the queue. So "available" needs
        more free copies than holds. This is an inference from API data, not checked
        against the Libby app.
        """
        if self.is_available:
            if self.unlimited:
                return "available"
            if self.available_copies is None:
                return "available" if self.holds == 0 else "wait"
            return "available" if self.available_copies > self.holds else "wait"
        return "wait" if self.is_holdable else "unavailable"

    @property
    def copies_reserved_for_holds(self) -> bool:
        return self.is_available and self.status == "wait"

    @property
    def url(self) -> str:
        # Not verified from the build sandbox (overdrive.com was not reachable there).
        return f"https://{self.library}.overdrive.com/media/{self.media_id}"


@dataclass
class Offer:
    """A listing that was matched to a Goodreads book."""

    listing: Listing
    match: str  # "isbn" | "exact" | "main-title" | "tail" | "fuzzy"  (see match.py)


@dataclass
class BookResult:
    book: Book
    offers: list[Offer] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # one entry per failed library lookup
