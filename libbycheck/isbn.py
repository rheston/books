"""ISBN normalisation. Everything is compared as ISBN-13."""

from __future__ import annotations

import re


def _check13(digits: str) -> bool:
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
    return (10 - total % 10) % 10 == int(digits[12])


def _check10(chars: str) -> bool:
    total = sum((10 if c == "X" else int(c)) * (10 - i) for i, c in enumerate(chars))
    return total % 11 == 0


def to_isbn13(raw: str | None) -> str | None:
    """Return a valid ISBN-13 for an ISBN-10/13 in any common formatting, else None.

    Handles hyphens and the ``="0393635554"`` wrapper Goodreads uses in CSV exports.
    """
    if not raw:
        return None
    chars = re.sub(r"[^0-9Xx]", "", raw).upper()
    if len(chars) == 13 and chars.isdigit() and _check13(chars):
        return chars
    if len(chars) == 10 and chars[:9].isdigit() and _check10(chars):
        body = "978" + chars[:9]
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body))
        return body + str((10 - total % 10) % 10)
    return None
