# Turso — the chosen stateful option for Render (replaces Neon)

Neon was ruled out: the user's free projects are used up, so it is
not available for this app. Turso is now the stateful option.
The Postgres adapter in `server.py` stays as a fallback for any
`postgres://` provider — it is not the chosen route, and no code
change is needed to keep it.

## How it works in this app

Engine priority, decided at startup and nowhere else:

1. `TURSO_DATABASE_URL` set (libsql:// form) + `TURSO_AUTH_TOKEN`
   set -> Turso remote, direct mode only:
   `libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)`.
   No embedded replica (no `sync_url`, no local file synced to
   Turso): on Render a local file is ephemeral and would confuse
   the state story. Remote direct mode is stateless — the database
   lives in Turso, not on Render's disk.
2. Else `DATABASE_URL` starting with `postgres://` -> Postgres
   (existing adapter, see `NEON.md`).
3. Else -> local SQLite `omnibrain.db`, exactly as before.

Turso WINS over Postgres if both are set on Render, so leave
`DATABASE_URL` empty on the Render service unless you are
deliberately switching away from Turso.

If `TURSO_DATABASE_URL` is set but `TURSO_AUTH_TOKEN` is missing,
the server exits at startup (exit code 2) with a clear message.
It never silently falls back to SQLite on Render — silent fallback
would reseed your data instead of using Turso.

`--reset` is ignored on Turso (same rule as Postgres): it never
drops the remote database; seed only fills empty tables.

The app never prints `TURSO_DATABASE_URL` or `TURSO_AUTH_TOKEN`.
The token is a per-database secret: Render dashboard env vars
only, never committed, never pasted in chat, never in a README.

## Your dashboard steps (not done yet — this is your part)

1. Turso: create a database named `secops-demo`.
   With the Turso CLI (or the dashboard equivalent):
   ```bash
   turso db create secops-demo
   turso db show secops-demo --url
   turso db tokens create secops-demo
   ```
   The URL must be the libsql:// form, e.g.
   `libsql://your-db.turso.io` (placeholder — your real URL will
   have your database and organisation names in it).
2. Render dashboard > `omnibrain` service > Environment > set
   `TURSO_DATABASE_URL` (the libsql:// URL) and `TURSO_AUTH_TOKEN`
   (the token from step 1), then redeploy. `render.yaml` already
   declares both as `sync: false`, so they must come from the
   dashboard, never from the repo.
3. Proof test (this is the live verification — it has NOT been
   run yet, because no Turso database or token exists here):
   open the Render app, confirm KPIs 79 / 38.0 / 66, add a test
   backlog item, then restart/redeploy the service (or wait out
   the ~15-minute Render spin-down) and confirm the item is still
   there. If the app reseeds empty instead, the Turso variables
   were not set on the service and it fell back to SQLite.

## Caveats — read before relying on this

- Render Free still spins down after ~15 minutes idle. Turso
  fixes the data loss, not the wake delay: the first visit after
  idle still wakes slowly, but with your data intact.
- Turso free databases archive after 10 days of inactivity
  (per earlier research — check Turso's current docs for the
  exact unarchive step before relying on a long-idle demo).
  An archived database will not answer until you unarchive it.
- Free quotas (earlier research — check Turso's current pricing
  page before relying on them): 5 GB storage, 100 databases,
  500M rows read and 10M rows written per month. This demo's
  seeded workspace is far below those limits; unindexed
  full-table scans count as rows scanned, so keep queries as
  they are (they are all small, indexed-by-id lookups).
- A Render URL is public by default and this app has no login —
  local-only remains the private mode. The demo data is
  simulated, which is why a public demo URL is acceptable at all.
- Local runs are unchanged: with both Turso variables unset,
  the app uses local SQLite and needs nothing installed
  (`libsql` is imported lazily, only on the Turso path;
  `pip install -r requirements.txt` on Render installs it).

## Troubleshooting

- Startup exits with "TURSO_AUTH_TOKEN is missing": the URL is
  set but the token is not. Set the token in Render (step 2) or
  unset the URL to use local SQLite.
- App reseeds after redeploy: the Turso variables are not set on
  the Render service (check Environment, not `render.yaml` —
  `sync: false` means Render does not read them from the repo).
- App loads but queries fail after a long idle period: the Turso
  database may be archived — unarchive it in Turso, then reload.
