"use strict";
// The static-demo shim. Injected into the EXPORTED copy of index.html by
// `scripts/export_static_demo.py`, before `app.js`, and never served by the
// FastAPI app — `targetring/static/` is untouched, so the server's demo-mode
// behaviour is exactly what `tests/test_demo.py` enumerates and this file
// cannot change it.
//
// Why a fetch wrapper rather than an edit to app.js: app.js builds its URLs in
// 23 places and three of them are template literals inside innerHTML. A rewrite
// pass over that source would be a second copy of the app that drifts. One
// wrapper, with the SAME two closed lists the server's `_demo_gate` uses, is
// the whole adapter — and when a route is added to app.js that nobody exported,
// it lands in the "no file for this" arm and says so, instead of 404ing into a
// console error.
//
// Three jobs:
//
//  1. Rebase. The exported site lives at some prefix (a user page serves it at
//     `/targetring/demo/`, `python -m http.server` at whatever directory you
//     point it at), so every absolute `/api/...` the app emits has to become a
//     path relative to THIS page. Read off `location`, never configured.
//  2. Name the file. `/api/runs` is a directory on a static host, so the export
//     writes `api/runs.json` and this maps onto it. Artefacts (`figure.png`,
//     `manifest.json`) already have an extension and keep their name.
//  3. Refuse, in the server's own words. Everything the demo gate 403s, plus
//     every POST and every route whose answer depends on an argument the export
//     could not enumerate (a `?sort=`, a live `/gene/` drawer), is answered
//     here with the same `{error, message}` JSON shape at the same status. The
//     page already renders that shape — a refused disease search shows the
//     sentence in its own error box — so nothing reaches the console.
(function () {
  // The directory this page was served from, with its trailing slash. `?run=`
  // deep links keep the same directory, so one read at load is enough.
  var BASE = location.pathname.replace(/[^/]*$/, "");
  // Published for anything that wants it (and so the console can check what the
  // page thinks it is hosted at).
  window.TARGETRING_API_BASE = BASE;
  window.TARGETRING_STATIC_DEMO = true;

  // Verbatim `app.DEMO_REFUSAL`. Asserted equal by the export script, so the
  // two cannot drift silently.
  var REFUSAL = "This is a read-only demo: it serves finished runs and makes no " +
    "requests and no model calls. Clone the repo to run analyses.";
  var STATIC_NOTE = "This copy is a static export: there is no server behind it " +
    "at all, only the files four finished runs wrote.";

  // `app._DEMO_FORBIDDEN_*`, same four shapes, same order.
  var FORBIDDEN_EXACT = {
    "/api/analyse": 1, "/api/autopick": 1, "/api/region-for": 1,
    "/api/diseases": 1, "/api/contrasts": 1
  };
  var FORBIDDEN_PREFIX = ["/api/contrast/", "/api/analyse/progress/"];
  var FORBIDDEN_SUFFIX = ["/phenotypes", "/web-figure"];
  var FORBIDDEN_PATTERNS = [/^\/api\/run\/[^/]+\/gene\/[^/]+$/];

  function forbids(path) {
    if (FORBIDDEN_EXACT[path]) return true;
    for (var i = 0; i < FORBIDDEN_PREFIX.length; i++) {
      if (path.indexOf(FORBIDDEN_PREFIX[i]) === 0) return true;
    }
    for (var j = 0; j < FORBIDDEN_SUFFIX.length; j++) {
      var s = FORBIDDEN_SUFFIX[j];
      if (path.length >= s.length && path.lastIndexOf(s) === path.length - s.length) return true;
    }
    for (var k = 0; k < FORBIDDEN_PATTERNS.length; k++) {
      if (FORBIDDEN_PATTERNS[k].test(path)) return true;
    }
    return false;
  }

  function refusal(message, status, error) {
    return Promise.resolve(new Response(
      JSON.stringify({ error: error || "demo_mode", message: message, note: STATIC_NOTE }),
      { status: status || 403, headers: { "Content-Type": "application/json" } }));
  }

  // Which exported file answers this path, or null for "nothing here does".
  // Returns a path relative to BASE.
  function fileFor(path, query) {
    // The artefact route: `/api/run/{id}/{filename}` where filename already
    // carries its type. Also catches `values.csv`, which must NOT be confused
    // with the `values` payload below.
    var art = /^\/api\/run\/([^/]+)\/([^/]+\.[a-z]+)$/.exec(path);
    if (art) return "api/run/" + art[1] + "/" + art[2];

    // The baked drawer, one file per gene.
    var drawer = /^\/api\/run\/([^/]+)\/drawer\/([^/]+)$/.exec(path);
    if (drawer) return "api/run/" + drawer[1] + "/drawer/" + drawer[2] + ".json";

    // One run's grid. Only the unsorted, unfiltered view was exported: the sort
    // is `order.py`'s and the dim filter is §3.2's, both server-side on purpose
    // (a second definition in JS is the drift the app's own comment refuses), so
    // a static copy can offer the run's stated order and nothing else.
    var vals = /^\/api\/run\/([^/]+)\/values$/.exec(path);
    if (vals) {
      var q = new URLSearchParams(query || "");
      if ((q.get("sort") || "") || (q.get("dim") || "")) return false;
      return "api/run/" + vals[1] + "/values.json";
    }

    var ev = /^\/api\/run\/([^/]+)\/evidence$/.exec(path);
    if (ev) return "api/run/" + ev[1] + "/evidence.json";

    // The flat payloads.
    if (/^\/api\/(runs|regions|orderings|example|health)$/.test(path)) {
      return "api" + path.slice(4) + ".json";
    }

    // A page file, asked for absolutely.
    if (/^\/[A-Za-z0-9._-]+$/.test(path)) return path.slice(1);

    return null;
  }

  var realFetch = window.fetch.bind(window);

  window.fetch = function (input, init) {
    var url, method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
    try {
      url = new URL(typeof input === "string" ? input : input.url, location.href);
    } catch (e) {
      return realFetch(input, init);
    }
    // Anything off this origin is somebody else's business (there is nothing
    // like that in app.js today; this is so adding one is not silently broken).
    if (url.origin !== location.origin) return realFetch(input, init);

    var path = url.pathname;
    // Already rebased by a previous pass, or a relative URL that resolved under
    // BASE: strip the prefix back off so one set of rules sees one shape.
    if (BASE !== "/" && path.indexOf(BASE) === 0) path = "/" + path.slice(BASE.length);

    if (method !== "GET" && method !== "HEAD") return refusal(REFUSAL);
    if (forbids(path)) return refusal(REFUSAL);

    var file = fileFor(path, url.search.replace(/^\?/, ""));
    if (file === false) {
      return refusal(
        "Re-sorting and dim filters are decided by the server (order.py and the " +
        "dim rules), so a static copy serves only the order this run drew its " +
        "spokes in. " + STATIC_NOTE, 403, "static_demo");
    }
    if (file === null) return refusal(REFUSAL, 404, "unknown_route");
    return realFetch(BASE + file, init);
  };

  // ---- the one button that cannot be answered by a file --------------------
  // "Load phenotype table & summary" calls `/api/run/<id>/gene/<gene>/
  // phenotypes`, which fetches Open Targets and Europe PMC and then spends on
  // an LLM call. The demo gate 403s it and this export has no file for it, so
  // it cannot work here by any route — but `loadPhenotypes` reads `RUN.run_id`
  // before it fetches, and `RUN` is null for every run read from disk, so what
  // the panel actually prints is "TypeError: Cannot read properties of null".
  // A refusal is a fine answer; a TypeError is not an answer. Replaced after
  // `app.js` has defined it (the inline `onclick` resolves through `window` at
  // click time), so this file still does not touch the app's source.
  //
  // The panel it replaces is not the only place this gene's phenotypes appear:
  // the baked drawer above it already carries `phenotype_terms` with the MGI
  // models under each one, and that is rendered from the file.
  window.addEventListener("DOMContentLoaded", function () {
    window.loadPhenotypes = function (gene) {
      var sel = '#phenotype-panel[data-gene="' + (window.CSS ? CSS.escape(gene) : gene) + '"]';
      var panel = document.querySelector(sel) || document.querySelector("#phenotype-panel");
      if (!panel) return;
      panel.innerHTML =
        '<div class="note">' + REFUSAL + " " + STATIC_NOTE +
        " The per-paper MGI table is the one surface that was never baked into a " +
        "run's artefacts — it fetches and then spends on every click. The mouse " +
        "phenotype terms for this gene, and the biological models under each of " +
        "them, are above.</div>";
    };
  });

  // ---- attributes, not just fetches ----------------------------------------
  // The demo cards and the deep-linked figure hint are built with innerHTML and
  // carry absolute `src`/`href` ("/api/run/<id>/figure.png"). Those never go
  // through fetch, so they need the same rebase — and it has to happen BEFORE
  // the HTML is parsed, not after. An observer alone leaves a window: the
  // parser starts the `<img>` request immediately, the callback runs on the
  // next microtask, and the net result is a 404 for the un-rebased URL in the
  // console followed by a correct second request. Measured, not theorised: that
  // was one `net::ERR_ABORTED` per card. So the setter rewrites the string.
  var ATTR_URL = /\b(src|href)\s*=\s*"(\/[^"]*)"/g;

  function rewriteHtml(html) {
    return html.replace(ATTR_URL, function (whole, attr, url) {
      if (url.charAt(1) === "/") return whole;          // protocol-relative
      var f = fileFor(url.split("?")[0], "");
      return f ? attr + '="' + BASE + f + '"' : whole;
    });
  }

  var innerHTMLDesc = Object.getOwnPropertyDescriptor(Element.prototype, "innerHTML");
  Object.defineProperty(Element.prototype, "innerHTML", {
    configurable: true,
    enumerable: innerHTMLDesc.enumerable,
    get: innerHTMLDesc.get,
    set: function (v) { innerHTMLDesc.set.call(this, rewriteHtml(String(v))); }
  });

  // The backstop, for anything inserted another way. A no-op today; it costs
  // nothing and it is the difference between a broken image and a correct one
  // if app.js ever builds a node with `createElement`.
  function rebase(el) {
    ["src", "href"].forEach(function (attr) {
      var v = el.getAttribute && el.getAttribute(attr);
      if (!v || v.charAt(0) !== "/" || v.charAt(1) === "/") return;
      var f = fileFor(v.split("?")[0], "");
      if (!f) return;
      el.setAttribute(attr, BASE + f);
    });
  }

  function rebaseTree(node) {
    if (node.nodeType !== 1) return;
    rebase(node);
    var kids = node.querySelectorAll ? node.querySelectorAll("[src],[href]") : [];
    for (var i = 0; i < kids.length; i++) rebase(kids[i]);
  }

  new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      var added = records[i].addedNodes;
      for (var j = 0; j < added.length; j++) rebaseTree(added[j]);
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
