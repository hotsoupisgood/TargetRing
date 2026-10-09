# Tabled — a password-walled app with a public read-only demo

**Raised:** 2026-10-09, Puck. **Status: tabled.** Nothing here is approved and
nothing here is built. **Rule 10 is unchanged and still in force:** nothing
public, local only, no deploys, no public URLs, no data pushed anywhere.

> *"i could passowrd protect the actuall functionality and do an example page
> that uses cached stuff for demo"* — and then *"I want to table this."*

This document exists so the design does not have to be re-derived. It records
the shape, the measurements, and the four things that would have to be true
first. If it is picked up again, start at **Prerequisites**.

## Why it came up

The purpose was never multi-user service. Puck's words: *"for my resume
primarily or a proof a concept"*, so *"other people running it as a demo"* can
see the figure without being walked through it. That goal is satisfied by
showing finished figures. It does not require strangers to run analyses.

## The shape

Two tiers behind one nginx on the existing droplet:

1. **Full functionality, password-walled.** nginx basic auth in front of
   everything. Auth belongs in nginx, not in the app: the app has no user
   model, no session handling and no login surface, and adding one to gate a
   personal demo is a larger change than the demo is worth, with more ways to
   be wrong.
2. **A public read-only demo.** A small allowlist of pre-computed runs, served
   as finished artefacts. No analysis, no paid calls, no model in the request
   path.

The split is enforced by a `TARGETRING_DEMO=1` environment flag that makes the
three acting routes return 403 — `/api/analyse`, `/api/autopick`,
`/api/region-for`. Those are the three that spend money (rule 8) and the first
is also the one that would eat the box.

## The demo depends on `runs/`, not `cache/`

Puck said "cached stuff", and caching is **not** the mechanism here. The two
are different things and only one of them is useful for this:

- `cache/` (251 MB) holds upstream payloads and model answers so a *new* run is
  cheaper. Serving from it still means running an analysis.
- `runs/` (863 MB across 282 runs, 5.3 MB each) holds finished artefacts —
  figure, legend, values, caption, ledger, manifest. Serving one is a file
  read.

A demo wants the second. It is what makes the box sizing trivial and what makes
"no paid calls" true by construction rather than by hope.

## Measured sizing

Taken on this Mac, 2026-10-09:

| Workload | Peak RSS | Time |
|---|---|---|
| python + imports, idle | 78 MB | — |
| heatmap: parse 67 MB TSV + render | 303 MB | — |
| 60-gene full analyse | 385 MB | 69.3 s |
| read-only serving existing runs | 133 MB | 1–2 ms per view |

The droplet is `45.55.123.140`, 1 vCPU / 961 MB / Ubuntu 24.04, up ~2 years.

**Read-only fits the existing $6 box with room to spare.** 133 MB against
961 MB, and a view is a file read.

**Live analysis does not.** 385 MB peak on a 961 MB box with no swap headroom
is an OOM kill under any concurrency, and 69.3 s single-threaded on 1 vCPU is
not a demo anyone waits through. DigitalOcean pricing fetched the same day:
1 GB $6, 2 GB $12, 2 vCPU/4 GB $24, CPU-optimised 2 vCPU/4 GB $42. If live
analysis is ever wanted, **2 vCPU / 4 GB at $24/mo** is the honest entry
point — and it is a rule 8 increase, so it needs Puck's explicit OK.

The third option discussed — host the site there, run the processing here — was
not costed. It needs an inbound path to this Mac, which is a larger rule 10
question than hosting finished files.

## Prerequisites

All four, before any deploy:

1. **A curated run allowlist.** `/api/run/{run_id}/{filename}` currently
   resolves any run id. Unwalled, that is ~280 runs of work reachable by
   guessing ids. The demo serves an explicit list and 404s everything else.
2. **The `TARGETRING_DEMO` flag**, with tests asserting the three acting routes
   403 when it is set. A flag that is only believed to work is not a gate.
3. **nginx basic auth** over the full-functionality tier.
4. **A rule 10 amendment**, written by Puck in `AGENTS.md`. Rule 10 is
   currently absolute. A deploy under an unamended rule 10 is a violation
   however good the reasons are, and the rule is there precisely so this
   decision gets made deliberately rather than inferred by an agent mid-task.

## What is not resolved

- Whether the demo runs are Crohn's only, or span regions and diseases. A
  single-disease demo is easier to caption honestly; a spread shows more.
- Whether the public tier shows the evidence surface and the ledger, or only
  the figure. Showing them is more of the point of the app; it is also more
  surface to get wrong in front of strangers.
- Licensing of the displayed upstream data. The HPA licence read (PLAN §9) is
  still outstanding and it bears directly on what may be shown publicly.

## Related

- `AGENTS.md` rule 8 (money, per-account caps) and rule 10 (nothing public)
- `docs/DECISION-AUTOPICK-2026-10-09.md` — the three paid routes this flag
  disables, and why each exists
