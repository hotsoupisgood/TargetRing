# GO / NO-GO rule — Puck's spec (2026-10-18)

Owner's words, WhatsApp 10:26–10:28 PDT: "make a recommendation, have go/no
go: we need the gene to be expressed, and also misregulated in the study, and
have a strong phenotype in a study." Then: "no toxicity data or low
druggability does not exclude. this could be fixed by consulting a chemist."

## The rule

Verdict from exactly three criteria. Each is **met / not met / no data** with
its value shown.

| # | criterion | met when | source |
|---|---|---|---|
| 1 | expressed | GTEx region median TPM ≥ `RECOMMEND_MIN_TPM` (1.0); if GTEx absent, tissue-distribution ring > 0 | GTEx, Open Targets prioritisation |
| 2 | dysregulated in the study | abs(logFC) ≥ `RECOMMEND_MIN_ABS_LOGFC` (1.0) and q ≤ `RECOMMEND_MAX_Q` (0.05) | the submitted DE table |
| 3 | phenotype in a study | mouse-KO ring > 0 or ≥ `RECOMMEND_MIN_PHENOTYPES` (1) mousePhenotypes terms | Open Targets |

- **GO** if all three met.
- **NO-GO** if any is not met.
- **INSUFFICIENT DATA** if none is not met but any is no data.

## What never affects the verdict

Safety / toxicity rings and druggability rings (and clinical stage, drug
count, literature volume) are **not criteria**. Low druggability or a
toxicity flag is shown as a **caveat line** under the verdict, never as an
exclusion:

> Caveat: low druggability / safety flag — not an exclusion; consult a
> medicinal chemist.

No data on toxicity is likewise not an exclusion and gets no caveat.

## Modality note (owner, 10:28 PDT: "note if it's downregulated, a biologic may be preferred")

Direction of the study effect sets a modality line under the verdict, not a
criterion:

- logFC < 0 (down-regulated in the contrast) → "down-regulated in the study:
  a biologic may be preferred". That sentence, no sub-types.
- logFC > 0 (up-regulated) → hypothesis is inhibition; no modality line.

The LLM summary states the direction and, when down-regulated, this one
sentence. Druggability rings still do not change the verdict.

## Audience (owner, 10:29 PDT)

The reader is a biologist, not a genetics specialist or a drug-development
expert. Plain terms, no
modality sub-types, no genetics jargon; name the evidence family and the
value and stop. This applies to the summary, the verdict box and the
geneticist walkthrough feedback.

## Deterministic now, LLM call in the final version

Owner, 10:28 PDT: "have this be an llm generated call in the final version
but precompute here." So:

- **Demo (now):** the verdict is computed by the deterministic rule above and
  baked. No model call at serve time.
- **Final version (live runs, not demo):** the verdict is an LLM call that
  receives the same three criteria with their values and thresholds, the
  caveat inputs, and this rule text, and returns verdict + one-paragraph
  reasoning in a fixed JSON schema. The deterministic rule still runs and is
  shown beside it; any disagreement is displayed, never hidden. Pinned model,
  prompt hash as `rule_version`, temperature 0 where accepted, inputs stored
  with the output. It is one of the paid routes under AGENTS.md rule 8 and
  is disabled in demo mode like the other LLM routes.
- Build the code path now behind a flag (`RECOMMEND_MODE=rule|llm`, default
  `rule`), with the LLM path tested against a patched client only; the bake
  for the four demo runs may use the LLM path once to produce the baked
  verdicts (cost cents, owner's API credits), but the baked JSON must carry
  both the rule verdict and the LLM verdict.

## Display aids added after the geneticist walkthrough (2026-10-18, later)

None of these changes the rule, its thresholds, its `rule_version` or a
single verdict. They change what is printed beside the verdict.

### The region is named, and judged as a disease tissue or not

The expressed line reads "`<TPM>` TPM in `<region>`, the run's chosen tissue"
and then one of:

- "(a disease tissue for this contrast)" — the run's manifest carries the
  `region ... was inferred from the disease` run condition, i.e. the region
  chooser picked it for this disease;
- "— not a disease tissue for this contrast" — the region is the form's
  silent default (`run.DEFAULT_REGION`, Lung) and nothing says it was chosen
  for this disease;
- nothing — the region was chosen by hand; no claim is made either way.

The four original demo runs were judged against Lung because the form default
went through unnoticed. `/api/analyse` on the contrast path now refuses an
empty region unless the disease's chooser answer (cached or made now, on the
`region` account) supplies one, and then stamps `region_inferred` with the
chooser's reason. The paste path keeps the default.

### Two TPMs, not a fold

The expression sentence is "`<TPM>` TPM in `<region>`, the run's chosen
tissue; median across `<n>` tissues `<TPM>`". No "x the GTEx median", and no
"specific", "enriched" or "concentrated" — unless region TPM / panel median
exceeds `RECOMMEND_SPECIFIC_RATIO` (5.0, `config.py`) **and** the region is a
disease tissue as above **and** neither GTEx whole blood nor spleen is at or
above the region's TPM (the immune-origin hint below). Why: a fold against a
panel median reads as tissue-specific for anything blood-borne in a blood-rich
tissue ("38.5x Lung" for a neutrophil gene; CXCL1 is 11x the panel median in
the ileum with a spleen TPM ten times the ileum's). The summary prompt carries
the same rule.

### Disease-area match beside the phenotype criterion — a display aid

The owner's rule stands: **any** abnormal mouse term meets the phenotype
criterion. Beside it the box prints "disease-area match: yes/no", computed by
`recommend.DISEASE_AREAS`: the contrast's text (Atlas name + test-group
label) selects a disease area, and each abnormal term label is matched by
keyword to that area's Mammalian Phenotype top-level buckets. Labels, not
ontology ids, because Open Targets' `mousePhenotypes` rows do not carry the
top-level class.

| disease area | contrast text contains | MP buckets (term label contains) |
|---|---|---|
| inflammatory bowel disease | crohn, colitis, inflammatory bowel, ibd | **digestive/alimentary system**: intestin, colon, colitis, ileum, bowel, gut, digest, gastr, enteric, crypt, paneth, goblet, stomach, duoden, jejun, rectum, cecum, peyer, villus, mucosa, absorption, feces, diarrhea, gallbladder, gallstone, bile, liver, hepat, pancrea, esophag, saliva, tooth · **immune system**: immun, lymph, leukocyte, monocyte, neutrophil, macrophage, granulocyte, t cell, b cell, thymus, spleen, cytokine, interleukin, antibody, immunoglobulin, nk cell, dendritic, eosinophil, basophil, mast cell, autoimmun, complement, interferon · **inflammation**: inflamm, arthritis, dermatitis, vasculitis, hepatitis, nephritis, sepsis |

Printed as "disease-area match: yes (inflammatory bowel disease) — term
[bucket]; …" or "disease-area match: no — met on *n* term(s), none in the
disease area". A contrast matching no row prints "no mapping for this
contrast". The mapping's hash is baked as `mapping_version`.
**It is a display aid, not a criterion**: it never moves the verdict and is not in
`rule_version`. The full pattern lists are the code (`recommend.DISEASE_AREAS`).

### The summary's two reconciling sentences

- When the contrast is in a mapped disease area (an inflamed-tissue biopsy)
  **and** the gene's GTEx whole-blood or spleen median TPM is ≥ the
  expression floor and ≥ the region's TPM (`recommend.immune_origin_hint`,
  rule printed with the answer), the summary must say: "the change may
  reflect infiltrating immune cells rather than regulation in the tissue".
  The hint reads the two TPMs the drawer bakes (`expression.whole_blood_tpm`,
  `expression.spleen_tpm`); when neither is baked, nothing is said.
- When the low-druggability caveat and a furthest-stage agent are both
  present, the summary must reconcile them: the caveat reads small-molecule
  records only; the agent is named with its modality and indications, and
  "not a `<disease>` lead" when none of its indications is the study's
  disease (MUC1: tecemotide, a cancer vaccine, Phase 3).

The prompt also forbids raw key names (hasPocket, Tbio, geneticConstraint…)
and gives the plain phrase for each. Biologist audience, as above.

## Placement and hygiene

- Verdict line first in the drawer, then the three criteria, then the
  caveat line when triggered, then the rule text with thresholds and
  "not used to order or filter the figure". (It also led the row-hover
  summary panel until 2026-10-20, when that panel was removed.)
- Baked into `runs/<id>/drawer/<GENE>.json` as `recommendation` with
  thresholds and `rule_version` (hash of this rule). Demo serves it static.
- PLAN.md v2.5: rings are evidence, not a score. This verdict is the owner's
  explicit rule, labelled as such; it never changes ordering, filters or the
  drop ledger.
- The LLM evidence summary's first sentence states and agrees with the
  verdict; it may mention the druggability/safety caveat only as a caveat.

## Trim and the Claude-coded phenotype flag (owner, 2026-10-18 18:42 PDT)

Owner's words: "can remove druggability caveat. and make it much less wordy.
or remove the word expression altogether as the information is in the
graph. i would like an explanation on the phenotype though, somewhere, like
a 0/1 for phenotype that is claude coded."

- **Remove the druggability caveat line** from the verdict box and from the
  summary's reconciliation sentence. Safety flags stay silent too unless a
  severe liability is on record (one short line).
- **Much less wordy.** The box shows: verdict pill; three criteria as
  `met / not met / no data` with the bare value, no prose; the expression
  line reduced to `expressed: yes/no (value)` or dropped entirely when the
  figure already shows the ring (owner's preference: drop); the rule text
  collapses to one line with a tooltip for thresholds.
- **Phenotype 0/1, Claude-coded.** A baked field `phenotype_llm` per gene:
  `{flag: 0|1, reason: one sentence, model, prompt_version, inputs}`. The
  prompt gets the gene's mouse phenotype terms (and knockout score) plus the
  contrast's disease area and asks: "Is there a model-organism phenotype
  that a biologist would read as evidence this gene matters for this
  disease area? Answer 1 or 0 and one plain sentence naming the term(s)."
  Grounded on the facts only. Shown in the box as `phenotype (Claude): 1 —
  <sentence>`; it is a display aid next to the owner's deterministic
  criterion, never the criterion, until the owner says otherwise. Paid
  route under rule 8: its own spend account and cap; baked for the demo,
  disabled live in demo mode.

### Implementation notes (2026-10-18, built)

**What the box prints now**, in order: the verdict pill with a three-word
region tag beside it (`region: ileum, inferred` — `recommend.region_tag`, the
long sentence in its `title`); the three criteria as `✓ / ✗ / ~`, the label,
`met / not met / no data`, and `criterion.bare` — the value and its unit and
nothing else (`19.06 TPM`, `logFC +3.1, p 2.2e-22`, `6 abnormal terms`), with
`criterion.shown` in the `title`; on the phenotype line, `disease-area terms:
n` with the matching terms and their buckets in the `title`; then the
`phenotype (Claude)` row; then the modality line when logFC < 0; then
`rule_short` on one line with `rule_tooltip` (thresholds and rule version) in
its `title`.

**What left the box**: the expression sentence and both TPMs (the owner's
preference — the specificity ring on the figure carries them and the drawer is
one click away), the disease-area sentence, the druggability caveat and the
`hasSafetyEvent` caveat. `criterion.shown`, `expression.shown`, `rule_text`,
`disease_area.shown` and `caveats` are all still computed and still in the
baked JSON: the summary prompt and the drawer's long form read them, and a
trimmed box must not mean a trimmed record. `rule_version` is unmoved and every
verdict is unmoved — re-stamped and counted after the change: 5 / 5 / 1,
5 / 6 / 2, 5 / 5 / 1, 5 / 5 / 1, exactly as `docs/DEMO-RUN-ALLOWLIST.md` has
them.

**The one safety line that survives**: `recommend.caveats` fires only on the
drawer's `severe_liabilities` rows — `Safety: n severe liability record(s) --
not an exclusion.` A bare ring flag is silent, because Open Targets emits
`hasSafetyEvent` for anything on record and "a safety flag is on record" under
a GO was a line the reader could not act on.

**`phenotype_llm`** (`targetring/phenotype_llm.py`,
`scripts/bake_phenotype_flags.py`). Baked per gene as
`{flag, reason, model, prompt_version, effort, generated_at, inputs,
facts_key, usage, stop_reason, is_criterion: false, note}`. The model is given
only the gene's mouse phenotype term labels, the knockout ring's state and
value, and the contrast's disease area — not the verdict, not the logFC, not
the expression, because a model given the verdict agrees with the verdict
instead of reading the terms. Output is an `output_config.format` JSON schema
(`{flag: 0|1, reason: string}`) and is still validated on arrival: a bool, a
flag outside {0, 1}, an empty sentence, prose instead of JSON, or a sentence
over 300 characters is a refusal, not a guess. One call per distinct
(facts, prompt, model) across all four runs; more than 20 distinct prompts go
through the Message Batches API. Spend account `phenotype_llm`, cap
`config.PHENOTYPE_FLAG_SPEND_CAP_USD` = $1.00, separate from the
phenotype-PAGE summary's `phenotype` account (the two names are one word apart
on purpose; see the note in `config.py`). `bake_phenotype_flags` raises under
`TARGETRING_DEMO=1` before any call: baked only, never live.

**The flag is not the criterion.** `recommend.py` does not import it, mention
it or see it; no module in `targetring/` imports `phenotype_llm` at all, only
the bake script does; the app serves the baked block through the drawer file
route it already had. Held in `tests/test_trim.py`.

**Rule 8 amendment for Puck to make.** Rule 8 names four paid calls. There are
now six accounts: the two added on 2026-10-18 are `summary` (the baked evidence
summary) and `phenotype_llm` (this flag), both spent offline by a script and
never by the app, each with its own cap and counter and neither borrowing from
another. The amendment is Puck's; the code already behaves as if it were made.

**The summary prompt** is 4 to 6 sentences: verdict first, `Hypothesis:`
second, one or two sentences on the evidence families that are present, the
required infiltrating-immune-cells sentence when the hint fires, and the absent
evidence last. No druggability sentence, no reconciliation sentence, no closing
"what would change this picture" sentence. A prompt change re-buys every
summary, so the four runs were re-baked.

## Which model each phenotype term came from (2026-10-18, later)

Puck: *"can you state which model each term came from"*. The drawer carried
`mouse_phenotypes`, a flat list of term labels, so a reader could see *that*
MGI curates a term for this gene but never in which mouse.

**Baked.** `phenotype_terms`, one entry per TERM:
`{label, mp_id, models: [{id, allelic_composition, genetic_background,
pmids}]}` — `phenotype.terms_with_models`, over the same first
`phenotype.DRAWER_TERMS_LIMIT` (25) `mousePhenotypes` records the flat label
list is read from, so the two cannot disagree about which terms a gene has. A
label Open Targets lists against several models appears **once**, with every
model under it; a model repeated under one label is listed once. A term with no
`biologicalModels` record keeps `models: []` — MGI naming no model is a
different fact from the phenotype not existing, and an empty list says so.
The PMIDs stay the **model's** literature: the (phenotype, model) pair is the
one `build_table_rows` emits, and the join onto papers that
`phenotype.build_table_rows`' docstring refuses is still refused here.

**Nothing reads it but the page.** The flat `mouse_phenotypes` list is
unchanged and is still the only phenotype input to `recommend.py`
(`facts_from_drawer` → `Facts.mouse_phenotypes`, the phenotype criterion and
the disease-area display aid) and to `phenotype_llm.build_facts`. So the
prompt hash, the `facts_key`, every flag and every verdict are unmoved, and no
paid call was re-bought to add this.

**Shown.** In the drawer's *mouse phenotype lead*, each term lists its models
underneath in small type — model id, allelic composition, genetic background,
and each PMID as a link to PubMed. In the verdict box, the phenotype line's
`title` gains one `term — n model(s)` line per term, and `models: n` (distinct
models across the gene's terms) sits beside the `phenotype (Claude)` sentence,
which is itself unchanged. A drawer baked before this field exists shows the
bare labels and says so rather than implying no models.

**Added to the four demo runs** with
`scripts/bake_drawers.py --phenotype-terms-only <run_id> ...`: one free Open
Targets query per run, the one key written back, the baked `summary`,
`phenotype_llm` and `recommendation` left exactly as they were. A full
`--force` re-bake would have re-bought both paid blocks, which is why that
pass exists. Held in `tests/test_phenotype_models.py`.
