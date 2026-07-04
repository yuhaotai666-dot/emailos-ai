# EmailOS AI — Backend

A loop-based **personal** email triage & draft assistant. It turns your inbox into a
**review queue**: the agent reads recent emails, decides which need attention, drafts
high-quality replies in your voice, critiques and rewrites them until they pass a
quality + safety bar, and queues them for you to approve.

> **Safety rule #1: the AI never sends email.** There is no send path in this codebase.
> The agent only drafts, critiques, scores, and queues. You review and approve everything.

## What it does (the loop)

```
fetch recent emails
  └─ classify each (category, priority, needs_reply, confidence, summary, risk)
       └─ if it needs a reply:
            retrieve memory (rules + past errors + success patterns)
            → generate draft (Theo's voice)
            → critique → score → check hard constraints
            → rewrite up to MAX_DRAFT_RETRIES if below the bar
            → save best draft to the review queue (pending_review)
  └─ build a Daily Brief (top priority, need-reply, drafts ready, follow-ups, actions)
```

## Architecture

- **FastAPI** app (`app/main.py`) with routers under `app/routes/`.
- **Pydantic** domain models in `app/models/`.
- **LangGraph agent** (`app/graph/`) — the per-email loop compiled as a `StateGraph`:
  `classify → (needs_reply?) → retrieve → generate → evaluate → rewrite* → finalize`.
  One graph run processes one email; `workflow_engine` runs it over the inbox.
- **Services** in `app/services/` — one responsibility each, each LLM step an LCEL chain:
  `classifier`, `context_retriever`, `draft_generator`, `draft_critic`, `draft_scorer`,
  `constraint_checker` (deterministic, non-LLM), `memory_service`, `error_library`,
  `workflow_engine` (the orchestrator).
- **LLM layer** (`services/llm_client.py`) — LangChain `ChatAnthropic`; structured JSON via
  `with_structured_output` (tool-calling under the hood). The service seam stays
  provider-agnostic, so another LangChain chat model can be swapped in later.
- **Repositories** (`app/repositories/`) — a JSON-file store behind a `Collection`
  interface, so a Supabase/Postgres implementation can replace it with no service changes.
- **Email source** behind an `EmailProvider` seam; MVP uses `MockEmailProvider` (Gmail later).

### Graceful degradation
If `ANTHROPIC_API_KEY` is empty (or `LLM_PROVIDER=mock`), no chat model is constructed and
every LLM step falls back to a **deterministic mock** implementation. The whole loop, the
API, and the test-suite run fully offline and free. Add a key to get real Claude drafts (via
LangChain `ChatAnthropic`) — no code changes.

## Run it locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional: add ANTHROPIC_API_KEY for real drafts
uvicorn app.main:app --reload
```

Then:
- Health: <http://localhost:8000/api/health>
- Interactive docs: <http://localhost:8000/docs>
- Kick off a run: `curl -X POST http://localhost:8000/api/agent/run-triage`

## Environment variables

| Var | Default | Meaning |
|-----|---------|---------|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, or `mock`. Selects the chat model. |
| `ANTHROPIC_API_KEY` | *(empty)* | Used when `LLM_PROVIDER=anthropic`. If empty, backend runs on mock outputs (no crash, no cost). |
| `OPENAI_API_KEY` | *(empty)* | Used when `LLM_PROVIDER=openai`. If empty, backend runs on mock outputs. |
| `LLM_MODEL` | `claude-sonnet-4-6` | Model id for the selected provider (Claude id for anthropic, e.g. `gpt-4o` for openai). Swap without touching code. |
| `EMAIL_PROVIDER` | `mock` | Email source. |
| `DATABASE_PROVIDER` | `local` | Persistence backend. |
| `MAX_DRAFT_RETRIES` | `2` | Max rewrite attempts per draft. |
| `MIN_DRAFT_SCORE` | `8` | Overall score below which a draft is rewritten. |
| `ALLOWED_ORIGINS` | localhost + `*.lovable.app` | CORS origins (comma-separated; `*` wildcard supported). |

## API

| Method & path | Purpose |
|---|---|
| `GET /api/health` | Backend status + current mode. |
| `POST /api/agent/run-triage` | Run the full agent loop; returns brief + triage + drafts + follow-ups + run summary. |
| `GET /api/agent/brief` | Latest Daily Brief. |
| `GET /api/drafts` | All drafts in the review queue. |
| `GET /api/drafts/{id}` | One draft. |
| `POST /api/drafts/{id}/critique` | Re-run critique + score + constraints. |
| `POST /api/drafts/{id}/regenerate` | Regenerate (optional `{ "instruction": "..." }`). |
| `POST /api/drafts/{id}/approve` | Mark approved. **Does not send.** |
| `POST /api/drafts/{id}/edit` | Save an edit; optional `learn: true` to learn from it. |
| `POST /api/drafts/{id}/ignore` | Mark ignored. |
| `GET/POST /api/memory/rules` | Preference rules. |
| `GET/POST /api/memory/errors` | Error cases (avoid repeating mistakes). |
| `GET/POST /api/memory/success-patterns` | What worked before. |
| `POST /api/memory/learn-from-edit` | Propose memory updates from an edit (saved only if `auto_save=true`). |

## Tests

```bash
source .venv/bin/activate
pytest
```
All tests run on the mock path (no network, no key needed).

## Current limitations

- Single-user, single-process. JSON persistence is not hardened for concurrency.
- `run-triage` rebuilds the triage + draft queue each run (memory persists).
- No real Gmail read/send; no auth; no Supabase yet.
- The `LLM_MODEL` default is a placeholder id — confirm the current Sonnet id before
  using the real LLM path.

## Roadmap (seams already in place)

- **Gmail**: implement `EmailProvider.fetch_recent` in a `GmailProvider` (read-only first).
  Sending stays intentionally unimplemented.
- **Supabase/Postgres**: implement the `Collection` interface in a `SupabaseRepository`
  and switch `DATABASE_PROVIDER`.
- **OpenAI**: add an alternate provider in `llm_client.py`; select via `LLM_PROVIDER`.
