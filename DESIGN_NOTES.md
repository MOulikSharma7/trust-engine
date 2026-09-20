# Design Notes

This is the "why," not the "what" — the code and `README.md` cover what
exists. This document exists so someone unfamiliar with the project can
understand the deliberate tradeoffs without reading the source, and so a
future maintainer (including future us) can tell a considered decision
apart from an accident.

## Tradeoffs

### 1. A Beta-Bernoulli belief + lower confidence bound, not a raw approval streak

`engine.py` tracks `alpha`/`beta` (Beta distribution parameters) per Rule and
gates promotion on `lower_confidence_bound()` — the 10th percentile of the
credible interval on the true approval rate — rather than something like
"last 5 approvals were clean, promote."

A raw streak is exactly as good as its worst-case luck: 5/5 approvals on a
severity-3 action is a coin that could plausibly be biased anywhere from
~55% to ~100% reliable, and `test_no_promotion_on_short_streak` exists
specifically to keep that from promoting. The lower confidence bound
punishes small sample sizes automatically — it's wide (and low) with little
data and narrows as evidence accumulates — without a separate "have we seen
enough outcomes yet" rule bolted on. The cost is that it's less legible than
a streak counter: explaining "why hasn't this promoted yet" requires
explaining a credible interval, not just a count. We judged that tradeoff
worth it because the whole point of the system is to resist a lucky streak.

### 2. Asymmetric demotion: fast down, slower back up

`demote()` drops a tier immediately on a single `FLAGGED_AFTER_AUTO_EXECUTION`
and only partially resets belief (`alpha = max(1.0, alpha * 0.5)`, not reset
to the prior). `maybe_promote()` then raises the required bound by
`0.03 * demotion_count` on every subsequent promotion attempt for that rule.
Both halves matter: partial belief retention means old good behavior still
counts for something, but the raised bar means the *same volume* of
subsequent good behavior that earned the original promotion is no longer
enough — `test_reprovision_harder_after_demotion` and the simulation's
recovery-time metric exist to prove this isn't just implemented but actually
biting (seed 6 in Milestone 2's replay: 11 outcomes to first promote, 36 to
recover). This mirrors how trust actually works for people, and is the
single property most worth demonstrating live, per the original spec.

### 3. `severity_hint` from the draft agent is informational only

`workflow/draft_agent.py`'s `DraftResult.candidate_actions[].severity_hint`
is the LLM's own per-call guess at how risky an action is, and it never
touches routing. The Rule's `severity` — set once, at design time, in
`simulation/replay.py`'s `DEFAULT_RULES` — is the only value `engine.py` ever
sees. A meaningful mismatch just logs a warning
(`_check_severity_hint` in `draft_agent.py`).

This is a security-relevant boundary, not just a style choice: an LLM call
is untrusted input from the Trust Engine's point of view (a prompt-injected
signal, a bad day for the model, a jailbroken draft) and letting it move
where an action routes would mean a single bad completion could talk its
way into AUTO-tier execution. Keeping severity a static, human-set value
means the worst a bad draft can do is get rejected or dropped — see #6 below
for the parallel enforcement on `action_type`.

### 4. `RuleStore` protocol now, SQLite today, Postgres deferred

`trust_engine/store.py` defines `RuleStore` as a `Protocol` and ships only
`SqliteRuleStore`. Every caller — the simulation harness, the workflow
pipeline, the API — depends on `RuleStore`, never on `sqlite3` directly.
This was a Milestone 2 decision (Docker/Postgres weren't available in that
environment) that turned out to be the right call independent of that
constraint: local dev needs zero setup (`pip install`, nothing else), and a
future `PostgresRuleStore` is a single new class plus a one-line change at
whatever constructs the store — not a refactor of `engine.py`, the
simulation harness, the workflow layer, or the API. The cost paid today is
SQLite's own limitations (see multi-tenant section below) — accepted
deliberately, not overlooked.

### 5. Outcomes for APPROVE/HUMAN-tier actions are recorded on resolution, not on enqueue

`workflow/executor.py` writes a queued candidate action to `approval_queue`
and explicitly does **not** call `engine.record_outcome` at that point — it's
only called later, when `api/routes/approval_queue.py`'s resolve endpoint
handles the human's actual decision. Conceptually this is the same idea the
simulation harness uses — a decision (there, a behavior profile's; here, a
human's) *is* the outcome — but see #8 below for how the two actually differ
in timing. Recording anything at enqueue time would either double-count
(once at enqueue, once at resolution) or force a guess about what the human
will eventually decide — both wrong. The tradeoff is that a queued item
sitting unresolved for a long time produces no signal at all, positive or
negative, for as long as it sits — accepted as correct, since "no decision
yet" genuinely isn't evidence of anything.

### 6. Executor: real execution for one action type, simulated for the rest — and `action_type` is constrained to existing Rules

Only `post_slack_message` performs a real side effect
(`REAL_EXECUTION_ACTION_TYPES` in `executor.py`); the other four default
action types always simulate (log what would happen, mutate nothing). Each
of those would need a specific, carefully-scoped target — which PR, which
ticket, which stakeholder's DM — that this project deliberately doesn't
build, rather than faking a "safe-looking" integration that isn't actually
safe. Symmetrically, `draft_agent.py` drops (and logs) any `action_type` the
LLM proposes that doesn't match an existing Rule — "no Rule, no route" is
enforced at the boundary rather than trusted to the model.

### 7. The false-promotion metric is windowed, not "ever flagged across the whole run"

This was a direct fix during the build: the first version of
`simulation/replay.py::summarize()` counted a promotion as "false" if *any*
later demotion occurred anywhere in the rest of the run. At seed 42 with a
90-day run, that produced a 100% false-promotion rate — technically correct
by that definition, but misleading, because a long enough run with any
nonzero per-outcome flag probability makes a flag near-certain for *any*
promoted rule regardless of whether the promotion itself was premature. The
fix (`FALSE_PROMOTION_WINDOW_OUTCOMES = 15`) only counts a flag landing
within 15 outcomes of the promotion as evidence the promotion itself was
wrong; the old "ever flagged" number is kept alongside as
`false_promotion_rate_raw` for reference, not silently dropped. This is
worth stating explicitly because it's the kind of metric-definition bug that
looks like a working number until someone asks what it actually means.

### 8. The simulation harness and the real pipeline update trust on different schedules — by design, but worth stating precisely

In `simulation/replay.py`, every rule — at every tier — gets a proposed
action resolved by the behavior profile every simulated day, and
`record_outcome` is called immediately regardless of tier. That's a
deliberate simplification for a harness whose whole job is compressing weeks
into a legible, continuously-evolving chart; the simulation isn't trying to
model the real pipeline's timing, it's trying to demonstrate the engine's
algorithm under a large volume of outcomes.

The real pipeline (`workflow/executor.py`) is stricter and, per Rule tier,
diverges from the simulation in two different ways:

- **AUTO tier**: a successful execution records exactly one
  `APPROVED_UNCHANGED` outcome per action (the "nothing went wrong" signal),
  same shape as the simulation's per-day outcome — so AUTO-tier evidence
  accrual is at least the same *kind* of signal, just at whatever cadence
  real actions actually happen rather than once a simulated day.
- **APPROVE/HUMAN tier**: the simulation still calls `record_outcome`
  immediately (the behavior profile *is* the human, resolving same-day), but
  the real pipeline defers entirely — see #5 above — until an actual human
  resolves the queued item, which could take minutes or never happen at all.

Net effect: a simulated curve and a real curve for the same rule are not
directly comparable point-for-point, especially for anything below AUTO
tier, where the simulation assumes same-day human responsiveness the real
system doesn't. Worth knowing before using a simulated chart as a literal
prediction of real-world promotion timing.

### 9. Two agent roles, on purpose

Ingest and draft are the only two agent/LLM-touching roles in the whole
system (`workflow/ingest/*` and `workflow/draft_agent.py`). Adding, say, a
separate "risk assessment" or "summarization" stage would add integration
surface and latency without adding anything `engine.py` can act on — the
Trust Engine only ever consumes a `Decision` and an `edit_distance`. This
was an explicit non-goal from the spec, and it held up in practice: nothing
in Milestones 3–4 wanted a third role.

## What would change for real multi-tenant production use

- **Per-tenant isolation.** `blueprint_id` already namespaces rules
  (`"morning-status"` today), but `SqliteRuleStore` is one file, one
  process, no tenant boundary beyond that string. Production would need
  either per-tenant schemas/databases or a `tenant_id` on every table with
  row-level security enforced at the query layer — not just convention.
- **Concurrency.** SQLite's single-writer model is fine for one demo user
  clicking Approve; it is not fine for multiple approvers resolving items
  concurrently, or a scheduled pipeline run overlapping with live API
  traffic. This is exactly what `RuleStore` was built to make swappable —
  the missing piece is a `PostgresRuleStore` with real transactions and
  row-level locking on `rules` (two concurrent `record_outcome` calls for
  the same rule must not race).
- **Role-based access on approval actions.** Right now anyone who can reach
  the API can resolve any queued item, at any severity. Production needs
  per-role authorization — who can resolve a severity-4/5 item, who can only
  view — and the resolution needs to record *who* decided, not just what.
- **Audit log.** `outcomes` records what happened to trust, but not the full
  provenance chain (which signals produced this draft, which model/prompt
  version, who resolved it, from what IP/session). A production deployment
  handling real approval decisions needs an immutable audit trail
  independent of the trust-scoring tables.
- **Secrets management.** Every credential in this project
  (`GITHUB_TOKEN`, `SLACK_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)
  is a plain environment variable, fine for local dev, not fine for a
  shared deployment. That's a secrets manager (Vault, cloud KMS, or the
  platform's native secret store), not an env var, in production —
  and the `mock` LLM backend needs an explicit, enforced guard rail so it
  can never be selected outside local/test environments.
- **Observability.** No metrics or alerting exist today beyond the
  simulation's own summary output and application logs. Production wants
  dashboards on tier-transition rate, false-promotion rate (windowed) per
  rule, and alerting specifically on repeated demotions — that's the signal
  that a given action type has become genuinely unreliable, not noise.
- **LLM hardening.** `LLMBackend` implementations have no retry/backoff,
  no timeout tuning, no cost tracking, and no defense against a signal
  containing prompt-injection content beyond the `action_type`/severity
  boundaries already in place (#3 and #6 above). All of that is
  infrastructure a single-demo build doesn't need and a production
  deployment can't skip.
