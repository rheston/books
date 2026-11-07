from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from . import goodreads, report
from .models import BookResult
from .overdrive import ApiError, Client, LibraryNotFound
from .pipeline import check_books

DEFAULT_CONFIG = "config.toml"
FORMATS = ("table", "tsv", "csv", "json")


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _progress(label: str):
    interactive = sys.stderr.isatty()
    last = [0]

    def show(done: int, total: int) -> None:
        if interactive:
            print(f"\r{label} {done}/{total}", end="" if done < total else "\n", file=sys.stderr, flush=True)
        elif done == total or done - last[0] >= max(1, total // 10):
            last[0] = done
            _err(f"{label} {done}/{total}")

    return show


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="libbycheck",
        description="Cross-reference a Goodreads shelf with Libby/OverDrive library availability.",
    )
    p.add_argument("--config", help=f"TOML config file (default: ./{DEFAULT_CONFIG} if it exists)")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="check the shelf against your libraries")
    src = c.add_mutually_exclusive_group()
    src.add_argument("--rss", metavar="URL", help="Goodreads shelf RSS URL (overrides config)")
    src.add_argument("--csv", metavar="FILE", help="Goodreads library export CSV (overrides config)")
    c.add_argument("--library", "-l", action="append", metavar="KEY", help="OverDrive library key; repeatable (overrides config)")
    c.add_argument("--format", choices=FORMATS, default="table", help="stdout format (default: table; use tsv to paste into Google Sheets)")
    c.add_argument("--out", metavar="FILE", help="also write the report here; format from extension (.tsv/.csv/.json), else --format")
    c.add_argument("--detail", action="store_true", help="one row per library and format instead of one per book")
    c.add_argument("--only-available", action="store_true", help="only books you could borrow right now")
    c.add_argument("--sort", choices=("status", "title"), default="status",
                   help="status: available first, then shortest wait (default); title: A-Z")
    c.add_argument("--limit", type=int, metavar="N", help="check only the first N books (useful for a trial run)")
    c.add_argument("--refresh", action="store_true", help="ignore cached availability")

    lib = sub.add_parser("libraries", help="find or verify OverDrive library keys")
    lsub = lib.add_subparsers(dest="libcmd", required=True)
    s = lsub.add_parser("search", help="search library names (downloads the directory once, ~130 requests, then cached)")
    s.add_argument("text")
    s.add_argument("--include-inactive", action="store_true", help="include terminated/merged libraries")
    i = lsub.add_parser("info", help="verify a library key")
    i.add_argument("key")
    return p


def _client(settings: config_mod.Settings, refresh: bool = False) -> Client:
    return Client(
        cache_dir=settings.cache_dir,
        availability_ttl=settings.availability_ttl_minutes * 60,
        refresh=refresh,
    )


def cmd_libraries(args, settings) -> int:
    client = _client(settings)
    if args.libcmd == "info":
        try:
            info = client.library_info(args.key)
        except LibraryNotFound:
            _err(f"No library with key '{args.key}'. Try: libbycheck libraries search \"<name>\"")
            return 1
        cols = [info["key"], info["name"], info["status"]] + (["consortium"] if info["isConsortium"] else [])
        print("\t".join(cols))
        return 0
    needle = args.text.lower()
    libs = client.directory(progress=_progress("downloading library directory"))
    hits = [l for l in libs if needle in l["name"].lower() or needle in (l["key"] or "").lower()]
    if not args.include_inactive:
        hits = [l for l in hits if l["status"] == "Live"]
    if not hits:
        _err(f"No libraries matching '{args.text}'.")
        return 1
    print("key\tname\tconsortium")
    for l in sorted(hits, key=lambda l: l["name"].lower()):
        print(f"{l['key']}\t{l['name']}\t{'yes' if l['isConsortium'] else ''}")
    return 0


def cmd_check(args, settings) -> int:
    if args.rss:
        settings.rss_url, settings.csv = args.rss, ""
    if args.csv:
        settings.csv, settings.rss_url = args.csv, ""
    if args.library:
        settings.libraries = args.library
    if not (settings.rss_url or settings.csv):
        _err("No Goodreads source. Set [goodreads] rss_url or csv in config.toml, or pass --rss / --csv.")
        return 2
    if "YOUR_KEY" in settings.rss_url:
        _err("rss_url is still the placeholder from config.example.toml. Replace it with your own "
             "Goodreads RSS link, or delete it and set csv instead.")
        return 2
    if not settings.libraries:
        _err("No libraries. Set [libby] libraries in config.toml, or pass --library KEY.")
        return 2

    client = _client(settings, refresh=args.refresh)
    for key in settings.libraries:  # fail fast on typos instead of after hundreds of lookups
        try:
            client.library_info(key)
        except LibraryNotFound:
            _err(f"Unknown library key '{key}'. Find yours with: libbycheck libraries search \"<name>\"")
            return 2
        except ApiError as e:
            _err(f"Could not reach OverDrive to verify library '{key}': {e}")
            return 1

    try:
        books = (
            goodreads.load_rss(settings.rss_url, settings.shelf)
            if settings.rss_url
            else goodreads.load_csv(settings.csv, settings.shelf)
        )
    except goodreads.GoodreadsError as e:
        _err(f"Goodreads: {e}")
        return 1
    if not books:
        _err(f"No books found on shelf '{settings.shelf}'. Nothing to check.")
        return 1
    if args.limit:
        books = books[: args.limit]
    _err(f"Checking {len(books)} books against {len(settings.libraries)} librar{'y' if len(settings.libraries) == 1 else 'ies'}...")

    results: list[BookResult] = check_books(
        books, settings.libraries, client, settings.types, settings.languages,
        workers=settings.workers, progress=_progress("checked"),
    )

    if args.detail:
        rows, columns = report.detail_rows(results), report.DETAIL_COLUMNS
    else:
        rows, columns = report.summary_rows(results, settings.prefer, args.sort), report.SUMMARY_COLUMNS
        if args.only_available:
            rows = [r for r in rows if r[columns.index("Status")] == report.STATUS_LABEL["available"]]

    text = report.render(rows, columns, args.format)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")  # no stray blank line when piped
    if args.out:
        out = Path(args.out)
        fmt = {".tsv": "tsv", ".csv": "csv", ".json": "json"}.get(out.suffix.lower(), args.format)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.render(rows, columns, fmt), encoding="utf-8")
        _err(f"Wrote {len(rows)} rows to {out}")

    failed = sum(1 for r in results if r.errors)
    if failed:
        _err(f"WARNING: {failed} of {len(results)} books had at least one failed library lookup "
             "(marked 'Lookup failed' or noted in Notes). Re-run to retry; results may be incomplete.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = args.config or (DEFAULT_CONFIG if Path(DEFAULT_CONFIG).exists() else None)
        settings = config_mod.load(path)
    except config_mod.ConfigError as e:
        _err(f"Config error: {e}")
        return 2
    try:
        return cmd_libraries(args, settings) if args.command == "libraries" else cmd_check(args, settings)
    except ApiError as e:
        _err(f"OverDrive error: {e}")
        return 1
    except KeyboardInterrupt:
        _err("Interrupted.")
        return 130
