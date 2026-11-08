#!/usr/bin/env python3
"""Build the single-file web app: docs/index.html.

Inlines web/src/core.js, web/src/app.js and the library directory (web/libraries.json) into
web/src/index.html. The output has no external requests, so it works from any static host
(GitHub Pages serves /docs) or straight from a downloaded file.

    python tools/build_web.py                       # rebuild docs/index.html
    python tools/build_web.py --check               # exit 1 if docs/index.html is out of date
    python tools/build_web.py --refresh-directory   # re-download the library directory first (~50 s)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "src"
DIRECTORY = ROOT / "web" / "libraries.json"
OUT = ROOT / "docs" / "index.html"


def refresh_directory() -> None:
    sys.path.insert(0, str(ROOT))
    from libbycheck.overdrive import Client

    libs = Client(cache_dir=None).directory(progress=lambda d, t: print(f"\rdirectory {d}/{t}", end="", file=sys.stderr))
    print(file=sys.stderr)
    live = [l for l in libs if l["status"] == "Live" and l["key"]]  # Terminated/Merged/Preview/InDevelopment are not usable
    rows = sorted(([l["key"], l["name"].strip(), 1 if l["isConsortium"] else 0] for l in live), key=lambda r: r[1].lower())
    DIRECTORY.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {len(rows)} live libraries to {DIRECTORY.relative_to(ROOT)}", file=sys.stderr)


def _script_safe(text: str) -> str:
    # Nothing inside a <script> block may contain a closing tag or an HTML comment opener.
    return text.replace("</script", "<\\/script").replace("<!--", "<\\!--")


def render() -> str:
    html = (SRC / "index.html").read_text(encoding="utf-8")
    directory = json.dumps(json.loads(DIRECTORY.read_text(encoding="utf-8")), ensure_ascii=False, separators=(",", ":"))
    directory = directory.replace("<", "\\u003c")  # valid JSON escape, keeps the data inert inside <script>
    for marker, body in (
        ("/*__DIRECTORY__*/", directory),
        ("/*__CORE__*/", _script_safe((SRC / "core.js").read_text(encoding="utf-8"))),
        ("/*__APP__*/", _script_safe((SRC / "app.js").read_text(encoding="utf-8"))),
    ):
        if html.count(marker) != 1:
            raise SystemExit(f"template must contain {marker} exactly once")
        html = html.replace(marker, body)
    return html


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if docs/index.html is not up to date")
    ap.add_argument("--refresh-directory", action="store_true", help="re-download web/libraries.json first")
    args = ap.parse_args()
    if args.refresh_directory:
        refresh_directory()
    html = render()
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != html:
            print("docs/index.html is out of date. Run: python tools/build_web.py", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(html.encode()) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
