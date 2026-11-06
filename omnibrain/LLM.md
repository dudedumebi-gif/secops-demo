# Optional LLM layer — gpt-4o-mini (wired, key not included)

The rule-based assistant stays authoritative. Ask / Triage / Draft
always compute the rule-based Steps 1–3 result first, exactly as
before. When `OPENAI_API_KEY` is set, the server then makes **one**
Chat Completions call (model `gpt-4o-mini`, temperature 0.2, max 600
tokens, 15s timeout, stdlib `urllib` only — no new dependency) and
returns the model's text in `llm_answer` **alongside** the unchanged
rule-based fields. The model is instructed to use only the supplied
rule-based reasoning and a compact workspace summary (real DB rows:
top backlog items with RICE, detections with MITRE + stage,
telemetry, playbooks, roadmap), to cite real row titles/IDs, to keep
the Steps 1–3 structure, and never to invent MITRE techniques,
controls, or workspace facts. If Step 1 matched no technique, it
must say "no MITRE technique matched — human review required".

## Responses

Every ask/triage/draft response now carries:

- `llm_used` (bool), `llm_model` (`"gpt-4o-mini"` when used, else
  `null`), `llm_error` (short generic string, e.g.
  `"openai_unavailable"`, when a call fails — never the key, never
  the raw API body), `llm_answer` (the model's text, or `null`).
- `GET /api/llm-status` returns `{configured: bool, model:
  "gpt-4o-mini"}` — `configured` only says whether a key is present,
  never the value. `GET /api/dashboard` includes `llm_available`.
- The Ask tab fetches the status on load and shows "Online —
  gpt-4o-mini" when configured, "Offline — rule-based" otherwise.
  Ask / Triage / Draft results show a small "LLM" vs "rule-based"
  source label.

## Fallback (mandatory)

No key, timeout, network error, non-200, or bad JSON → the exact
current rule-based response with `llm_used=false`. Never a 500
because of the LLM. One attempt only, no retry, no background
calls, no auto cron.

## Your dashboard steps (the key is your part)

1. Render dashboard > `omnibrain` service > Environment > set
   `OPENAI_API_KEY` to an OpenAI API key for the account that owns
   the key, then redeploy. `render.yaml` already declares it as
   `sync: false`, so it must come from the dashboard, never from
   the repo. The repo app **cannot** use the Muse-side OpenAI
   connection (Secure Vault) — that connection belongs to Muse,
   not to this app; the app reads only its own environment.
   Never commit a key, never paste it in chat.
2. Live test: open the Render app, Ask a phishing question
   ("How do we respond to a phishing attack?"). Confirm the
   response has `llm_used: true`, `llm_model: "gpt-4o-mini"`,
   an `llm_answer` that cites real rows, and Step 1 still
   `T1566`. Then unset the key (or check the app before setting
   it): the same question returns `llm_used: false` and the
   rule-based answer unchanged.

## Cost

gpt-4o-mini (chosen as the cheapest model), max 600 tokens per
call, one call per ask/triage/draft the user makes, and no calls
at any other time — no background refresh, no cron, no per-page
call. Dashboard loads do not call OpenAI. Expect pennies per
demo session at most; check OpenAI's current pricing before
relying on an exact figure.

## Privacy

The LLM receives the simulated demo workspace context only —
seeded simulated data, not live employer telemetry. There is no
employer SIEM/EDR/SOAR data in this app at all. Threat-intel
refresh still sends no internal data to public feeds (read-only
GETs to CISA/NVD; see README.md).
