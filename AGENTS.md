# AGENTS.md — targetring

A web app for target prioritisation. Input: a differential-expression result
(gene symbol, logFC, adjusted p) plus a body region. Output: a Circos figure
where the circle is the submitted gene set in a stated order and each ring is a
heatmap track;
clicking a gene opens a drawer with its drug pipeline and its literature.
Puck (Lucas Henry) owns it. It reproduces, automatically, a figure he used to
build by hand at work.

`PLAN.md` is the spec and the source of truth. The two reviews in
`docs/reviews/` are binding, not advisory — they were commissioned before the
build and every BLOCKING item in them is a build requirement.

## The one rule that matters

**A plausible wrong figure is the worst possible output.** It is worse than a
crash, worse than an empty ring, worse than a slow page. Everything below is
downstream of that.

So: never impute a missing value, never zip arrays by position, never branch on
HTTP status alone, never let a gene disappear without appearing in the drop
ledger. When an invariant fails, render the banner on the figure — do not
produce a clean-looking chart.

## Hard rules

1. **Missing is not low.** Every ring has four states: value, `no_data`,
   `not_applicable`, `failed`. `no_data` renders as hatched grey and is never
   filled, averaged, zeroed or imputed. Each gene carries *k* = how many rings
   it actually has, printed on the figure.
2. **Join by identifier, never by index.** Open Targets `targets()` drops genes
   it cannot find and returns HTTP 200 with a shorter array, which silently
   shifts every downstream spoke (`docs/reviews/QA-v0-plan.md` B2). Build
   `{id: record}` and look up by requested id.
3. **Every gene is accounted for.** `input == rendered + ledgered`, asserted in
   code. The drop ledger names each gene and why. Counts are stamped on the
   figure.
4. **Verify the query came back unchanged.** Europe PMC echoes your query in
   `request.queryString` — assert it byte-identical; one unbalanced quote
   inflated a hit count 12× with HTTP 200. Treat an `ERROR` key anywhere in an
   NCBI response as a hard failure whatever the status code.
5. **Resolve identifiers once, before any ring.** Symbol → Ensembl (with the
   exact GTEx gencode version) → UniProt → ChEMBL, with explicit `ambiguous`
   and `no_match` states. A wrong gencode version returns empty and looks like
   legitimate absence.
6. **The rodent result is a lead, not a verdict.** Three levels only:
   `asserted` / `asserted-negative` / `none found`, labelled "abstract asserts
   target manipulation altered the phenotype (unverified)". Show the sentence
   and the PMID. Never fold it into the score.
7. **No ranking at all** (v2.5, Puck's decision — PLAN §3). This rule used to
   read "no weighted sum as the headline: rank within the submitted set, report
   the Pareto non-dominated set as the headline and the weighted score as a
   labelled convenience". The Pareto mechanism kept reproducing the defect it
   was prescribed to fix (COUNCIL V2, V10), so all of it is gone: no front, no
   score, no weights, no eligibility, no stability flag. The spoke order is
   **decided, stated on the figure and checked in the artefact**, and the
   default is the order of the submitted table. Threshold filters may remove
   genes from the plot, and then the figure says so.
8. **Money.** Four paid calls, four separate caps with their own counters:
   LLM triage (`LLM_SPEND_CAP_USD`, top 10 genes or on click, never a whole
   200-gene list), the phenotype summary (`PHENOTYPE_LLM_SPEND_CAP_USD`, one
   call per gene, summarises only the table it sits above), the contrast
   chooser (`CHOOSER_LLM_SPEND_CAP_USD`, one call per disease, choosing only
   among contrasts the index already offered it) and the region chooser
   (`REGION_LLM_SPEND_CAP_USD`, one call per disease and then **cached**,
   choosing only among the GTEx tissues this build can read). None borrows from
   another. All separate from ytpt's ledger.

   **A cap is an approval gate, not a ceiling.** It stops the call, names the
   numbers and records the refusal; raising it is Puck's to do and he expects
   to be asked. So: never spend past a cap, and never treat a cap as a reason
   the work cannot happen — report the number and ask.
   *Amended 2026-10-09 twice, Puck: "there is no cap. i just want to ok
   increases" (a third paid call, the contrast chooser), then a fourth for the
   region chooser — "handled by an llm call by simply using general knowledge
   on where the disease is located. we cache the results". Amended 2026-09-29, Puck on WhatsApp;
   previously "the LLM triage is the only paid call".*
9. **Rate limits are unmeasured.** Cap concurrency at 4 per host. Treat any 429
   as a hard run failure — never a retry loop.
10. **Nothing public.** Local only. No deploys, no public URLs, no data pushed
    anywhere.

## Every run writes a manifest

UTC timestamp, git SHA, every source URL and schema version, model id and
prompt hash, and PASS/FAIL per invariant — written next to the figure, so a
screenshot is self-describing. A morning screenshot that cannot be audited is
not a deliverable.

## What is not claimed

Overnight work produces a figure that is internally consistent and auditable.
It produces no ranking, score or recommendation of any kind (v2.5). Triage
precision is unmeasured until a human labels a gold set. Say what the figure is
on the figure, not only in the report — including the spoke-ordering rule and
any active filter, because a reader must not mistake a filtered plot for the
whole submitted set.

## Talking to Puck

Brief and direct, numbers with units, uncertainty stated. Lead the report with
what is broken, then what works. Separate "what I did" from "what you need to
decide", each on its own line. No play-by-play.
