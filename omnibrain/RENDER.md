# Render review — can it work for OmniBrain + SOC demo?

Short answer: yes for a demo URL, with one big caveat — **Free-tier
SQLite is not durable.** Do not treat a Render URL as private just
because the GitHub repo is private.

## What Render Free gives you (demo-appropriate)

- Web service: ~0.1 CPU, 512 MB RAM, 750 hours/month on Free.
- The service spins down after ~15 minutes idle and takes roughly
  a minute to wake on the next request. That is fine for a demo link,
  annoying for always-on use.
- A static site for `soc-demo/` works cleanly: it is a single HTML
  file, saves nothing, and has no database to lose.

## The SQLite caveat — read this before deploying

- Render Free has an **ephemeral filesystem**: files written at
  runtime (including `omnibrain.db`) are lost on spin-down, restart,
  and redeploy. The app reseeds from its built-in seed on first run,
  so it will still work — but your edits, backlog additions, logged
  incidents, and threat-intel cache will not survive.
- That is acceptable for a demo, not for durable work.
- To make SQLite durable you need a paid plan: Starter is about
  $7/month plus a persistent disk at about $0.25/GB/month, mounted
  for the database. The alternative is a Postgres migration, which
  is not in this task — it would need a non-stdlib database driver
  and a schema change, and is not free-tier durable either, because
  free Postgres expires after 30 days.

## Public by default — private mode is local-only

- A Render URL is public to anyone who has it, and this app has
  no login. Local-only (`HOST=127.0.0.1`, the default) is the
  private mode. The private GitHub repo does **not** make a Render
  deployment private.
- Do not put real incident data, credentials, or an LLM key in a
  Render demo unless you accept that URL visibility. For this
  portfolio demo the data is simulated, which is why a public demo
  URL is acceptable at all.

## Exact dashboard steps (when you decide to deploy)

1. In Render: New+ > Blueprint (or New+ > Web Service, then point
   it at this repo and copy the settings from `render.yaml`).
2. Connect the repo `dudedumebi-gif/secops-demo`.
3. Apply `render.yaml`. The OmniBrain service is named `omnibrain`
   (rootDir `omnibrain`, startCommand `python3 server.py`,
   plan `free`, `HOST=0.0.0.0`). Render injects `PORT`
   automatically — do not hardcode it.
4. In the service's Environment tab, add `OPENAI_API_KEY` only if
   you later wire the LLM hook; leave it unset for the offline
   demo. Never commit a key.
5. Deploy. The URL will be name-dependent, typically
   `https://omnibrain.onrender.com` if that name is free — Render
   will show the exact URL after deploy. The SOC demo static site
   gets its own URL from the same Blueprint.
6. Expect the first load after idle to take about a minute
   (spin-up), and expect a fresh seed after any redeploy on Free.

## What was verified locally (not a deploy)

- The server starts with `HOST=0.0.0.0 PORT=8767` (see verification
  in the parent handoff). That is the same bind Render needs; it
  does not prove the Render deploy, which has not been run.
