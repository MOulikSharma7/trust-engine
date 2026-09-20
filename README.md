# Trust Engine

An adaptive trust engine for governed agent workflows: a statistical core that
decides, per action type, whether an AI agent should **auto-execute** an
action, **draft it for human approval**, or **hand it entirely to a human** —
and that earns or loses that autonomy based on real evidence, not static
config.

Built incrementally across five milestones. Milestones 1–4 are complete and
covered by an automated test suite; Milestone 5's cloud deployment is on hold
by request — this README and `DESIGN_NOTES.md` are the Milestone 5
documentation deliverable, written against the system as it actually exists
today (not aspirationally).

---

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌───────────────────┐     ┌─────────────┐
│   INGEST     │ --> │    DRAFT     │ --> │   TRUST ENGINE     │ --> │   OUTCOME   │
│ GitHub +     │     │ one LLM call │     │  routes: AUTO /    │     │  TRACKER    │
│ Slack        │     │ -> candidate │     │  APPROVE / HUMAN   │     │ record      │
│ connectors   │     │ actions      │     │  + executor runs   │     │ Outcome via │
│              │     │              │     │  AUTO-tier actions │     │ RuleStore   │
└──────────────┘     └──────────────┘     └───────────────────┘     └─────────────┘
                                                                             │
                                                                             ▼
                                                            feeds back into each
                                                            Rule's Beta(alpha,
                                                            beta) belief for the
                                                            next decision
```

Two independent layers, built and tested separately:

1. **`trust_engine/`** — the core. Pure logic (`engine.py`) over plain
   dataclasses (`models.py`), plus a persistence layer (`store.py`). No LLM
   calls, no HTTP calls. Fully unit-testable in-memory.
2. **`workflow/`, `api/`, `simulation/`** — everything around the core: real
   ingestion, a draft agent, an executor, a FastAPI service, a minimal
   dashboard, and a simulation harness that compresses weeks of usage into a
   scripted replay. This layer is intentionally thin — it orchestrates calls
   into `trust_engine/`, it doesn't reimplement any trust-scoring logic.

## Repository structure

```
trust-engine/
  trust_engine/            # Milestone 1 — the core (models, engine, store)
    models.py               # Rule, Outcome, Tier, Decision — plain dataclasses/enums
    engine.py                # lower_confidence_bound, maybe_promote, record_outcome, demote
    store.py                  # RuleStore protocol + SqliteRuleStore (rules, outcomes, approval_queue)
    tests/
  simulation/               # Milestone 2 — replay harness, no LLM/API calls
    profiles.py              # ConsistentApprover, NoisyApprover, LateCatchApprover
    replay.py                 # run_replay(), summarize(), the default rule set + CLI
    plots.py                   # confidence-bound-over-time chart
    output/                     # generated plots (gitignored contents, dir kept)
    tests/
  workflow/                 # Milestone 3 — real ingestion + draft agent + executor
    ingest/
      normalize.py            # Signal — the stable schema both connectors emit
      github_connector.py       # real GitHub REST API, graceful on missing token/rate-limit
      slack_connector.py         # real Slack Web API, falls back to a bundled fixture
      fixtures/slack_messages.json
    llm/
      base.py                  # LLMBackend protocol
      anthropic_backend.py       # real Anthropic SDK backend
      openai_backend.py           # real OpenAI SDK backend
      mock_backend.py              # demo/test-only — NOT a real provider, see below
      __init__.py               # build_backend_from_env() — reads LLM_BACKEND once
    draft_agent.py            # one LLM call -> validated DraftResult
    executor.py                 # AUTO -> execute (real or simulated); APPROVE/HUMAN -> queue
    pipeline.py                  # wires it together; CLI entry point
    tests/
  api/                       # Milestone 4 — FastAPI service + dashboard backend
    main.py                    # app, routers, static mounts
    dependencies.py              # get_store() — one sqlite3 connection per request
    schemas.py                     # pydantic I/O models
    routes/                          # rules, signals, approval_queue, simulation
    tests/
  frontend/                 # Milestone 4 — plain HTML/CSS/vanilla JS dashboard, no build step
    index.html, app.js, styles.css
  README.md                 # this file
  DESIGN_NOTES.md            # tradeoffs + what changes at larger scale
  requirements.txt
  pytest.ini
```

## Setup

Requires Python 3.11+ (built and tested on 3.14).

```bash
pip install -r requirements.txt
```

No Docker, no external database — `trust_engine/store.py`'s `SqliteRuleStore`
uses `sqlite3` from the standard library. A `PostgresRuleStore` implementing
the same `RuleStore` interface is the Milestone 5 deployment target and is
deliberately not built yet (see `DESIGN_NOTES.md`).

### Run the tests

```bash
pytest
```

58 tests across all four milestones: `trust_engine/tests/` (core logic,
no I/O), `simulation/tests/`, `workflow/tests/` (connectors and the executor
against mocked APIs — no real network calls in the suite), `api/tests/`
(FastAPI `TestClient` against an isolated in-memory store).

### Run the simulation harness

```bash
python -m simulation.replay --profile=late_catch --days=90 --seed=6
```

Produces `simulation/output/autonomy_curve.png` and prints summary metrics
(false-promotion rate — both windowed and raw — average outcomes needed to
reach AUTO by severity, and demotion/recovery-time comparisons). Runs
entirely in-memory; never touches the real `local.db`. `--seed=6` is
validated, not hand-picked — see `DESIGN_NOTES.md` for what it demonstrates.

### Run the real pipeline

```bash
python -m workflow.pipeline --once --github-repo=owner/name --slack-channel="#standup"
```

Or on a loop: `--loop --interval=3600`.

Needs at least one **LLM backend** credential to produce a real draft:

| Env var | Purpose | If unset |
|---|---|---|
| `LLM_BACKEND` | `anthropic` (default) \| `openai` \| `mock` | defaults to `anthropic` |
| `ANTHROPIC_API_KEY` | required if `LLM_BACKEND=anthropic` | `AnthropicBackend` raises at construction |
| `OPENAI_API_KEY` | required if `LLM_BACKEND=openai` | `OpenAIBackend` raises at construction |
| `GITHUB_TOKEN` | GitHub connector auth | connector logs a warning, returns no signals |
| `SLACK_BOT_TOKEN` | Slack connector auth + real `post_slack_message` execution | connector falls back to the bundled fixture; executor falls back to simulated execution |

`LLM_BACKEND=mock` is **not a real provider** — it returns a canned,
clearly-labeled response (`[MOCK BACKEND — not a real LLM response]`) so the
pipeline/API can be exercised end-to-end with zero credentials. Never rely
on it for anything beyond local smoke-testing.

### Run the API + dashboard

```bash
uvicorn api.main:app --reload
```

Open `http://localhost:8000` for the dashboard (approval queue with
approve/edit/reject, rule/tier table with confidence bounds, an on-demand
simulation panel). API docs at `http://localhost:8000/docs`.

## What's live vs. fixture/simulated right now

This matters for anyone picking the project back up — carried forward from
the Milestone 3/4 build:

- **GitHub connector**: real REST API code path, unit-tested against mocked
  responses. Needs `GITHUB_TOKEN` to actually pull data.
- **Slack connector**: real Web API code path exists, but defaults to the
  bundled fixture (`workflow/ingest/fixtures/slack_messages.json`) when
  `SLACK_BOT_TOKEN` is unset. Every fixture-derived signal is tagged
  `payload["fixture"] = True`.
- **Executor real-vs-simulated split**: only `post_slack_message` performs a
  real side effect (posts via Slack's `chat.postMessage`). The other four
  default action types (`update_pr_description`, `flag_blocker_to_stakeholder`,
  `comment_on_payments_ticket`, `reschedule_delivery_date`) are always
  simulated (logged, not executed) — each would need a specific, carefully
  scoped target this project doesn't build yet.
- **LLM backends**: `AnthropicBackend` and `OpenAIBackend` are real
  implementations; `mock` is a demo convenience only.

## Current status

- **Milestone 1** — Trust Engine core: done, 10 tests.
- **Milestone 2** — Persistence + simulation harness: done, 8 store tests + 6 simulation tests.
- **Milestone 3** — Real workflow integration: done, 22 tests.
- **Milestone 4** — API + minimal frontend: done, 12 API tests.
- **Milestone 5** — Documentation: this file + `DESIGN_NOTES.md`. Cloud
  deployment (Terraform, managed Postgres) is on hold by request; the
  `RuleStore` abstraction exists specifically so that work doesn't require
  touching any calling code when it happens.
