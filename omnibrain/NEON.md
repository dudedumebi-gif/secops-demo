# Neon Postgres for OmniBrain — state that survives Render spin-down

Short answer: Render Free still spins the web service down after
~15 minutes idle, so the first visit after idle still wakes slowly.
What Neon changes is that your data survives that spin-down and any
redeploy. Without Neon, the Render Free filesystem is ephemeral and
the app reseeds empty. With Neon, the database lives outside Render,
so edits, backlog items, logged incidents, and the threat-intel cache
persist.

Locally, nothing changes unless you set DATABASE_URL: without a
postgres DATABASE_URL the app uses local SQLite (`omnibrain.db`)
exactly as before, with nothing to install.

## How the code chooses a database

- `DATABASE_URL` starting with `postgres://` or `postgresql://`
  -> Postgres, via `psycopg[binary]` in `requirements.txt`, imported
  lazily only on that path. A SQLite run never requires psycopg.
- Anything else, including DATABASE_URL unset -> local SQLite.
- One small adapter in `server.py` translates the app's SQLite
  dialect for Postgres: `?` placeholders become `%s`, `INTEGER
  PRIMARY KEY AUTOINCREMENT` becomes `SERIAL PRIMARY KEY`, and
  INSERTs gain `RETURNING id` so `lastrowid` still works. Handlers
  are not duplicated. Seed logic is unchanged: seed only when tables
  are empty, same rows, same KPIs.

## Your dashboard steps (this is your work, not done by the agent)

1. Neon dashboard > New Project. Name it `secops-demo`, pick a
   region, create it. Do not reuse a project from another product;
   this demo gets its own Neon project.
2. In the Neon project, copy the pooled connection string. It must
   include `sslmode=require` — Neon pooled strings do. It looks
   like this placeholder only, never a real value:

   ```
   postgresql://user:password@host/db?sslmode=require
   ```

   Never commit that string, never paste it in chat, never put it
   in this repo. It lives only in the Render dashboard (and, for a
   local Postgres test, in your own shell environment).
3. Render dashboard > the `omnibrain` service > Environment > add
   `DATABASE_URL` with that string, then redeploy. `render.yaml`
   already declares `DATABASE_URL` as `sync: false` and runs
   `pip install -r requirements.txt` at build time, so the driver
   is present on Render. Render injects `PORT` itself; `HOST` is
   `0.0.0.0` in `render.yaml`.
4. Verify state survives spin-down — this is the proof, and it is
   different from the old SQLite-on-Render behaviour:
   a. Open the Render app. KPIs should read 79% fidelity, 38.0 min
      MTTR, 66% coverage (the seed values).
   b. Add a test backlog item in the app.
   c. Restart or redeploy the Render service (or just wait for it
      to spin down after ~15 minutes idle, then open it again).
   d. Confirm the test item is still there. If it is, Neon is
      holding state. If the app reseeds empty instead, DATABASE_URL
      was not set on the service (it fell back to SQLite) — check
      the Environment tab, not the code.

## Local use with Postgres (optional)

```bash
cd omnibrain
DATABASE_URL='postgresql://user:password@host/db?sslmode=require' python3 server.py
```

That shell value is yours only; it is not stored in the repo, and
`.gitignore` covers `.env` if you prefer a local env file. Do not
run `python3 server.py --reset` against Postgres expecting a wipe:
`--reset` is ignored on the Postgres path on purpose — it keeps
existing data and only seeds empty tables. To start a Postgres
database over, drop and recreate the tables in the Neon dashboard.

## Limits, stated plainly

- Live Neon verification is still pending: the Postgres path in
  this repo is code-tested with a stub cursor (see the parent
  handoff), but no live Neon database was available in the build
  sandbox, and no Neon project was created for this demo. Do not
  treat a Render deploy as proven until step 4 above passes on
  your own Neon project.
- Render Free still sleeps. Neon fixes data loss, not wake delay.
- The Render URL is still public by default and this app has no
  login; local-only remains the private mode. The data here is
  simulated, which is why a public demo URL is acceptable at all.
- The LLM hook is still unwired. Wiring gpt-4o-mini online is a
  separate, later step, after these changes are finished.
