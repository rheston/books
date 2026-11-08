/* libbycheck core: a JavaScript port of the Python package's matching and lookup logic.
 *
 * No DOM code lives here, so Node can test it (web/test/core.test.js) and the build inlines it
 * into the single-file web app. Behavior is pinned to the Python implementation by the shared
 * cases in tests/vectors.json. Change one side and the other must follow.
 *
 * Listing objects use snake_case field names on purpose: they mirror the Python Listing and the
 * shared test cases.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.LibbyCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const BASE_URL = "https://thunder.api.overdrive.com/v2";

  // ---------------------------------------------------------------- ISBN

  function check13(d) {
    let t = 0;
    for (let i = 0; i < 12; i++) t += Number(d[i]) * (i % 2 === 0 ? 1 : 3);
    return (10 - (t % 10)) % 10 === Number(d[12]);
  }

  function check10(c) {
    let t = 0;
    for (let i = 0; i < 10; i++) t += (c[i] === "X" ? 10 : Number(c[i])) * (10 - i);
    return t % 11 === 0;
  }

  /** A valid ISBN-13 for an ISBN-10/13 in any common formatting (hyphens, Goodreads' ="..." wrapper), else null. */
  function toIsbn13(raw) {
    if (!raw) return null;
    const c = String(raw).replace(/[^0-9Xx]/g, "").toUpperCase();
    if (c.length === 13 && /^\d{13}$/.test(c) && check13(c)) return c;
    if (c.length === 10 && /^\d{9}/.test(c) && check10(c)) {
      const body = "978" + c.slice(0, 9);
      let t = 0;
      for (let i = 0; i < 12; i++) t += Number(body[i]) * (i % 2 === 0 ? 1 : 3);
      return body + String((10 - (t % 10)) % 10);
    }
    return null;
  }

  // ---------------------------------------------------------------- matching

  const BRACKETS = /\s*[(\[][^)\]]*[)\]]/g;
  const SUBTITLE_SPLIT = /\s*[:—–]\s+|\s+-\s+/;
  const ARTICLES = new Set(["the", "a", "an"]);
  const NAME_SUFFIXES = new Set(["jr", "sr", "ii", "iii", "iv", "phd", "md"]);
  const FUZZY_TITLE_THRESHOLD = 0.92;
  const GENERIC_TAILS = new Set(["novel", "memoir", "stories", "poems", "essays", "thriller", "mystery", "romance", "a story"]);
  const BENIGN_SUBTITLE = new RegExp(
    "\\b(novel|novella|memoir|stories|poems|poetry|essays|thriller|mystery|romance|edition|" +
      "unabridged|abridged|anniversary|reissue|reprint|annotated|illustrated|revised|expanded)\\b"
  );
  const LONG_SUBTITLE_WORDS = 4;
  const STRONG = ["isbn", "exact"];

  function stripAccents(s) {
    return s.normalize("NFKD").replace(/\p{M}/gu, "");
  }

  function norm(s) {
    s = stripAccents(s).toLowerCase().replace(/&/g, " and ").replace(/['’]/g, "");
    return s.replace(/[^a-z0-9]+/g, " ").trim();
  }

  function dropArticle(n) {
    const parts = n.split(" ");
    return parts.length > 1 && ARTICLES.has(parts[0]) ? parts.slice(1).join(" ") : n;
  }

  /** Split at the first subtitle separator, like Python's split(maxsplit=1). */
  function splitSubtitle(t) {
    const m = SUBTITLE_SPLIT.exec(t);
    return m ? [t.slice(0, m.index), t.slice(m.index + m[0].length)] : [t];
  }

  /** [full, main, tail] normalised forms. Brackets removed; tail is the subtitle ("" if none). */
  function titleForms(title) {
    const t = title.replace(BRACKETS, "");
    const parts = splitSubtitle(t);
    return [dropArticle(norm(t)), dropArticle(norm(parts[0])), parts.length > 1 ? dropArticle(norm(parts[1])) : ""];
  }

  /** Title without series brackets or subtitle, for building search queries. */
  function mainTitle(title) {
    return splitSubtitle(title.replace(BRACKETS, ""))[0].trim();
  }

  // Port of difflib.SequenceMatcher(None, a, b).ratio() for short strings (no junk/autojunk).
  function longestMatch(a, b, alo, ahi, blo, bhi) {
    let besti = alo, bestj = blo, bestsize = 0;
    let j2len = new Map();
    for (let i = alo; i < ahi; i++) {
      const next = new Map();
      for (let j = blo; j < bhi; j++) {
        if (a[i] !== b[j]) continue;
        const k = (j2len.get(j - 1) || 0) + 1;
        next.set(j, k);
        if (k > bestsize) { besti = i - k + 1; bestj = j - k + 1; bestsize = k; }
      }
      j2len = next;
    }
    return [besti, bestj, bestsize];
  }

  function matchedChars(a, b, alo, ahi, blo, bhi) {
    if (alo >= ahi || blo >= bhi) return 0;
    const [i, j, k] = longestMatch(a, b, alo, ahi, blo, bhi);
    if (!k) return 0;
    return k + matchedChars(a, b, alo, i, blo, j) + matchedChars(a, b, i + k, ahi, j + k, bhi);
  }

  function ratio(a, b) {
    const total = a.length + b.length;
    return total === 0 ? 1 : (2 * matchedChars(a, b, 0, a.length, 0, b.length)) / total;
  }

  /** "exact", "main-title", "tail", "fuzzy" or null. */
  function titleMatch(grTitle, odTitle, odSubtitle) {
    let odFull = odTitle;
    if (odSubtitle && !norm(odTitle).includes(norm(odSubtitle))) odFull = odTitle + ": " + odSubtitle;
    const [gFull, gMain, gTail] = titleForms(grTitle);
    const [oFull, oMain, oTail] = titleForms(odFull);
    if (!gFull || !oFull) return null;
    if (gFull === oFull) return "exact";
    if (gMain === oMain) {
      const gPlain = !gTail || BENIGN_SUBTITLE.test(gTail);
      const oPlain = !oTail || BENIGN_SUBTITLE.test(oTail);
      if (gPlain && oPlain) return "exact";
      if (gTail && !oTail && gTail.split(" ").length >= LONG_SUBTITLE_WORDS) return "exact";
      if (!(gTail && oTail)) return "main-title";
    }
    if (gTail && !oTail && gTail === oFull && !GENERIC_TAILS.has(gTail)) return "tail";
    if (oTail && !gTail && oTail === gFull && !GENERIC_TAILS.has(oTail)) return "tail";
    if (ratio(gFull, oFull) >= FUZZY_TITLE_THRESHOLD) return "fuzzy";
    return null;
  }

  /** [last name, first initial] from "First Last", "Last, First" or "J.R.R. Tolkien". */
  function authorKey(name) {
    name = stripAccents(name).replace(/['’]/g, "");
    const comma = name.indexOf(",");
    if (comma !== -1) name = name.slice(comma + 1) + " " + name.slice(0, comma);
    const tokens = name.replace(/[^A-Za-z]+/g, " ").toLowerCase().split(/\s+/).filter((t) => t && !NAME_SUFFIXES.has(t));
    if (!tokens.length) return ["", ""];
    return [tokens[tokens.length - 1], tokens.length > 1 ? tokens[0][0] : ""];
  }

  /** Surname as written (case and accents kept) for use in a search query. */
  function querySurname(name) {
    if (name.includes(",")) return name.slice(0, name.indexOf(",")).trim();
    const tokens = name.replace(/\./g, " ").split(/\s+/).filter((t) => t && !NAME_SUFFIXES.has(t.toLowerCase().replace(/^,+|,+$/g, "")));
    return tokens.length ? tokens[tokens.length - 1] : "";
  }

  function authorsMatch(grAuthors, odCreators) {
    for (const g of grAuthors) {
      const [gLast, gInit] = authorKey(g);
      if (!gLast) continue;
      for (const o of odCreators) {
        const [oLast, oInit] = authorKey(o);
        if (gLast === oLast && (!gInit || !oInit || gInit === oInit)) return true;
      }
    }
    return false;
  }

  function bookAuthors(book) {
    return [book.author, ...(book.additional_authors || [])].filter(Boolean);
  }

  /** "isbn", a title kind that also had a matching author, or null. */
  function match(book, listing) {
    if ((book.isbns || []).some((i) => listing.isbns.includes(i))) return "isbn";
    const kind = titleMatch(book.title, listing.title, listing.subtitle);
    return kind && authorsMatch(bookAuthors(book), listing.creators) ? kind : null;
  }

  // ---------------------------------------------------------------- listings

  const LISTING_DEFAULTS = {
    library: "", media_id: "", title: "", subtitle: "", creators: [], media_type: "", languages: [], isbns: [],
    is_owned: true, is_available: false, is_holdable: false, owned_copies: null, available_copies: null, holds: 0,
    est_wait_days: null, lucky_day_available: 0, pre_release: false, availability_type: "",
  };

  function makeListing(partial) {
    return Object.assign({}, LISTING_DEFAULTS, partial);
  }

  /** Port of Listing.from_api: trim a raw Thunder record to the fields we use. */
  function listingFromApi(raw, library) {
    let creators = (raw.creators || []).filter((c) => c.name).map((c) => c.name);
    if (!creators.length && raw.firstCreatorName) creators = [raw.firstCreatorName];
    const isbns = new Set();
    for (const f of raw.formats || []) {
      for (const id of f.identifiers || []) {
        if (id.type === "ISBN" || id.type === "LibraryISBN") {
          const isbn = toIsbn13(id.value);
          if (isbn) isbns.add(isbn);
        }
      }
    }
    return {
      library,
      media_id: String(raw.id),
      title: raw.title || "",
      subtitle: raw.subtitle || "",
      creators,
      media_type: (raw.type && raw.type.id) || "",
      languages: (raw.languages || []).filter((l) => l.id).map((l) => l.id),
      isbns: [...isbns].sort(),
      is_owned: raw.isOwned !== false,
      is_available: Boolean(raw.isAvailable),
      is_holdable: Boolean(raw.isHoldable),
      owned_copies: raw.ownedCopies ?? null,
      available_copies: raw.availableCopies ?? null,
      holds: raw.holdsCount || 0,
      est_wait_days: raw.estimatedWaitDays ?? null,
      lucky_day_available: raw.luckyDayAvailableCopies || 0,
      pre_release: Boolean(raw.isPreReleaseTitle),
      availability_type: raw.availabilityType || "",
    };
  }

  const unlimited = (l) => l.availability_type === "always";
  const inCollection = (l) => l.is_owned && (unlimited(l) || l.owned_copies !== 0);

  /** "available" | "wait" | "unavailable". Free copies must outnumber holds (see the Python docstring). */
  function status(l) {
    if (l.is_available) {
      if (unlimited(l)) return "available";
      if (l.available_copies === null) return l.holds === 0 ? "available" : "wait";
      return l.available_copies > l.holds ? "available" : "wait";
    }
    return l.is_holdable ? "wait" : "unavailable";
  }

  const copiesReservedForHolds = (l) => l.is_available && status(l) === "wait";

  function libraryUrl(l) {
    // Follows OverDrive's usual pattern; not verified from the build environment.
    return `https://${encodeURIComponent(l.library)}.overdrive.com/media/${encodeURIComponent(l.media_id)}`;
  }

  // ---------------------------------------------------------------- lookup pipeline

  const STATUS_RANK = { available: 0, wait: 1, unavailable: 2 };

  function queriesFor(book) {
    const title = mainTitle(book.title);
    const last = book.author ? querySurname(book.author) : "";
    const queries = [];
    if (title && last) queries.push(`${title} ${last}`);
    if (title) queries.push(title);
    queries.push(...[...(book.isbns || [])].sort());
    return queries.filter((q, i) => queries.indexOf(q) === i);
  }

  /** Verified offers for one book at one library. client.search(library, query) -> Promise<listing[]>. */
  async function findOffers(client, library, book, types, languages) {
    const typeSet = new Set(types), langSet = new Set(languages);
    let weak = [];
    for (const query of queriesFor(book)) {
      const offers = [];
      for (const listing of await client.search(library, query)) {
        if (!inCollection(listing)) continue;
        if (typeSet.size && !typeSet.has(listing.media_type)) continue;
        if (langSet.size && listing.languages.length && !listing.languages.some((l) => langSet.has(l))) continue;
        const kind = match(book, listing);
        if (kind) offers.push({ listing, match: kind });
      }
      const strong = offers.filter((o) => STRONG.includes(o.match));
      if (strong.length) return strong;
      if (!weak.length) weak = offers;
    }
    return weak;
  }

  function bestOffer(offers, prefer) {
    if (!offers.length) return null;
    const key = (o) => {
      const l = o.listing;
      const fmt = prefer.includes(l.media_type) ? prefer.indexOf(l.media_type) : prefer.length;
      const wait = l.est_wait_days !== null ? l.est_wait_days : 1e6;
      const st = status(l);
      return [STATUS_RANK[st], st === "available" ? 0 : wait, fmt, -(l.owned_copies || 0), l.library];
    };
    const cmp = (a, b) => {
      for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
      return 0;
    };
    let best = offers[0], bestKey = key(best);
    for (const o of offers.slice(1)) {
      const k = key(o);
      if (cmp(k, bestKey) < 0) { best = o; bestKey = k; }
    }
    return best;
  }

  /**
   * Check every book at every library with a small worker pool. A failed lookup is recorded on the
   * result (never thrown) so one flaky library cannot sink the run. opts: {types, languages, workers,
   * onProgress(done,total), shouldStop()}. Results keep the order of `books`.
   */
  async function checkBooks(books, libraries, client, opts) {
    const o = Object.assign({ types: [], languages: [], workers: 4 }, opts);
    const results = books.map((book) => ({ book, offers: [], errors: [] }));
    const tasks = [];
    books.forEach((_, i) => libraries.forEach((lib) => tasks.push([i, lib])));
    let next = 0, done = 0;
    async function worker() {
      for (;;) {
        if (o.shouldStop && o.shouldStop()) return;
        const t = tasks[next++];
        if (!t) return;
        const [i, lib] = t;
        try {
          results[i].offers.push(...(await findOffers(client, lib, books[i], o.types, o.languages)));
        } catch (e) {
          results[i].errors.push(`${lib}: ${e && e.message ? e.message : e}`);
        }
        done++;
        if (o.onProgress) o.onProgress(done, tasks.length);
      }
    }
    await Promise.all(Array.from({ length: Math.max(1, o.workers) }, worker));
    return results;
  }

  // ---------------------------------------------------------------- OverDrive client

  class ApiError extends Error {
    constructor(message) { super(message); this.name = "ApiError"; }
  }
  class LibraryNotFound extends ApiError {
    constructor(message) { super(message); this.name = "LibraryNotFound"; }
  }

  const CACHE_PREFIX = "lbc:v2:";

  /**
   * Thunder client for the browser (or Node, with a fake fetch in tests): spaces requests out, retries
   * transient failures with backoff, and caches search results briefly in `storage` (localStorage-like).
   */
  function createClient(options) {
    const o = Object.assign(
      { fetchFn: (...a) => fetch(...a), sleep: (ms) => new Promise((r) => setTimeout(r, ms)), now: () => Date.now(),
        storage: null, minInterval: 150, retries: 4, ttlMs: 30 * 60 * 1000, timeoutMs: 30000, refresh: false },
      options
    );
    let nextSlot = 0;

    async function throttle() {
      const t = o.now();
      const wait = nextSlot - t;
      nextSlot = Math.max(t, nextSlot) + o.minInterval;
      if (wait > 0) await o.sleep(wait);
    }

    async function get(path, params) {
      let url = BASE_URL + path;
      if (params) url += "?" + new URLSearchParams(params).toString();
      let lastError = "";
      for (let attempt = 0; attempt <= o.retries; attempt++) {
        await throttle();
        let delay = 2000 * 2 ** attempt;
        try {
          const init = {};
          if (typeof AbortSignal !== "undefined" && AbortSignal.timeout) init.signal = AbortSignal.timeout(o.timeoutMs);
          const resp = await o.fetchFn(url, init);
          if (resp.ok) return await resp.json();
          if (resp.status === 404) throw new LibraryNotFound(`404 Not Found: ${path}`);
          lastError = `HTTP ${resp.status} for ${path}`;
          if (![429, 500, 502, 503, 504].includes(resp.status)) throw new ApiError(lastError);
          const ra = resp.headers && resp.headers.get && resp.headers.get("Retry-After");
          if (ra && /^\d+$/.test(ra)) delay = Math.max(delay, Number(ra) * 1000);
        } catch (e) {
          if (e instanceof ApiError) throw e;
          lastError = `${e && e.name ? e.name : "Error"} for ${path}: ${e && e.message ? e.message : e}`;
        }
        if (attempt < o.retries) await o.sleep(delay);
      }
      throw new ApiError(`${lastError} (gave up after ${o.retries + 1} attempts)`);
    }

    function cacheGet(key) {
      if (o.refresh || !o.storage) return null;
      try {
        const blob = JSON.parse(o.storage.getItem(CACHE_PREFIX + key));
        if (blob && o.now() - blob.t <= o.ttlMs) return blob.d;
      } catch (e) { /* unreadable cache is just a miss */ }
      return null;
    }

    function cachePut(key, data) {
      if (!o.storage) return;
      try { o.storage.setItem(CACHE_PREFIX + key, JSON.stringify({ t: o.now(), d: data })); } catch (e) { /* quota: skip */ }
    }

    return {
      /** Throws LibraryNotFound for an unknown key. */
      async libraryInfo(key) {
        const raw = await get(`/libraries/${encodeURIComponent(key)}`);
        return { key: raw.preferredKey || raw.id || key, name: raw.name || "", status: raw.status || "", isConsortium: Boolean(raw.isConsortium) };
      },
      async search(library, query, perPage = 24) {
        const key = `search:${library}:${perPage}:${query}`;
        const hit = cacheGet(key);
        if (hit) return hit;
        const raw = await get(`/libraries/${encodeURIComponent(library)}/media`, { query, perPage: String(Math.min(perPage, 100)), page: "1" });
        const listings = (raw.items || []).map((item) => listingFromApi(item, library));
        cachePut(key, listings);
        return listings;
      },
      /** Drop expired cache entries so localStorage does not fill up. */
      prune() {
        if (!o.storage) return;
        try {
          const stale = [];
          for (let i = 0; i < o.storage.length; i++) {
            const k = o.storage.key(i);
            if (!k || !k.startsWith("lbc:")) continue;
            let blob = null;
            try { blob = JSON.parse(o.storage.getItem(k)); } catch (e) { /* corrupt */ }
            if (!k.startsWith(CACHE_PREFIX) || !blob || o.now() - blob.t > o.ttlMs) stale.push(k);
          }
          stale.forEach((k) => o.storage.removeItem(k));
        } catch (e) { /* ignore */ }
      },
    };
  }

  // ---------------------------------------------------------------- Goodreads input

  /** RFC 4180-style CSV parser (quotes, embedded newlines, CRLF, BOM). Like Python's csv module, a quote
   *  only opens a quoted field at the start of a field. */
  function parseCsv(text) {
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    const rows = [];
    let row = [], field = "", inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (inQuotes) {
        if (c === '"') {
          if (text[i + 1] === '"') { field += '"'; i++; } else inQuotes = false;
        } else field += c;
      } else if (c === '"' && field === "") inQuotes = true;
      else if (c === ",") { row.push(field); field = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(field); rows.push(row); row = []; field = "";
      } else field += c;
    }
    if (field !== "" || row.length) { row.push(field); rows.push(row); }
    return rows.filter((r) => r.some((f) => f !== ""));
  }

  /** Books on `shelf` from a Goodreads "Export Library" CSV. Throws a clear Error for the wrong file. */
  function booksFromGoodreadsCsv(text, shelf) {
    shelf = shelf || "to-read";
    const rows = parseCsv(text);
    if (!rows.length) throw new Error("The file is empty.");
    const headers = rows[0].map((h) => h.trim());
    const missing = ["Title", "Author"].filter((c) => !headers.includes(c));
    if (missing.length) {
      throw new Error(`Missing expected column(s) ${missing.join(", ")}. Found: ${headers.join(", ")}. Is this the Goodreads "Export Library" CSV?`);
    }
    const hasShelfInfo = headers.includes("Exclusive Shelf") || headers.includes("Bookshelves");
    const books = [];
    for (const r of rows.slice(1)) {
      const row = {};
      headers.forEach((h, i) => { row[h] = (r[i] || "").trim(); });
      if (hasShelfInfo && row["Exclusive Shelf"] !== shelf && !(row["Bookshelves"] || "").split(",").map((s) => s.trim()).includes(shelf)) continue;
      if (!row.Title) continue;
      books.push({
        title: row.Title,
        author: row.Author || "",
        gr_id: row["Book Id"] || "",
        isbns: [...new Set([toIsbn13(row.ISBN13), toIsbn13(row.ISBN)].filter(Boolean))],
        additional_authors: (row["Additional Authors"] || "").split(",").map((s) => s.trim()).filter(Boolean),
        date_added: row["Date Added"] || "",
      });
    }
    return books;
  }

  /** One book per line: "Title by Author", "Title - Author", "Title — Author" or "Title<TAB>Author".
   *  Returns {books, skipped}; lines without a recognisable author are skipped, because a book is only
   *  matched when both title and author agree. */
  function booksFromText(text) {
    const books = [], skipped = [];
    for (let line of text.split(/\r?\n/)) {
      line = line.trim();
      if (!line) continue;
      let m = line.match(/^(.+)\t(.+)$/) || line.match(/^(.+)\s+by\s+(.+)$/i) || line.match(/^(.+?)\s+[—–-]\s+(.+)$/);
      if (!m) { skipped.push(line); continue; }
      books.push({ title: m[1].trim(), author: m[2].trim(), gr_id: "", isbns: [], additional_authors: [], date_added: "" });
    }
    return { books, skipped };
  }

  // ---------------------------------------------------------------- report

  const SUMMARY_COLUMNS = ["Title", "Author", "Status", "Library", "Format", "Est. wait (days)", "Holds",
    "Copies available", "Copies owned", "Match", "Notes", "Library link", "Goodreads link"];
  const DETAIL_COLUMNS = ["Title", "Author", "Library", "Format", "Status", "Est. wait (days)", "Holds",
    "Copies available", "Copies owned", "Lucky Day copies available", "Match", "Catalog title", "Library link"];
  const STATUS_LABEL = { available: "Available now", wait: "Wait list", unavailable: "Owned, not holdable", none: "Not found", error: "Lookup failed" };
  const MATCH_LABEL = { isbn: "isbn", exact: "title+author", "main-title": "title+author (subtitle differs)",
    tail: "title+author (franchise prefix)", fuzzy: "title+author (fuzzy)" };
  const RESULT_RANK = { available: 0, wait: 1, unavailable: 2, error: 3, none: 4 };

  const blankNull = (v) => (v === null || v === undefined ? "" : v);
  const goodreadsUrl = (b) => (b.gr_id ? `https://www.goodreads.com/book/show/${b.gr_id}` : "");

  function resultStatus(r, prefer) {
    const best = bestOffer(r.offers, prefer);
    if (best) return status(best.listing);
    return r.errors.length ? "error" : "none"; // a failed lookup is never reported as "not in the catalog"
  }

  function notesFor(r, best, st) {
    const notes = [];
    if (best) {
      const l = best.listing;
      if (l.pre_release) notes.push("pre-release");
      if (unlimited(l)) notes.push("unlimited access");
      if (copiesReservedForHolds(l)) notes.push(`${l.available_copies} free copies but ${l.holds} holds: likely reserved for the queue`);
      if (l.lucky_day_available) notes.push(`Lucky Day copies available: ${l.lucky_day_available}`);
    }
    if (r.errors.length && (best || st === "error")) notes.push("lookup failed: " + r.errors.join("; "));
    return notes.join("; ");
  }

  /** One row per book, as arrays matching SUMMARY_COLUMNS; each row also carries a hidden .key = status key. */
  function summaryRows(results, prefer, sort) {
    const keyed = results.map((r, idx) => {
      const st = resultStatus(r, prefer);
      const best = bestOffer(r.offers, prefer);
      const l = best ? best.listing : null;
      const wait = !l || status(l) === "available" || l.est_wait_days === null ? "" : l.est_wait_days;
      const row = [r.book.title, r.book.author, STATUS_LABEL[st], l ? l.library : "", l ? l.media_type : "", wait,
        l ? l.holds : "", l ? blankNull(l.available_copies) : "", l ? blankNull(l.owned_copies) : "",
        best ? MATCH_LABEL[best.match] : "", notesFor(r, best, st), l ? libraryUrl(l) : "", goodreadsUrl(r.book)];
      row.state = st;
      row.result = r;
      const sortKey = sort === "title" ? [r.book.title.toLowerCase(), idx] : [RESULT_RANK[st], wait === "" ? 0 : wait, idx];
      return { sortKey, row };
    });
    keyed.sort((a, b) => {
      for (let i = 0; i < a.sortKey.length; i++) if (a.sortKey[i] !== b.sortKey[i]) return a.sortKey[i] < b.sortKey[i] ? -1 : 1;
      return 0;
    });
    return keyed.map((k) => k.row);
  }

  /** One row per library x format, matching DETAIL_COLUMNS. */
  function detailRows(results) {
    const rows = [];
    for (const r of results) {
      const offers = [...r.offers].sort((a, b) => {
        const ka = [STATUS_RANK[status(a.listing)], a.listing.library, a.listing.media_type];
        const kb = [STATUS_RANK[status(b.listing)], b.listing.library, b.listing.media_type];
        for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] < kb[i] ? -1 : 1;
        return 0;
      });
      for (const o of offers) {
        const l = o.listing, st = status(l);
        rows.push([r.book.title, r.book.author, l.library, l.media_type, STATUS_LABEL[st],
          st === "available" || l.est_wait_days === null ? "" : l.est_wait_days, l.holds, blankNull(l.available_copies),
          blankNull(l.owned_copies), l.lucky_day_available, MATCH_LABEL[o.match],
          l.title + (l.subtitle ? `: ${l.subtitle}` : ""), libraryUrl(l)]);
      }
    }
    return rows;
  }

  /** TSV (paste into Google Sheets) or CSV text. Cells never contain raw tabs/newlines. */
  function toDelimited(rows, columns, delimiter) {
    const clean = (v) => String(v).replace(/[\t\r\n]/g, " ");
    const quote = (s) => (s.includes(delimiter) || s.includes('"') ? `"${s.replace(/"/g, '""')}"` : s);
    return [columns, ...rows].map((r) => r.map((c) => quote(clean(c))).join(delimiter)).join("\n") + "\n";
  }

  return {
    BASE_URL, toIsbn13, norm, titleForms, mainTitle, ratio, titleMatch, authorKey, querySurname, authorsMatch, match, STRONG,
    makeListing, listingFromApi, status, inCollection, unlimited, copiesReservedForHolds, libraryUrl,
    queriesFor, findOffers, bestOffer, checkBooks, createClient, ApiError, LibraryNotFound,
    parseCsv, booksFromGoodreadsCsv, booksFromText,
    SUMMARY_COLUMNS, DETAIL_COLUMNS, STATUS_LABEL, MATCH_LABEL, summaryRows, detailRows, resultStatus, toDelimited,
  };
});
