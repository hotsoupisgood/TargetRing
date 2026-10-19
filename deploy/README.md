# Deploying the two-tier demo — steps Puck would run

**Nothing here has been run.** This is the documented sequence for
prerequisite 3 of `docs/TABLED-PUBLIC-DEMO-2026-10-09.md` (nginx basic auth),
written so it doesn't have to be re-derived. Prerequisite 4 — Puck amending
`AGENTS.md` rule 10 himself — comes **before** step 5 below, not after. An
agent must not do step 5 (or any step that exposes a port publicly) under
the current, unamended rule 10.

## 0. What you need decided first

- The hostname(s) for the demo and/or full tier (`docs/DEMO-HOSTING-PLAN.md`
  has the box-vs-cost options).
- Whether both tiers are wanted, or just the public demo. If only the demo:
  skip steps 2 and 4 and delete the second `server` block from
  `nginx.demo.conf`.

## 1. Generate the basic-auth credential file (not done here)

```sh
sudo apt-get install apache2-utils   # provides htpasswd, if not already present
htpasswd -c /etc/nginx/targetring.htpasswd <your-username>
# enter a password when prompted; -c creates the file, omit it to add a
# second user to an existing file
```

This file holds a bcrypt/crypt hash, not a plaintext password, but it is
still a credential — it must not be committed to the repo and must not be
readable by other users on the box (`chmod 640`, owned by `root:www-data` or
equivalent).

## 2. Start the full-functionality app process (password-walled tier)

```sh
cd ~/targetring
set -a; . ./.env; set +a   # ANTHROPIC_API_KEY etc, for this process only
.venv/bin/python -m uvicorn targetring.app:app --host 127.0.0.1 --port 8901
```

Run this under whatever process supervisor the box already uses (systemd
unit, supervisord, pm2 — not specified here; use what's already on the
droplet). Bind to `127.0.0.1` only — nginx is the only thing that should
reach it.

## 3. Start the demo app process (public tier) — no API key, ever

```sh
cd ~/targetring
unset ANTHROPIC_API_KEY   # explicit, in case the shell has one set
TARGETRING_DEMO=1 \
TARGETRING_DEMO_RUNS="20261009T160226Z-805ee9,20261009T013311Z-81aef2,20261009T012955Z-26960a,20261003T034753Z-8f54e7" \
  .venv/bin/python -m uvicorn targetring.app:app --host 127.0.0.1 --port 8900
```

The run id list is the Crohn's allowlist from
`docs/DECISION-DEMO-CROHNS-2026-10-18.md` / `docs/DEMO-RUN-ALLOWLIST.md`.
Also bind to `127.0.0.1` only.

**Verify the gate before touching nginx:**

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8900/api/analyse   # expect 403
curl -s http://127.0.0.1:8900/api/runs | python3 -m json.tool   # expect exactly the 4 allowlisted runs
```

## 4. Install the nginx config

```sh
sudo cp deploy/nginx.demo.conf /etc/nginx/sites-available/targetring.conf
sudo ln -s /etc/nginx/sites-available/targetring.conf /etc/nginx/sites-enabled/
# edit server_name in both blocks to the real hostname(s) first
sudo nginx -t          # syntax check before reload
sudo systemctl reload nginx
```

## 5. (Gate: requires the rule 10 amendment, prerequisite 4) Point DNS and open the port

Not detailed further here — this is the step that makes the box reachable
from the public internet, and `AGENTS.md` rule 10 is currently absolute
("nothing public ... no deploys, no public URLs"). Puck has to amend the
rule himself (`docs/DEMO-HOSTING-PLAN.md` has proposed amendment text) before
this step, or anything that opens an inbound public port, happens.

## Rollback

Both tiers are independent processes behind nginx `location`/`server`
blocks. To pull the public demo without touching the password-walled tier:
stop the port-8900 process and/or remove or comment out the first `server`
block and reload nginx. No data is deleted by any of this — `runs/`, `cache/`
and `.env` are untouched by starting or stopping either process.

## What this does NOT cover

- TLS/certificates — noted as a follow-up in `nginx.demo.conf`, not set up.
- Process supervision (systemd units, restart-on-crash) — use whatever the
  droplet already runs.
- Firewall rules beyond nginx itself (e.g. `ufw`) — out of scope for this doc.
