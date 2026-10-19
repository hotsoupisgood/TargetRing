# Demo hosting plan

Builds on the measurements in `docs/TABLED-PUBLIC-DEMO-2026-10-09.md` (still
tabled — this doc does not un-table it; it answers "if it is picked up
again, what exactly happens" so that step doesn't have to be re-derived
either). Nothing in this document has been executed: no account created, no
DNS record touched, no process bound to a public interface.

## Scope statement (Puck's own words, kept verbatim)

> *"for my resume primarily or a proof a concept"* — so *"other people
> running it as a demo"* can see the figure without being walked through it.

Everything below is sized to that scope: a handful of visitors looking at a
handful of pre-rendered figures, not a service with real traffic. Nothing
here should be read as building toward more than that.

## The chosen option

**Read-only demo on the existing $6/mo droplet** — the first of the three
measured options in the tabled doc, and the only one actually costed and
fitting the stated scope.

| | |
|---|---|
| Box | Existing DigitalOcean droplet, 1 vCPU / 1 GB / Ubuntu 24.04 |
| Workload | Serve finished artefacts from `runs/` only — no analysis, no model call, no paid API route |
| Peak RSS measured | 133 MB (read-only serving), against 961 MB available |
| Cost | **$6/month — the box Puck already pays for. $0 incremental.** |

The two alternatives from the tabled doc are **not** chosen:

- Live analysis on a bigger box (2 vCPU / 4 GB, **$24/mo**) — rejected
  because the scope statement doesn't call for live analysis; a stranger
  waiting 69 s single-threaded for a demo page is not "proof of concept,"
  it's a bad first impression, and it would also need a rule 8 increase
  (a public box running `/api/analyse` is money spent per visitor).
- Host-the-site-here, process-on-the-Mac — never costed in the tabled doc
  because it needs an inbound path to this machine, which the tabled doc
  correctly flagged as a bigger rule 10 question than serving finished
  files from a droplet. Not reconsidered here.

If traffic or scope ever grows past "resume demo," re-cost from the tabled
doc's numbers rather than guessing — they're two weeks old as of this
writing and DigitalOcean's listed prices may have moved.

## What Puck must do himself

These are not automatable by an agent under the current rules, and
shouldn't be — rule 10 exists so this exact decision is made deliberately.

1. **Amend `AGENTS.md` rule 10.** Proposed text, his to edit or paste
   as-is:

   > *Amended 2026-10-18, Puck: a read-only demo of the Crohn's-only run
   > allowlist (`docs/DECISION-DEMO-CROHNS-2026-10-18.md`) may be served
   > publicly from the existing droplet under `TARGETRING_DEMO=1`, behind
   > the two-tier nginx config in `deploy/`. The full-functionality app
   > stays password-walled and local-analysis-only. No other route, box, or
   > run set is public under this amendment; widening it is a new
   > amendment, not an inference from this one.*

   Until this (or his own wording) is in `AGENTS.md`, nothing in `deploy/`
   should be started against a public interface.

2. **Generate the htpasswd file** for the full-functionality tier —
   `deploy/README.md` step 1 has the exact command. This is a credential;
   it is generated on the box, never committed, never pasted into chat or a
   repo file.

3. **DNS.** Point whatever hostname(s) he wants (e.g. a subdomain of an
   existing domain, or the droplet's bare IP for a first pass) at the
   droplet's existing IP. Not needed if the IP alone is acceptable for a
   resume link.

4. **Decide the two things `docs/TABLED-PUBLIC-DEMO-2026-10-09.md` still
   leaves open** beyond the disease question this round of work answered:
   whether the public tier shows the evidence surface/drawer and ledger, or
   figure-only (`docs/DECISION-DEMO-CROHNS-2026-10-18.md` leaves this
   unresolved on purpose), and the GTEx attribution sentence placement
   (`docs/LICENCES.md`).

## What is already prepared (this round of work)

- The Crohn's-only run allowlist, verified end-to-end locally
  (`docs/DEMO-RUN-ALLOWLIST.md`, `.env.example`).
- The nginx config for both tiers (`deploy/nginx.demo.conf`) and the exact
  command sequence to install it (`deploy/README.md`).
- The licence read PLAN §9 asked for (`docs/LICENCES.md`) — no blocker
  found, one attribution sentence to add to the page template.
- This decision record and cost table.

None of it is started. Starting step 2 of `deploy/README.md` (even on
`127.0.0.1`, for local verification) was done once, as part of
`docs/DEMO-RUN-ALLOWLIST.md`'s verification, and torn down immediately after.

## Go/no-go checklist

Before anything binds to a public interface:

- [ ] Rule 10 amendment is in `AGENTS.md`, in Puck's own words or the text
      above, committed by Puck (not by an agent).
- [ ] `deploy/README.md` steps 1–4 run successfully with real hostnames —
      confirm `curl` against `127.0.0.1:8900` still shows only the four
      allowlisted runs and 403s the three acting routes, same as the local
      verification in `docs/DEMO-RUN-ALLOWLIST.md`.
- [ ] GTEx attribution sentence (`docs/LICENCES.md`) is on the demo page
      template, not just in the manifest download.
- [ ] `nginx -t` passes and both `server_name` directives point at real,
      Puck-chosen hostnames (not the `*.example.invalid` placeholders in
      `deploy/nginx.demo.conf`).
- [ ] The demo app process (port 8900 in the example config) has confirmed
      no `ANTHROPIC_API_KEY` in its environment.
- [ ] Puck has looked at the four figures as a stranger would see them —
      no walkthrough, no prior context — and the captions read as honest
      about being Crohn's-only, one contrast, four variations.

Only after every box above is checked does step 5 of `deploy/README.md`
(DNS + public port) happen.
