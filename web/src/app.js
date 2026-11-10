/* libbycheck web UI. All data from the catalog or the user's files is inserted as text, never as HTML. */
(function () {
  "use strict";
  const C = window.LibbyCore;
  const $ = (id) => document.getElementById(id);

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  // ---- persistence (all optional: the page works without storage)
  function webStorage() { try { return window.localStorage; } catch (e) { return null; } }
  const store = {
    get(key, fallback) {
      try { const v = webStorage().getItem(key); return v ? JSON.parse(v) : fallback; } catch (e) { return fallback; }
    },
    set(key, value) { try { webStorage().setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ } },
  };

  const DIRECTORY = JSON.parse($("library-directory").textContent); // [key, name, isConsortium]
  const EXAMPLE_BOOKS = [
    ["The Overstory", "Richard Powers"], ["Piranesi", "Susanna Clarke"], ["Braiding Sweetgrass", "Robin Wall Kimmerer"],
    ["Project Hail Mary", "Andy Weir"], ["Klara and the Sun", "Kazuo Ishiguro"], ["Gideon the Ninth", "Tamsyn Muir"],
  ].map(([title, author]) => ({ title, author, gr_id: "", isbns: [], additional_authors: [], date_added: "" }));

  const state = {
    books: store.get("libbycheck.books", []),
    booksSource: store.get("libbycheck.booksSource", ""),
    libraries: store.get("libbycheck.libraries", []), // [{key, name}]
    results: null, checkedAt: null, filter: "all", sort: "status", running: false, cancelled: false,
  };

  function message(el, kind, text) {
    el.hidden = !text;
    el.className = "msg " + kind;
    el.textContent = text || "";
  }

  function libName(key) {
    const l = state.libraries.find((x) => x.key === key);
    return l ? l.name : key;
  }

  // ---- last results survive a reload (phone browsers often reload a tab when you switch apps and back)
  const RESULTS_KEY = "libbycheck.lastRun";
  function validResults(list) {
    return Array.isArray(list) && list.every((r) => r && r.book && typeof r.book.title === "string" && Array.isArray(r.errors) &&
      Array.isArray(r.offers) && r.offers.every((o) => o && typeof o.match === "string" && o.listing && typeof o.listing.library === "string" &&
        typeof o.listing.media_id === "string" && Array.isArray(o.listing.creators)));
  }
  function setResults(results) {
    state.results = results;
    state.checkedAt = Date.now();
    store.set(RESULTS_KEY, { v: 1, at: state.checkedAt, results });
  }
  function clearResults() {
    state.results = null;
    state.checkedAt = null;
    store.set(RESULTS_KEY, null);
  }
  (function restoreResults() {
    const saved = store.get(RESULTS_KEY, null);
    if (saved && saved.v === 1 && validResults(saved.results)) { state.results = saved.results; state.checkedAt = saved.at; }
  })();

  // ---- 1. books
  function setBooks(books, source) {
    state.books = books;
    state.booksSource = source;
    clearResults();
    store.set("libbycheck.books", books);
    store.set("libbycheck.booksSource", source);
    renderBooks();
    renderResults();
    updateRunButton();
  }

  function renderBooks() {
    const box = $("books-summary");
    box.hidden = !state.books.length;
    box.replaceChildren();
    if (!state.books.length) return;
    const preview = state.books.slice(0, 3).map((b) => b.title).join(", ");
    box.append(
      h("p", { text: `${state.books.length} book${state.books.length === 1 ? "" : "s"} ready (${state.booksSource})` }),
      h("p", { class: "small hint", text: preview + (state.books.length > 3 ? ", ..." : "") }),
      h("div", { class: "row" }, h("button", { type: "button", class: "link", text: "Clear books", onclick: () => setBooks([], "") }))
    );
  }

  $("csv-file").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const msg = $("books-msg");
    try {
      const shelf = $("opt-shelf").value.trim() || "to-read";
      const books = C.booksFromGoodreadsCsv(await file.text(), shelf);
      if (!books.length) {
        message(msg, "error", `No books found on the "${shelf}" shelf in ${file.name}. If your shelf has another name, change it under Options and open the file again.`);
        return;
      }
      message(msg, "info", "");
      setBooks(books, `Goodreads CSV, shelf "${shelf}"`);
    } catch (err) {
      message(msg, "error", err.message);
    }
  });

  $("use-paste").addEventListener("click", () => {
    const { books, skipped } = C.booksFromText($("paste-list").value);
    if (!books.length) {
      message($("books-msg"), "error", "No books recognised. Put each book on its own line as: Title by Author");
      return;
    }
    message($("books-msg"), skipped.length ? "error" : "info",
      skipped.length ? `Skipped ${skipped.length} line${skipped.length === 1 ? "" : "s"} with no author (a book is only matched when title and author both agree): ${skipped.slice(0, 3).join("; ")}${skipped.length > 3 ? "..." : ""}` : "");
    setBooks(books, "pasted list");
  });

  $("use-example").addEventListener("click", () => {
    message($("books-msg"), "info", "These are example books, not your shelf.");
    setBooks(EXAMPLE_BOOKS, "example books");
  });

  // ---- 2. libraries
  function saveLibraries() {
    store.set("libbycheck.libraries", state.libraries);
    renderLibraries();
    updateRunButton();
  }

  function addLibrary(key, name) {
    if (state.libraries.some((l) => l.key === key)) return;
    state.libraries.push({ key, name });
    clearResults();
    saveLibraries();
    renderResults();
  }

  function renderLibraries() {
    const chips = $("lib-chips");
    chips.replaceChildren(...state.libraries.map((l) =>
      h("li", { class: "chip" }, l.name, h("button", {
        type: "button", "aria-label": `Remove ${l.name}`, text: "×",
        onclick: () => { state.libraries = state.libraries.filter((x) => x.key !== l.key); clearResults(); saveLibraries(); renderResults(); },
      }))));
    chips.hidden = !state.libraries.length;
  }

  function searchLibraries(q) {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    return DIRECTORY.filter(([key, name]) => { const hay = `${name} ${key}`.toLowerCase(); return words.every((w) => hay.includes(w)); }).slice(0, 30);
  }

  $("lib-search").addEventListener("input", (e) => {
    const list = $("lib-results");
    const found = searchLibraries(e.target.value);
    list.replaceChildren(...found.map(([key, name, consortium]) =>
      h("li", {}, h("button", { type: "button", onclick: () => { addLibrary(key, name); $("lib-search").value = ""; list.hidden = true; } },
        name, " ", h("span", { class: "key", text: `(${key}${consortium ? ", consortium" : ""})` })))));
    if (e.target.value.trim() && !found.length) list.replaceChildren(h("li", {}, h("button", { type: "button", disabled: true, text: "No match. Try fewer words, or add by key below." })));
    list.hidden = !e.target.value.trim();
  });

  $("lib-key-add").addEventListener("click", async () => {
    const key = $("lib-key").value.trim().toLowerCase();
    const msg = $("libs-msg");
    if (!key) return;
    try {
      const info = await C.createClient({ minInterval: 0 }).libraryInfo(key);
      if (info.status && info.status !== "Live") { message(msg, "error", `${info.name || key} exists but its status is "${info.status}", so it is not an active library.`); return; }
      addLibrary(info.key, info.name || key);
      $("lib-key").value = "";
      message(msg, "info", "");
    } catch (err) {
      message(msg, "error", err instanceof C.LibraryNotFound ? `No library has the key "${key}". Search by name above instead.` : `Could not reach OverDrive to check "${key}": ${err.message}`);
    }
  });

  // ---- 3. run
  function chosenTypes() { return ["ebook", "audiobook"].filter((t) => $("opt-" + t).checked); }

  function updateRunButton() {
    const problems = [];
    if (!state.books.length) problems.push("add your books");
    if (!state.libraries.length) problems.push("add a library");
    if (!chosenTypes().length) problems.push("choose ebooks or audiobooks under Options");
    $("run").disabled = state.running || problems.length > 0;
    $("run-hint").textContent = problems.length ? `To start: ${problems.join(", ")}.` :
      `${state.books.length} book${state.books.length === 1 ? "" : "s"} at ${state.libraries.length} librar${state.libraries.length === 1 ? "y" : "ies"}. Large shelves take a minute or two.`;
  }
  ["opt-ebook", "opt-audiobook"].forEach((id) => $(id).addEventListener("change", updateRunButton));

  $("cancel").addEventListener("click", () => { state.cancelled = true; $("cancel").disabled = true; });

  $("run").addEventListener("click", async () => {
    // Keep the earlier results aside: a cancelled or failed run should not throw them away.
    const previous = { results: state.results, checkedAt: state.checkedAt };
    state.running = true; state.cancelled = false; state.results = null;
    $("cancel").hidden = false; $("cancel").disabled = false;
    $("progress").hidden = false; $("progress-text").hidden = false;
    renderResults(); updateRunButton();
    const client = C.createClient(); // no cache: every check asks the libraries again, so the numbers are fresh
    let finished = false;
    try {
      const results = await C.checkBooks(state.books, state.libraries.map((l) => l.key), client, {
        types: chosenTypes(), languages: $("opt-english").checked ? ["en"] : [], workers: 4,
        shouldStop: () => state.cancelled,
        onProgress: (done, total) => {
          $("progress").max = total; $("progress").value = done;
          $("progress-text").textContent = `Checked ${done} of ${total} lookups`;
        },
      });
      if (state.cancelled) {
        // Unchecked books would show as "Not found", which is false, so the partial run is dropped.
        state.results = previous.results; state.checkedAt = previous.checkedAt;
        $("progress-text").textContent = previous.results ? "Cancelled. Your earlier results are shown again." : "Cancelled. Nothing to show.";
      } else {
        setResults(results);
        $("progress-text").textContent = summaryText(results);
        finished = true;
      }
    } catch (err) {
      state.results = previous.results; state.checkedAt = previous.checkedAt;
      $("progress-text").textContent = "Something went wrong: " + err.message;
    } finally {
      state.running = false;
      $("cancel").hidden = true; $("progress").hidden = true;
      renderResults(); updateRunButton();
      if (finished) scrollToResults();
    }
  });

  function summaryText(results) {
    const count = { available: 0, wait: 0, none: 0, error: 0 };
    for (const row of C.summaryRows(results, ["ebook", "audiobook"], "status")) count[row.state === "unavailable" ? "wait" : row.state] += 1;
    const parts = [`${count.available} available now`, `${count.wait} on wait lists`, `${count.none} not found`];
    if (count.error) parts.push(`${count.error} lookup failed`);
    return `Done. ${parts.join(", ")}. Results are below.`;
  }

  // The results sit below the button, off-screen on a phone, so bring them into view.
  function scrollToResults() {
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    $("results").scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }

  // ---- results
  const FILTERS = [
    ["all", "All", () => true],
    ["available", "Available now", (r) => r.state === "available"],
    ["wait", "Wait list", (r) => r.state === "wait" || r.state === "unavailable"],
    ["none", "Not found", (r) => r.state === "none"],
    ["error", "Lookup failed", (r) => r.state === "error"],
  ];

  function currentRows() {
    const prefer = ["ebook", "audiobook"];
    const rows = C.summaryRows(state.results, prefer, state.sort);
    const f = FILTERS.find((x) => x[0] === state.filter) || FILTERS[0];
    return { all: rows, shown: rows.filter(f[2]) };
  }

  function metaLine(r) {
    const best = C.bestOffer(r.offers, ["ebook", "audiobook"]);
    if (!best) return "";
    const l = best.listing, st = C.status(l);
    const parts = [libName(l.library), l.media_type];
    if (st === "available") {
      parts.push(C.unlimited(l) ? "unlimited copies" : l.available_copies !== null && l.owned_copies !== null ? `${l.available_copies} of ${l.owned_copies} copies free` : "copy free");
    } else {
      if (l.est_wait_days !== null) parts.push(`about ${l.est_wait_days} day${l.est_wait_days === 1 ? "" : "s"} wait`);
      parts.push(`${l.holds} hold${l.holds === 1 ? "" : "s"}`);
      if (l.owned_copies !== null) parts.push(`${l.owned_copies} copies`);
    }
    return parts.join(" · ");
  }

  function detailTable(r) {
    const rows = C.detailRows([r]);
    if (!rows.length) return null;
    const cols = ["Library", "Format", "Status", "Wait (days)", "Holds", "Free / owned", "Match"];
    return h("details", {}, h("summary", { text: `All libraries and formats (${rows.length})` }),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, cols.map((c) => h("th", { text: c })))),
        h("tbody", {}, rows.map((d) => {
          return h("tr", {},
            h("td", {}, h("a", { href: d[12], target: "_blank", rel: "noopener noreferrer", text: libName(d[2]) })),
            h("td", { text: d[3] }), h("td", { text: d[4] }), h("td", { text: d[5] }), h("td", { text: d[6] }),
            h("td", { text: d[7] === "" && d[8] === "" ? "unlimited" : `${d[7]} / ${d[8]}` }), h("td", { text: d[10] }));
        })))));
  }

  function renderBookCard(row) {
    const r = row.result;
    const st = row.state;
    const best = C.bestOffer(r.offers, ["ebook", "audiobook"]);
    const links = [];
    if (best) links.push(h("a", { href: C.libraryUrl(best.listing), target: "_blank", rel: "noopener noreferrer", text: "Open at library" }));
    if (r.book.gr_id) links.push(h("a", { href: `https://www.goodreads.com/book/show/${encodeURIComponent(r.book.gr_id)}`, target: "_blank", rel: "noopener noreferrer", text: "Goodreads" }));
    return h("li", { class: "book", "data-state": st },
      h("div", { class: "book-head" },
        h("div", {}, h("div", { class: "book-title", text: r.book.title }), h("div", { class: "book-author", text: r.book.author })),
        h("span", { class: "pill", "data-state": st, text: C.STATUS_LABEL[st] })),
      metaLine(r) && h("div", { class: "meta", text: metaLine(r) }),
      row[10] && h("div", { class: "note", text: row[10] }),
      best && !C.STRONG.includes(best.match) && h("div", { class: "note", text: `Looser match (${C.MATCH_LABEL[best.match]}). Check that it is the right book.` }),
      links.length ? h("div", { class: "links" }, links) : null,
      detailTable(r));
  }

  function renderResults() {
    const box = $("results");
    box.hidden = !state.results;
    if (!state.results) return;
    $("results-when").textContent = state.checkedAt
      ? `Checked ${new Date(state.checkedAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}. Availability changes through the day, so run it again for fresh numbers.`
      : "";
    const { all, shown } = currentRows();
    $("filters").replaceChildren(...FILTERS.map(([id, label, test]) => {
      const n = all.filter(test).length;
      if (id !== "all" && id !== "available" && id !== "wait" && n === 0) return null; // hide empty rare buckets
      return h("button", { type: "button", "aria-pressed": String(state.filter === id), text: `${label} (${n})`,
        onclick: () => { state.filter = id; renderResults(); } });
    }).filter(Boolean));
    $("sort").value = state.sort;
    const failed = state.results.filter((r) => r.errors.length).length;
    message($("results-msg"), failed ? "error" : "info",
      failed ? `${failed} book${failed === 1 ? "" : "s"} had a failed lookup, so results for those may be incomplete. Run the check again to retry.` : "");
    $("book-list").replaceChildren(...shown.map(renderBookCard));
    if (!shown.length) $("book-list").replaceChildren(h("li", { class: "small hint", text: "Nothing in this group." }));
  }

  $("sort").addEventListener("change", (e) => { state.sort = e.target.value; renderResults(); });

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall through */ }
    const ta = h("textarea", { style: "position:fixed;opacity:0", "aria-hidden": "true" });
    ta.value = text; document.body.append(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { /* ignore */ }
    ta.remove();
    return ok;
  }

  $("copy").addEventListener("click", async () => {
    const { shown } = currentRows();
    const ok = await copyText(C.toDelimited(shown, C.SUMMARY_COLUMNS, "\t"));
    message($("results-msg"), ok ? "info" : "error", ok ? `Copied ${shown.length} rows. Paste into cell A1 of a Google Sheet.` : "Could not copy automatically. Use Download CSV instead.");
  });

  $("download").addEventListener("click", () => {
    const { shown } = currentRows();
    const url = URL.createObjectURL(new Blob([C.toDelimited(shown, C.SUMMARY_COLUMNS, ",")], { type: "text/csv" }));
    const a = h("a", { href: url, download: "libbycheck.csv" });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  // ---- boot
  // Earlier versions cached lookups for 30 minutes. Checks now always fetch fresh numbers, so clear any leftovers.
  try { const ls = webStorage(); Object.keys(ls).filter((k) => k.startsWith("lbc:")).forEach((k) => ls.removeItem(k)); } catch (e) { /* ignore */ }
  renderBooks();
  renderLibraries();
  renderResults();
  updateRunButton();
  // Test/automation hook: lets a script read the rendered state without scraping.
  window.__libbycheck = state;
})();
