# Licences of sources on the render path

PLAN §9: *"To be checked by a human before workplace use ... HPA's terms
need reading if it returns to the stack."* This is that read, done for the
public-demo decision (`docs/DECISION-DEMO-CROHNS-2026-10-18.md`), plus the
other sources actually on the render path, read live on 2026-10-18. Flagged
up front: **nothing here blocks public display of the derived figure**, but
two sources (GTEx, HPA) impose a specific attribution sentence that is not
currently on the figure, and HPA is not actually fetched by this codebase
today.

## Summary table

| source | licence | derived-figure display OK? | attribution required on demo page |
|---|---|---|---|
| Open Targets | **CC0 1.0** (public domain dedication) for data; Apache 2.0 for code | Yes, no restriction | Not legally required (CC0), but citation requested |
| GTEx | **Bespoke GTEx Portal Data License** (free, no royalty, mandatory attribution clause, no-endorsement clause) | Yes | **Yes — exact phrase required** (below) |
| PubTator3 (NCBI/NLM) | **US government work** (NLM-created annotations are public domain); underlying PubMed abstract text may carry publisher copyright, not NLM's to license | Yes, for the derived count this app shows | No legal requirement found; standard NLM courtesy acknowledgment recommended |
| Pharos (NCATS/NIH) | **No separate Pharos licence** — "data accessed from Pharos and TCRD is publicly available from the primary sources ... respect their individual licenses" | Yes, for the TDL classification (Pharos's own derived call) | No legal requirement found; citation requested (DOI given) |
| Human Protein Atlas | **CC BY 4.0** for HPA's own copyrightable content, per HPA's own licence page — **note: Open Targets' source table lists HPA itself as CC BY-SA 3.0**, a discrepancy between how HPA describes its own licence and how a third party describes it; HPA's own page governs for direct use | Yes, with attribution | **Yes — citation + image credit required** (below). **Not currently fetched by this codebase at all** — see below. |

Also on the render path but not named in PLAN §9 or this task, read from the
same Open Targets table and included for completeness since they feed the
drawer: **ChEMBL** (drug mechanism/pipeline data) is CC BY-SA 3.0 — share-
alike, attribution required. **Europe PMC** (literature query verification,
rule 4) is "CC-BY, CC-BY-NC or CC0 or open access depending on the
publication" — per-article, not a blanket licence; this app only uses it to
verify a query echoed unchanged and does not redistribute article text, which
is a narrower use than display.

## Per source, in detail

### Open Targets

- Licence: <https://platform-docs.opentargets.org/licence> — data marked
  **CC0 1.0** (<http://creativecommons.org/publicdomain/zero/1.0>), "dedicates
  the data to the public domain, allowing downstream users to consume the
  data without restriction." Codebase under Apache 2.0.
- The page explicitly states every data source Open Targets aggregates (its
  own licensing table, 40+ rows including ChEMBL, GTEx, Expression Atlas,
  HPA) "have agreed for their data to be used without restriction by all
  Open Targets users" — so values read *through* the Open Targets API
  (tractability, safety flags, chemical probe, trial phase/activity, mouse
  phenotype) carry Open Targets' own CC0 grant, not the upstream source's
  original licence.
- Public display: **permitted, no restriction.** Citation is requested, not
  required; the README/figure manifest already names Open Targets as a
  source, which satisfies the request.

### GTEx

- Licence page (JS-rendered; read via browser, not `web_fetch`):
  <https://gtexportal.org/home/license>.
- Not a standard CC licence — a bespoke "GTEx Portal Data License." Key
  terms: free, no royalty; users must acknowledge GTEx as the source "in a
  clear and conspicuous manner"; must not imply GTEx endorsement; and anyone
  *redistributing* the data must either keep it current or state clearly
  that it may not reflect the current/accurate data.
- **Exact required phrase** (from the licence text, with the bracketed
  manuscript wording adapted for a web page rather than a paper): *"The data
  used for the analyses described [on this page] were obtained from: the
  GTEx Portal on [date]."* A citation to GTEx v8 (the dataset version pinned
  in `targetring/config.py`) plus a link to gtexportal.org satisfies this.
- Public display: **permitted**, conditional on that attribution being
  present and "clear and conspicuous" — i.e. not buried in a footer link
  three clicks deep. **This app's current figure/caption does not carry this
  exact phrase** — it names GTEx as a ring source but not with the dated
  acknowledgment sentence the licence specifies. Flag, not a blocker: fix by
  adding one sentence to the demo page template, not to every run.

### PubTator3 (NCBI / NLM)

- No PubTator3-specific licence page found; governed by NCBI's general
  policy: <https://www.ncbi.nlm.nih.gov/home/about/policies/>.
- "Information that is created by or for the US government on this site is
  within the public domain... Molecular Data Usage: NCBI itself places no
  restrictions on the use or distribution of the data." But: "some submitters
  of the original data... may claim... rights," and separately, NLM does not
  claim copyright on PubMed abstracts — "however, journal publishers or
  authors may."
- What this app actually shows from PubTator3: a **literature-mention
  count** (the "Literature volume" ring) — a derived number, not
  republished abstract text. That is squarely in the "NCBI places no
  restriction" category; the publisher-copyright caveat is about
  redistributing the underlying article text, which this app does not do.
- Public display: **permitted** for the count shown today. Would need a
  fresh look if the app ever displayed PubTator3-sourced abstract snippets
  rather than a count.

### Pharos (NCATS / NIH)

- Licence page: <https://pharos.nih.gov/about> → "Pharos License": *"Data
  accessed from Pharos and TCRD is publicly available from the primary
  sources listed above. Please respect their individual licenses regarding
  proper use and redistribution."*
- Pharos imposes no licence of its own; it passes through ~70 upstream
  sources' individual terms (ChEMBL, HPA, GTEx, GWAS Catalog, UniProt,
  Ensembl, etc. — the same names recurring across every source checked
  here). The one field this app reads from Pharos, **TDL** (target
  development level: Tdark → Tclin), is Pharos/TCRD's own classification —
  a value they compute and publish, not a verbatim copy of one named
  upstream record — and NIH/NCATS-funded federal work is not asserting
  copyright over that classification anywhere on the page.
- Public display: **permitted** for the TDL value shown. Citation requested
  (DOI given on the About page); not legally required.

### Human Protein Atlas — flagged, but not currently in scope

- Licence page: <https://www.proteinatlas.org/about/licence> — HPA's own
  copyrightable content is **CC BY 4.0**
  (<https://creativecommons.org/licenses/by/4.0/>). Attribution rules are
  specific and two-tiered:
  - **General citation**, anywhere HPA content/data is referenced: cite a
    primary publication (list given on the licence page, e.g. Uhlén et al.,
    *Science* 2015, PMID 25613900) **and** cite the website,
    "Human Protein Atlas proteinatlas.org."
  - **Specific citation**, for any image/gene/data actually shown: image
    credit "Human Protein Atlas," **plus** a direct URL to that
    image/gene/data on a versioned subdomain (`v##.proteinatlas.org`).
  - HPA's own page separately warns that some *included* third-party data
    (Ensembl, UniProt, GTEx, TCGA, AlphaFold, etc.) carries its own licence
    on top of HPA's CC BY 4.0, and it is "the responsibility of users... to
    ensure that their utilization... does not infringe any of the rights of
    such third parties."
- **The discrepancy worth flagging explicitly:** Open Targets' own
  source-licensing table lists "Human Protein Atlas" as **CC BY-SA 3.0**
  (a share-alike variant), not the CC BY 4.0 that HPA's own licence page
  states. The two are not equivalent — CC BY-SA 3.0 requires derivative works
  to carry the same licence forward; plain CC BY 4.0 does not. Since this
  app, if it ever adds HPA, would read HPA directly rather than through Open
  Targets' re-export, **HPA's own stated terms (CC BY 4.0) are the ones that
  govern**, but the discrepancy itself suggests the two organisations have
  not fully reconciled which licence applies to HPA data in a given context,
  and it is worth re-checking before any HPA integration ships.
- **Why this doesn't block anything today:** grepped `targetring/*.py` for
  `proteinatlas` and `HPA` — the only hits are in `targetring/http.py`,
  comments about HPA's HTTP error-response shape (HPA returns a 400 as a
  full HTML page, same bug class as MGI/GenTAR), not a live fetch. HPA is
  **not on the render path**; PLAN §9's own wording was conditional on that
  ("if it returns to the stack"). It hasn't. This read is preparation for if
  it does, not a response to a current gap.

## What must be shown on the demo page

1. The GTEx attribution sentence (above), dated, visible on the demo page
   itself — not just in the downloadable manifest.
2. A line naming Open Targets, PubTator3/NCBI, Pharos/NCATS, ChEMBL and the
   run's Expression Atlas source — all already present in every run's
   `manifest.json` and caption; carrying that same text onto the public
   page (not just into the downloadable artefacts) satisfies the spirit of
   every citation request above, none of which is a legal display-blocker.
3. **Nothing from HPA** — it isn't shown, so there is nothing to attribute.
   If HPA is ever added, items 1 (sentence pattern) and the specific-citation
   rule above both apply, and the CC BY 4.0 vs CC BY-SA 3.0 discrepancy
   should be resolved first.

## Bottom line

No licence read here blocks public display of the derived figure. The one
actionable gap is GTEx's required attribution sentence, which is not
currently printed anywhere a public viewer would see without opening the
manifest — a one-line fix to the demo page template, not a licensing
blocker.
