// Run with: node --test web/test/core.test.js
// Pins the JavaScript port to the Python implementation via the shared tests/vectors.json.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const C = require("../src/core.js");

const root = path.join(__dirname, "..", "..");
const V = JSON.parse(fs.readFileSync(path.join(root, "tests", "vectors.json"), "utf8"));
const fixture = JSON.parse(fs.readFileSync(path.join(root, "tests", "fixtures", "nypl_overstory_search.json"), "utf8"));
const L = (partial) => C.makeListing(Object.assign({}, V.listing_defaults, partial));
const book = (b) => Object.assign({ gr_id: "", isbns: [], additional_authors: [], date_added: "" }, b);

test("toIsbn13", () => {
  for (const [raw, expect] of V.toIsbn13) assert.equal(C.toIsbn13(raw), expect, String(raw));
});

test("querySurname", () => {
  for (const [name, expect] of V.querySurname) assert.equal(C.querySurname(name), expect, name);
});

test("authorKey", () => {
  for (const [name, expect] of V.authorKey) assert.deepEqual(C.authorKey(name), expect, name);
});

test("authorsMatch", () => {
  for (const c of V.authorsMatch) assert.equal(C.authorsMatch(c.gr, c.od), c.expect, JSON.stringify(c));
});

test("titleMatch", () => {
  for (const c of V.titleMatch) assert.equal(C.titleMatch(c.gr, c.od, c.sub || ""), c.expect, JSON.stringify(c));
});

test("status / inCollection / reserved", () => {
  for (const c of V.status) {
    const l = L(c.listing);
    assert.equal(C.status(l), c.status, c.name);
    assert.equal(C.inCollection(l), c.in_collection, c.name);
    assert.equal(C.copiesReservedForHolds(l), c.reserved, c.name);
  }
});

test("queriesFor", () => {
  for (const c of V.queriesFor) assert.deepEqual(C.queriesFor(book(c.book)), c.expect, JSON.stringify(c.book));
});

test("findOffers", async () => {
  for (const c of V.findOffers) {
    const client = { search: async (_lib, q) => (c.catalog[q] || []).map(L) };
    const offers = await C.findOffers(client, "a", book(c.book), c.types || ["ebook", "audiobook"], c.languages || ["en"]);
    assert.deepEqual(offers.map((o) => [o.listing.title, o.listing.media_type, o.match]), c.expect, c.name);
  }
});

test("bestOffer", () => {
  for (const c of V.bestOffer) {
    const offers = c.offers.map((p) => ({ listing: L(p), match: "exact" }));
    assert.equal(C.bestOffer(offers, c.prefer), offers[c.expect], c.name);
  }
  assert.equal(C.bestOffer([], ["ebook"]), null);
});

test("difflib ratio matches Python's SequenceMatcher on known values", () => {
  // Python: SequenceMatcher(None, "color purple", "colour purple").ratio() == 24/25
  assert.ok(Math.abs(C.ratio("color purple", "colour purple") - 24 / 25) < 1e-12);
  assert.equal(C.ratio("abc", "abc"), 1);
  assert.equal(C.ratio("abc", "xyz"), 0);
  assert.equal(C.ratio("", ""), 1);
});

test("report output is byte-identical to Python's golden TSV/CSV", () => {
  const g = V.report;
  const results = g.results.map((c) => ({
    book: book(c.book),
    offers: c.offers.map((o) => ({ listing: L(o.listing), match: o.match })),
    errors: c.errors,
  }));
  assert.equal(C.toDelimited(C.summaryRows(results, g.prefer, "status"), C.SUMMARY_COLUMNS, "\t"), g.summary_status_tsv);
  assert.equal(C.toDelimited(C.summaryRows(results, g.prefer, "title"), C.SUMMARY_COLUMNS, ","), g.summary_title_csv);
  assert.equal(C.toDelimited(C.detailRows(results), C.DETAIL_COLUMNS, "\t"), g.detail_tsv);
});

test("real OverDrive response: parse, and only the true Overstory editions match", () => {
  const listings = fixture.items.map((it) => C.listingFromApi(it, "nypl"));
  const ebook = listings.find((l) => l.title === "The Overstory" && l.media_type === "ebook");
  assert.equal(ebook.media_id, "3360345");
  assert.deepEqual([ebook.owned_copies, ebook.available_copies, ebook.holds, ebook.est_wait_days], [16, 0, 77, 69]);
  assert.equal(C.status(ebook), "wait");
  assert.ok(ebook.isbns.includes("9780393635539"));
  const b = book({ title: "The Overstory", author: "Richard Powers" });
  const matched = listings.filter((l) => C.match(b, l)).map((l) => [l.media_type, l.title, C.match(b, l)]).sort();
  assert.deepEqual(matched, [["audiobook", "The Overstory", "exact"], ["ebook", "The Overstory", "exact"]]);
});

test("listingFromApi keeps missing counts as null (unlimited-access record seen live)", () => {
  const l = C.listingFromApi({ id: 1, title: "Tom Swift and His Motor Boat", isOwned: true, isAvailable: true, isHoldable: true,
    holdsCount: 0, availabilityType: "always", type: { id: "ebook" } }, "spl");
  assert.equal(l.owned_copies, null);
  assert.equal(l.available_copies, null);
  assert.equal(C.status(l), "available");
});

// ---- Goodreads input (same synthetic samples as the Python tests; not captured from a real account)

const CSV_TEXT =
  "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,Average Rating,Publisher,Binding,Number of Pages,Year Published,Original Publication Year,Date Read,Date Added,Bookshelves,Bookshelves with positions,Exclusive Shelf,My Review\r\n" +
  '1,"The Overstory","Richard Powers","Powers, Richard","","=""0393635554""","=""9780393635539""",0,4.1,Norton,Paperback,502,2019,2018,,2024/03/01,to-read,to-read (#1),to-read,\r\n' +
  '2,"Piranesi (Hardcover)","Susanna Clarke","Clarke, Susanna","","","",0,4.2,Bloomsbury,Hardcover,272,2020,2020,,2024/03/02,"favorites, to-read",,currently-reading,\r\n' +
  '3,"Already Read","Someone Else","Else, Someone","A. Translator, B. Illustrator","=""""","=""""",5,3.0,X,Hardcover,100,2001,2001,2024/01/01,2023/01/01,,,read,"line one\nline two"\r\n';

test("parseCsv handles quotes, doubled quotes, embedded newlines, CRLF and a BOM", () => {
  const rows = C.parseCsv("﻿a,b\r\n\"x,1\",\"he said \"\"hi\"\"\"\r\n\"multi\nline\",z\r\n");
  assert.deepEqual(rows, [["a", "b"], ["x,1", 'he said "hi"'], ["multi\nline", "z"]]);
});

test("booksFromGoodreadsCsv: shelf filter, ISBNs, additional authors", () => {
  const books = C.booksFromGoodreadsCsv(CSV_TEXT, "to-read");
  assert.deepEqual(books.map((b) => b.title), ["The Overstory", "Piranesi (Hardcover)"]); // 2nd via Bookshelves
  assert.deepEqual(books[0].isbns.sort(), ["9780393635539", "9780393635553"]);
  assert.equal(books[0].gr_id, "1");
  assert.deepEqual(books[1].isbns, []);
  const read = C.booksFromGoodreadsCsv(CSV_TEXT, "read");
  assert.deepEqual([read[0].author, ...read[0].additional_authors], ["Someone Else", "A. Translator", "B. Illustrator"]);
});

test("booksFromGoodreadsCsv: wrong file is a clear error, not an empty list", () => {
  assert.throws(() => C.booksFromGoodreadsCsv("name,thing\na,b\n", "to-read"), /Missing expected column/);
  assert.throws(() => C.booksFromGoodreadsCsv("", "to-read"), /empty/);
});

test("booksFromText: common separators; lines without an author are reported, not guessed", () => {
  const { books, skipped } = C.booksFromText(
    "Piranesi by Susanna Clarke\nThe Overstory - Richard Powers\nKlara and the Sun — Kazuo Ishiguro\nDune\tFrank Herbert\n\nJust A Title\n");
  assert.deepEqual(books.map((b) => [b.title, b.author]),
    [["Piranesi", "Susanna Clarke"], ["The Overstory", "Richard Powers"], ["Klara and the Sun", "Kazuo Ishiguro"], ["Dune", "Frank Herbert"]]);
  assert.deepEqual(skipped, ["Just A Title"]);
});

// ---- client: retry, backoff, cache (fake fetch, no network)

function fakeFetch(script) {
  const calls = [];
  const fn = async (url) => {
    calls.push(url);
    const item = script.shift();
    if (item instanceof Error) throw item;
    return { ok: item.status === 200, status: item.status, headers: { get: (k) => (item.headers || {})[k] || null }, json: async () => item.body };
  };
  return { fn, calls };
}
const memStorage = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k),
    get length() { return m.size; }, key: (i) => [...m.keys()][i] };
};
const SEARCH = { status: 200, body: fixture };
function client(script, extra) {
  const f = fakeFetch(script);
  const sleeps = [];
  return { f, sleeps, c: C.createClient(Object.assign({ fetchFn: f.fn, sleep: async (ms) => { sleeps.push(ms); }, minInterval: 0 }, extra)) };
}

test("client retries 503/429 with backoff, honouring Retry-After", async () => {
  const { f, sleeps, c } = client([{ status: 503 }, { status: 429, headers: { "Retry-After": "7" } }, SEARCH]);
  assert.equal((await c.search("nypl", "the overstory powers")).length, 5);
  assert.equal(f.calls.length, 3);
  assert.deepEqual(sleeps, [2000, 7000]);
});

test("client: network error is retried; exhausted retries give a clear ApiError", async () => {
  const a = client([new TypeError("Failed to fetch"), SEARCH]);
  assert.equal((await a.c.search("nypl", "q")).length, 5);
  const b = client([{ status: 500 }, { status: 500 }, { status: 500 }], { retries: 2 });
  await assert.rejects(b.c.search("nypl", "q"), /HTTP 500.*gave up after 3 attempts/);
  assert.equal(b.f.calls.length, 3);
});

test("client: 400 is not retried; 404 is LibraryNotFound", async () => {
  const a = client([{ status: 400 }]);
  await assert.rejects(a.c.search("nypl", "q"), C.ApiError);
  assert.equal(a.f.calls.length, 1);
  const b = client([{ status: 404 }]);
  await assert.rejects(b.c.libraryInfo("nope"), C.LibraryNotFound);
});

test("client cache: second call served from storage; refresh and expiry bypass it", async () => {
  const storage = memStorage();
  let now = 1000;
  const a = client([SEARCH, SEARCH, SEARCH], { storage, now: () => now });
  await a.c.search("nypl", "overstory");
  await a.c.search("nypl", "overstory");
  assert.equal(a.f.calls.length, 1);
  await a.c.search("kcls", "overstory"); // different library: separate entry
  assert.equal(a.f.calls.length, 2);
  now += 31 * 60 * 1000; // past the 30 min TTL
  await a.c.search("nypl", "overstory");
  assert.equal(a.f.calls.length, 3);
  const b = client([SEARCH], { storage, now: () => now, refresh: true });
  await b.c.search("nypl", "overstory");
  assert.equal(b.f.calls.length, 1);
});

test("client cache: prune drops expired and foreign-version entries only", () => {
  const storage = memStorage();
  let now = 5000;
  storage.setItem("lbc:v2:search:a:24:old", JSON.stringify({ t: 0, d: [] }));
  storage.setItem("lbc:v2:search:a:24:fresh", JSON.stringify({ t: now, d: [] }));
  storage.setItem("lbc:v1:search:legacy", JSON.stringify({ t: now, d: [] }));
  storage.setItem("someone-elses-key", "x");
  C.createClient({ storage, now: () => now, ttlMs: 1000 }).prune();
  assert.deepEqual([...Array(storage.length).keys()].map((i) => storage.key(i)).sort(), ["lbc:v2:search:a:24:fresh", "someone-elses-key"]);
});

test("checkBooks: order preserved, failures recorded not thrown, progress and stop honoured", async () => {
  const b1 = book({ title: "The Overstory", author: "Richard Powers" }), b2 = book({ title: "Piranesi", author: "Susanna Clarke" });
  const client = { search: async (lib, q) => { if (lib === "flaky") throw new C.ApiError("HTTP 500"); return q === "The Overstory Powers" ? [L({ library: "good" })] : []; } };
  const progress = [];
  const res = await C.checkBooks([b1, b2], ["good", "flaky"], client, { types: ["ebook"], languages: ["en"], workers: 2, onProgress: (d, t) => progress.push([d, t]) });
  assert.deepEqual(res.map((r) => r.book.title), ["The Overstory", "Piranesi"]);
  assert.equal(res[0].offers.length, 1);
  assert.match(res[0].errors[0], /flaky/);
  assert.equal(res[1].errors.length, 1);
  assert.deepEqual(progress[progress.length - 1], [4, 4]);
  let calls = 0;
  const stopped = await C.checkBooks([b1, b2], ["good"], { search: async () => { calls++; return []; } }, { workers: 1, shouldStop: () => calls >= 1 });
  assert.ok(calls <= 4 && stopped.length === 2);
});
