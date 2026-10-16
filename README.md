# targetring

**A differential-expression result in, a Circos evidence figure out — built so
that it cannot quietly draw something wrong.**

Type a disease. The app finds a public expression study for it, picks a
comparison of affected against unaffected people, and draws what is known about
each gene that differs: druggability, safety, tissue specificity, drug pipeline,
literature, mouse phenotype. Every choice it made is shown and can be changed.
Click a gene for its full drug pipeline and the trials behind it.

![Crohn's disease, from an Expression Atlas contrast to a figure](docs/showcase/01-figure-from-a-disease.png)

*Crohn's disease. The app chose contrast `E-GEOD-101794 g2_g1` (n=61 vs 13),
took the top 60 genes by adjusted p, and drew the 12 selected. The centre
states the contrast, the sign convention and the selection rule. Each ring
has its own scale, in the column on the right.*

## Why it exists

This figure used to be built by hand, for a target-prioritisation meeting,
from six browser tabs and a spreadsheet. It took an afternoon per gene set, it
was never quite reproducible, and the hardest part was not the data — it was
being sure that an empty cell meant "nobody has measured this" and not "the
lookup silently failed". targetring automates the figure, and it was designed
around that one hard part.

## The one rule

> **A plausible wrong figure is the worst possible output.** Worse than a
> crash, worse than an empty ring, worse than a slow page.

Everything in the build is downstream of that. Concretely:

- **Missing is never low.** Every cell is one of four states — a value,
  `no_data`, `not_applicable`, `failed` — and the three non-values are drawn
  distinctly (hatched, stippled, red) and never filled, averaged or zeroed.
  Each gene carries *k*, how many rings it actually has, printed on the figure.
- **Join by identifier, never by position.** Open Targets drops genes it
  cannot find and returns HTTP 200 with a shorter array, which shifts every
  downstream cell by one. Every adapter builds `{id: record}` and looks up by
  the id it asked for.
- **Every gene is accounted for.** `input == rendered + ledgered` is asserted
  in code on every run. A gene that leaves the figure is named in the drop
  ledger with the reason. Counts are stamped on the figure.
- **The query must come back unchanged.** Europe PMC echoes the query; it is
  asserted byte-identical. One unbalanced quote once inflated a hit count 12×
  behind an HTTP 200.
- **When a check fails, the figure says so.** Seven invariants run on every
  render. A failure puts a banner on the figure itself — not a log line, not a
  toast — so a screenshot carries the warning.

![An invariant failure, on the figure](docs/showcase/02-invariant-failure-banner.png)

*A deliberately broken input. The figure renders, and it says not to trust it.*

## What is on the figure

Twelve rings, nine from external sources. No ranking, no score, no weights:
the spoke order is the order of the submitted table, stated on the figure, and
the rings are evidence for a reader to weigh, not a verdict.

| ring | what it is | source |
|---|---|---|
| logFC, −log10 adj p | the submitted measurement | yours |
| Tractability | family suggests druggable (3 keys) | Open Targets `prioritisation` |
| Chemical probe | a high-quality probe exists | Open Targets |
| Safety flags | safety event, essentiality, constraint, cancer driver | Open Targets |
| Regional specificity | region TPM ÷ median across 54 tissues | GTEx v8 |
| Furthest trial phase | for indications with genetic evidence linking them to this target | Open Targets |
| Trial activity now | live / dormant / stopped-for-cause | Open Targets clinical reports |
| TDL | Tdark → Tclin, how much is known | Pharos |
| Mouse phenotype | a lead, not a verdict; labelled "unverified" | Open Targets `mousePhenotypes` |
| Literature volume | mentions resolved to a gene id, not text-matched | PubTator3 |
| k | rings with a real value, of 9 | derived |

Two of those rows hide real decisions. *Furthest phase* requires genetic
evidence linking the drug's indication to the target, because an approved
multi-kinase inhibitor would otherwise set a ring to "approved" for every
kinase it happens to hit. *Trial activity* is three named states rather than
an ordinal, because only ~13% of trial terminations say anything about the
target — measured live over 1,433 BRAF reports — and a "discontinued" marker
would mislead on seven of every eight.

![The gene drawer for BRAF](docs/showcase/03-gene-drawer-braf.png)

*The drawer. 18 agents, each with its phase, status, indication and
categorised stop reasons. The safety liability is stated above the pipeline
because the clinical rings are a pipeline statement, never a safety one.*

## Filters are stated, not silent

Threshold filters can remove genes from the plot. When they have, the figure
says which filters, how many genes, and that it is not the whole submitted
set. A filter never drops a gene for being unmeasured.

![A filtered figure that says so](docs/showcase/04-filtered-and-says-so.png)

## How it was built

**Planned first.** [`PLAN.md`](PLAN.md) is the spec. Before the build, two
external reviews were commissioned against it — a scientific council and a QA
review, in [`docs/reviews/`](docs/reviews/) — and every BLOCKING finding
became a build requirement. Several of the plan's original choices were wrong
against the live sources (a GraphQL field that does not exist, a search
endpoint that returns other genes) and the reviews caught them before any code
did.

**Then built in versions**, each with a report: [`V1`](docs/V1-REPORT.md),
[`V2`](docs/V2-REPORT.md), then a sequence of measured, dated decisions:

- [RNA-seq only](docs/DECISION-RNASEQ-ONLY-2026-10-02.md) — microarray
  contrasts dropped after a probe-mapping investigation showed sign flips.
- [The app chooses a contrast by default](docs/DECISION-AUTOPICK-2026-10-09.md)
  — reversing an earlier refusal, with the reasons, and what must stay true.
- [A public demo, tabled](docs/TABLED-PUBLIC-DEMO-2026-10-09.md) — sized and
  costed, not built.

**With money gated.** Four LLM calls exist — gene triage, a phenotype summary,
a contrast chooser, a region chooser. Each has its own spend cap and counter
in an append-only ledger, reserve-before and commit-after. Each chooses only
from a closed list the app supplied, and the answer is verified against that
list on return. When a model call fails, the rule-based fallback runs and the
figure records *who actually chose*.

**Tested.** 1,418 tests. Several of them scan the executable code for the
thing they forbid — one reads the chooser's method body to assert it combines
nothing into a score.

## Stack

Python 3.13 · FastAPI + uvicorn · httpx · matplotlib + pyCirclize · pydantic
· SQLite cache · a few hundred lines of vanilla JS. Local only, by design.

## Status

Working, used, and still local. 18k lines, 133 commits, Sept–Oct 2026.
