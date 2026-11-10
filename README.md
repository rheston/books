# libbycheck

Cross-reference your Goodreads **to-read** shelf with what is actually on the shelves of your
Libby (OverDrive) libraries, in one repeatable command.

```
$ libbycheck check            # (abridged example)
Title                          Author            Status         Library  Format     Est. wait (days)  Holds  ...
-----------------------------  ----------------  -------------  -------  ---------  ----------------  -----
Braiding Sweetgrass: Indigen…  Robin Wall Kimm…  Available now  spl      ebook                        0
Piranesi                       Susanna Clarke    Wait list      nypl     ebook      16                67
Zxqv Plorn Handbook of Nonex…  Nobody Nowhere    Not found
```

There are two ways to run it, both from this repo:

| | Web app | Command line |
|---|---|---|
| Runs on | Any modern browser, **including your phone** | A computer with Python 3.11+ |
| Install | None. It is one file: `docs/index.html` | None (standard library only) |
| Goodreads input | CSV export, or a pasted list | CSV export or RSS feed |
| Best for | Checking from anywhere | Scheduling, scripting, saving a report on a timer |

Both use the same matching rules, kept identical by shared tests (see Development).

## Web app (works on your phone)

`docs/index.html` is the whole app: no server, no account, no build step to use it, no requests except to OverDrive.

**Put it on your phone.** The repo is public, so GitHub Pages can host it for free:

1. On GitHub: repo **Settings → Pages → Build and deployment**.
2. Source: **Deploy from a branch**. Branch: `main` (or whichever branch has `docs/`). Folder: **/docs**. Save.
3. After a minute it is live at `https://<your-username>.github.io/books/`. Open that on your phone, then use your browser's **Add to Home Screen** so it opens like an app.

The page contains no personal data: your book list and libraries are stored only in your own browser.
To use it on a computer without hosting, just open `docs/index.html` in a browser.

**Use it.**

1. **Your books.** Export from Goodreads (My Books, then Import and export, then Export Library) and open the CSV in the page. Or paste lines like `Piranesi by Susanna Clarke`. The page remembers them, so next time you only press Check.
2. **Your libraries.** Search by name and tap to add each library whose card you have in Libby. (It can also add by key.)
3. **Check availability.** Results show Available now, Wait list and Not found, with a filter, a sort, per-library detail, and **Copy for Google Sheets** (paste into cell A1) or **Download CSV**. When the check finishes, the page scrolls to the results and says what it found. The last results are saved in your browser with the time they were checked, so they are still there if the page reloads. Reloading only shows those saved results. Tap **Check availability** again for fresh numbers: every check asks the libraries again. Changing your books or libraries clears the saved results.

The web app does not read a Goodreads RSS feed. I could not test whether Goodreads lets a web page read its feed, and the usual workaround (a third-party proxy) would expose your private RSS key. The command-line tool still supports RSS.

## Command line

Use this for scheduling or scripting.

### Quick start

```bash
cp config.example.toml config.toml      # config.toml is git-ignored: it holds your private RSS key
# edit config.toml: your Goodreads source and your library keys (see below)
python -m libbycheck check              # or: pip install -e . && libbycheck check
```

### 1. Point it at your Goodreads shelf

Goodreads retired its public API, so there are two routes. Set one in `config.toml`:

| Route | How | Trade-off |
|---|---|---|
| **RSS** (`rss_url`) | My Books → *to-read* shelf → the RSS link at the bottom of the page | Fully automatable. Needs your profile/shelf reachable with that key. Reported to cap each page at ~100 books; libbycheck keeps requesting `page=N` until nothing new appears. |
| **CSV** (`csv`) | My Books → Import and export → *Export Library* | No cap, but you re-export by hand when the shelf changes. |

The RSS URL contains a private key; treat it like a password. libbycheck hides it in error messages.

### 2. Tell it which libraries to check

Libby doesn't expose your connected cards publicly, so list the OverDrive **library key** for each card:

```bash
libbycheck libraries search "king county"   # downloads the directory once (~50 s), then cached
libbycheck libraries info kcls              # verify a key
```

The key is also the subdomain of your library's OverDrive site (`nypl.overdrive.com` → `nypl`).
A typo'd key fails immediately instead of after hundreds of lookups.

## Command line output and flags

`libbycheck check` prints one row per book, best option first. Useful flags:

| Flag | Effect |
|---|---|
| `--format tsv` | Tab-separated; **paste straight into Google Sheets**. Also `csv`, `json`, `table` (default). |
| `--out report.tsv` | Also write a file (format from extension). |
| `--detail` | One row per library × format instead of one per book. |
| `--only-available` | Only books you could borrow right now. |
| `--sort title` | A–Z instead of available-first. |
| `--limit 20` | Trial run on the first 20 books. |
| `--refresh` | Ignore cached availability (otherwise cached 30 min). |
| `-l KEY` / `--csv F` / `--rss URL` | Override the config. |

**Status values**

| Status | Meaning |
|---|---|
| Available now | A copy is free and not spoken for by the holds queue. |
| Wait list | Owned, but you would join a queue. *Est. wait (days)* is OverDrive's estimate. |
| Not found | No verified match at any of your libraries. |
| Lookup failed | A library could not be reached, so "not found" would be a guess. Re-run. |

The **Match** column shows confidence: `title+author` (normal), or `title+author (subtitle differs | franchise prefix | fuzzy)` for weaker matches worth a glance, or `isbn`.

Exit code: `0` all good · `1` ran but some lookups failed (results still printed) or a runtime error · `2` config/usage error.

## How matching works

Catalog search is fuzzy: searching one novel also returns other books by the same author. So every hit is
verified, and a book only counts if **title and author** both match (or an ISBN does):

- Series brackets are ignored: `The Left Hand of Darkness (Hainish Cycle, #4)` = `The Left Hand of Darkness`.
- Leading articles, accents, punctuation, `Last, First` vs `First Last`, and initials (`J.R.R.`) are normalised.
- Descriptive subtitles don't change the book (`The Overstory` = `The Overstory: A Novel`), but series-style ones do
  (`Thrawn` ≠ `Thrawn: Alliances`).
- If a library has an exact match, weaker partial matches there are discarded so a sequel can't masquerade as the book.
- Ebook and audiobook are separate records, each with its own queue. Several editions of one book are all considered.
- Records with zero copies are ignored. By default only English-language ebook/audiobook records count.

An ISBN match counts as a strong match when one occurs, and the ISBN is searched as a last resort. But don't expect it
to do much: Goodreads usually stores the *print* edition's ISBN, while OverDrive lists the ebook's and audiobook's own
ISBNs (a print ISBN searched live returned nothing), so title + author is what finds most books.

## Caveats (please read)

- **Unofficial API.** Libby has no public API. This uses OverDrive's unauthenticated "Thunder" endpoints
  (`thunder.api.overdrive.com/v2`) that library websites use. They are undocumented and can change without notice.
  Checked live on 2026-11-07 against `nypl`, `kcls`, `spl` and the library directory. OverDrive's official
  [Library Availability API](https://developer.overdrive.com/api-docs/discovery-apis/library-availability) needs
  partner credentials.
- **The Goodreads readers are not verified against real Goodreads output.** The network where this was built could
  not reach goodreads.com, and RSS needs a private key. The CSV and RSS parsers follow Goodreads' documented export
  format and are covered by tests on hand-written samples, but a real feed may differ. They raise clear errors on
  unexpected input rather than silently returning nothing. **First run: use `--limit 10` and check the books listed.**
- **"Available now" is a judgment, not a field.** The API's `isAvailable` flag was sometimes true while holds
  outnumbered free copies (e.g. 3 free, 37 holds). libbycheck only says "Available now" when free copies exceed holds.
  This is inferred from API data and has not been checked against the Libby app.
- **Library links** (`https://<key>.overdrive.com/media/<id>`) follow OverDrive's usual URL pattern but were not
  opened during development.
- **Web app testing.** It was driven end to end in headless Chromium at iPhone 13 size, in light and dark, against the
  live OverDrive API: CSV upload, library search, add by key, a full run, filter, sort, copy (clipboard contents checked),
  CSV download, cancel, and reload persistence. It was **not** tested in real iOS Safari or Android Chrome, on GitHub
  Pages, or with Goodreads' export on a phone.
- **Library list is a snapshot.** The page embeds the 2,315 libraries that were live on 2026-11-07 (terminated, merged and
  preview accounts are left out). If yours is missing, add it by key, or refresh with
  `python tools/build_web.py --refresh-directory`.
- Not covered: Libby's "Lucky Day" collection (shown in Notes only), magazines, and Hoopla/other services.

## Running it on a schedule

It is a normal CLI, so cron works, e.g. refresh a sheet-ready file each morning:

```cron
0 7 * * *  cd ~/books && python -m libbycheck check --format tsv --out output/available.tsv --only-available --refresh
```

## Development

```bash
python -m unittest discover -s tests      # Python: CLI logic, plus a check that docs/index.html is up to date
node --test web/test/core.test.js         # JavaScript: the web app's logic
python tools/build_web.py                 # rebuild docs/index.html after editing web/src/*
```

`docs/index.html` is generated from `web/src/` (`index.html`, `core.js`, `app.js`) and `web/libraries.json`. Edit those,
then rebuild; a Python test fails if the built file is stale.

The Python and JavaScript implementations are held together by `tests/vectors.json`: shared matching, status and
ranking cases, plus a golden TSV that Python renders and JavaScript must reproduce byte for byte. Change a rule in one
place and both suites fail until the other follows.

The tests include a trimmed copy of a real OverDrive response (`tests/fixtures/`) and network-free fakes. Goodreads
samples in the tests are synthetic.

## Sources consulted

These informed the approach. Only search-result summaries were read, not the full pages, apart from live API checks.

- cloin, [*libbrary*](https://github.com/cloin/libbrary): Libby availability via the unauthenticated Thunder v2 API; warns it is unofficial.
- ping, [*odmpy*](https://github.com/ping/odmpy): a Thunder v2 client (`odmpy/overdrive.py`).
- Lydon-01, [*Goodreads Libby Availability Checker*](https://gist.github.com/Lydon-01/6b59667e4d343cad97c9ce796888a498): userscript with a title/author query fallback strategy.
- Jane Friedman, [*Goodreads Closing Off Access to Its Data, Making It Less Useful*](https://janefriedman.com/goodreads-closing-off-access-to-its-data-making-it-less-useful/): Goodreads API retirement.
- OverDrive, [*Library Availability API*](https://developer.overdrive.com/api-docs/discovery-apis/library-availability): the official, credentialed alternative.
