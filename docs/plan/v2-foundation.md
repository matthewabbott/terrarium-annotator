# v2 Foundation Build Plan

*2026-09-03 — ordered task list for autonomous/agent-driven implementation. Each task: target modules, contract, acceptance. Verification layers (L0–L4) per `dev-verification.md`; architecture per `v2-architecture.md`. Tasks are sequenced; do them in order. One commit per task, worklog entry per session.*

## Guardrails (autonomous runs)

1. **Never modify `banished.db`.** Code must open it read-only via SQLite URI with the URI flag — `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` (without `uri=True` the `file:` string is treated as a literal filename); L0 tests assert the reader connection is read-only.
2. **No pushes to origin.** Commits are local on `feature/v2-foundation`; Matt pushes.
3. **No token spend without approval.** L3 (real-model smoke) and any LLM-calling script require Matt's explicit go-ahead in the session. L0/L1/L2/L4 are free and always allowed.
4. **Merge bar per task**: `pytest -q` green, `ruff check src tests` clean, tests mirror module structure.
5. **Deviation policy**: deviating from `v2-architecture.md` is allowed — update the design doc in the same commit and note why in the worklog. If the deviation needs a product decision (schema scope, model choice, budget), STOP and leave it documented as an open question.
6. **Session protocol** per AGENTS.md: read SPEC + design docs + recent worklog first; end with a worklog entry listing what's done, what's next, blockers.

## Tasks

### T1 — Corpus reader (`corpus/`)

- `thread_order()`: the verified executable resolver from `dev-verification.md` (COALESCE OP-time query), yielding thread IDs chronologically.
- `story_posts(thread_id)`: stream `story_post`-tagged posts, `ORDER BY time ASC`.
- `batches(thread_id, batch_size)`: group the thread's story posts (time order) into batches of at most `batch_size` posts; batch = {thread_id, batch_index, post_ids, text}. **No gap heuristics in T1** — explicit size-based batches only; thread boundary always breaks a batch. Vote tallies/meta are simply excluded by the tag predicate. (Gap-based scene segmentation and its benchmark are deferred — see T8.)
- Config: tag predicate (`story_post` default), DB path.
- **Acceptance**: L0 tests on fabricated SQLite fixtures: ordering matches resolver, double-tagged posts handled, threads lacking `op_post` ordered by fallback, batch boundaries respected (size cap, no cross-thread batches, empty threads), reader connection is read-only. `tests/test_corpus.py`.
- **Status**: done (2026-09-03; 12 L0 tests, merge bar green).

### T2 — Story log + merge tree (`memory/`)

- Append-only `story_log` (seq, thread_id, scene range, gist); `story_tree` write-once with `tree_version`.
- `pending_blocks()`, `settle(block, summary)` enforcing strict in-order settlement; blocks thread-aligned (no cross-thread merges until thread close).
- `cover(T, budget)`: budgeted age-decaying digest (OptMem algorithm); `forget(block)` + rebuild.
- Merge function is injected (`Callable[[list[str]], str]`) — LLM-free in tests.
- **Acceptance**: L0 property tests from `dev-verification.md` (cover ≤ budget, aligned powers of two, granularity non-increasing toward T, rebuild-from-log equivalence, forget/rebuild consistency). `tests/test_story_log.py`.
- **Status**: done (2026-09-03; 20 L0 tests, merge bar green; design doc §1 refined to match).

### T3 — Glossary store + quote gate (`glossary/`)

- Tables: `entry`, `revision` (append-only, carries thread/scene/log_seq/pass_id/tree_version), `entry_source` (thread_id, post_id, quote), FTS5 over term+gloss.
- `propose_entry` / `update_entry` / `add_alias` semantics; **quote gate**: quote must be a verbatim substring of the cited post AND contain the term/alias, or the write is rejected.
- No delete/UPDATE path. Merges union evidence (API exists, human-invoked).
- **Acceptance**: L0 incl. adversarial fixtures (paraphrased quote, quote spanning posts, unknown term alias, unicode/case). `tests/test_glossary.py`.
- **Status**: done (2026-09-03; 21 L0 tests incl. adversarial quote fixtures, merge bar green).

### T4 — Injection layer (`inject/`)

- Trigger match: case-sensitive whole-word over current scene text only (scan depth 1).
- Hard token budget (default 15% of configured context), priority = recently-updated then shortest, recursion ≤1.
- Pure function: `(scene_text, entries, budget) -> injected_cards`; token counter injected.
- **Acceptance**: L0 budget/drop-order/recursion tests. `tests/test_injection.py`.
- **Status**: done (2026-09-03; 12 L0 tests incl. gloss-only-recursion adversarial case, merge bar green).

### T5 — LLM client seam (`llm/`)

- `ChatClient` protocol: `chat(messages, tools) -> response`.
- `OpenAICompatibleClient` (v1 AgentClient shape: retries/backoff/timeout) + `ScriptedModel` (fixture-replay) + recording wrapper for L4.
- **Acceptance**: L2 stub-server tests (5xx, malformed JSON, schema drift); ScriptedModel replays fixtures exactly. `tests/test_llm.py`.
- **Status**: in progress (2026-09-03 goal-mode session).

### T6 — Runner (`runner.py`, `tools/`)

- Scene loop: assemble context (system + digest + injected cards + scene), call model, dispatch tools (`propose_entry`, `update_entry`, `add_alias`, `fetch_entry`, `fetch_post`, `fetch_thread_range`, `recall_story`), append gist, record transcript, update `run_state`.
- Thread close: issue due merge calls; periodic re-verification queue hook (stub OK).
- Resume from `run_state` mid-thread.
- **Acceptance**: L1 end-to-end — ScriptedModel + fabricated 3-thread corpus, all L1 assertions from `dev-verification.md` incl. adversarial fixtures and kill/resume. `tests/test_runner.py`.
- **Status**: pending.

### T7 — Verify CLI (`cli.py`)

- `annotator verify <db>`: post-run invariant checker (quote validity vs corpus, provenance coverage, backlink integrity, budget compliance, resume-consistency probe). Doubles as dashboard v1.
- **Acceptance**: L0 tests: checker passes T6's fabricated run output, fails on seeded violations (one per invariant). `tests/test_verify.py`.
- **Status**: pending.

### T8 — Scene segmentation heuristic (deferred, bounded)

- Investigate whether gap-based scenes (splitting batches at large post/time gaps) beat fixed-size batches. Requires a bounded benchmark over `banished.db` gap distributions plus a quality check on L3 output. **Not started without Matt's go-ahead; T1–T7 must not depend on it.**
- **Status**: deferred.


## Repository readiness vs. driver

This repo provides the *inputs* to an overnight run: sequenced tasks, guardrails, merge bar, design docs, durable state in git (commits + worklog). It does **not** contain an autonomous driver. There is no scheduler/launcher, no task-state lease mechanism, no restart handler, and no automated escalation path in-repo.

The driver is **an externally launched agent session** (e.g. `omp` started by Matt, or a cron/loop wrapper he sets up) executing this protocol:

Expect **multiple sessions**: one session attempts pending tasks until its context limit, then stops; completing T1–T7 requires external relaunch/supervision across sessions, with continuity carried by this file's Status lines and the worklog. Nothing here auto-resumes a dead session.

1. Read SPEC, design docs, this plan, recent worklog (AGENTS.md session protocol).
2. **Crash recovery first**: if any task is `in progress`, look for completion evidence (a later commit flipping it to `done` + worklog entry). Absent that, the previous session died mid-task: inspect the working tree and last commit; resume the task if the partial work is sound, otherwise reset it to `pending`. Never end a session leaving a task in `in progress` without a worklog note explaining why.
3. Pick the first task whose Status is exactly `pending` — skip `deferred` (T8) and everything under "Gated on Matt"; flip it to `in progress` in the same commit that starts it. If no `pending` (or reclaimed) task remains, STOP: the autonomous scope is complete.
4. Implement + tests; merge bar (`pytest -q`, `ruff check src tests`); commit; flip Status to `done`.
5. Worklog entry; continue to next task or end session (context limits).

Escalation is manual-by-design: the driver stops at any Gated item, any guardrail 5 stop condition, or any merge-bar failure it cannot fix in-scope — and documents the blocker in the worklog. Durable task state = this file's Status lines + git history; per-task verification = the merge bar at each commit.

## Gated on Matt (not autonomous)

- **G1 — Model choice + credentials** for first real runs (Kimi API key or omp-RPC spike after validating the Python host-tool API against the omp repo source).
- **G2 — L3 smoke run** on threads 30265887 + 30305969, then L4 recording capture.
- **G3 — Push/PR policy** (origin exists; local-only so far).
- **G4 — Lag/agreement on entries-per-1k-posts band** after the first real pass produces numbers.
