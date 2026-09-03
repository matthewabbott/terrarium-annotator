# v2 Foundation Build Plan

*2026-09-03 — ordered task list for autonomous/agent-driven implementation. Each task: target modules, contract, acceptance. Verification layers (L0–L4) per `dev-verification.md`; architecture per `v2-architecture.md`. Tasks are sequenced; do them in order. One commit per task, worklog entry per session.*

## Guardrails (autonomous runs)

1. **Never modify `banished.db`.** Code must open it `file:...?mode=ro`; L0 tests assert the reader connection is read-only.
2. **No pushes to origin.** Commits are local on `feature/v2-foundation`; Matt pushes.
3. **No token spend without approval.** L3 (real-model smoke) and any LLM-calling script require Matt's explicit go-ahead in the session. L0/L1/L2/L4 are free and always allowed.
4. **Merge bar per task**: `pytest -q` green, `ruff check src tests` clean, tests mirror module structure.
5. **Deviation policy**: deviating from `v2-architecture.md` is allowed — update the design doc in the same commit and note why in the worklog. If the deviation needs a product decision (schema scope, model choice, budget), STOP and leave it documented as an open question.
6. **Session protocol** per AGENTS.md: read SPEC + design docs + recent worklog first; end with a worklog entry listing what's done, what's next, blockers.

## Tasks

### T1 — Corpus reader (`corpus/`)

- `thread_order()`: the verified executable resolver from `dev-verification.md` (COALESCE OP-time query), yielding thread IDs chronologically.
- `story_posts(thread_id)`: stream `story_post`-tagged posts, `ORDER BY time ASC`.
- `scenes(thread_id)`: group maximal runs of consecutive (by time) `story_post` posts; scene = {thread_id, scene_index, post_ids, text}.
- Config: tag predicate (`story_post` default), DB path.
- **Acceptance**: L0 tests on fabricated SQLite fixtures: ordering matches resolver, double-tagged posts handled, threads lacking `op_post` ordered by fallback, reader connection is read-only. `tests/test_corpus.py`.

### T2 — Story log + merge tree (`memory/`)

- Append-only `story_log` (seq, thread_id, scene range, gist); `story_tree` write-once with `tree_version`.
- `pending_blocks()`, `settle(block, summary)` enforcing strict in-order settlement; blocks thread-aligned (no cross-thread merges until thread close).
- `cover(T, budget)`: budgeted age-decaying digest (OptMem algorithm); `forget(block)` + rebuild.
- Merge function is injected (`Callable[[list[str]], str]`) — LLM-free in tests.
- **Acceptance**: L0 property tests from `dev-verification.md` (cover ≤ budget, aligned powers of two, granularity non-increasing toward T, rebuild-from-log equivalence, forget/rebuild consistency). `tests/test_story_log.py`.

### T3 — Glossary store + quote gate (`glossary/`)

- Tables: `entry`, `revision` (append-only, carries thread/scene/log_seq/pass_id/tree_version), `entry_source` (thread_id, post_id, quote), FTS5 over term+gloss.
- `propose_entry` / `update_entry` / `add_alias` semantics; **quote gate**: quote must be a verbatim substring of the cited post AND contain the term/alias, or the write is rejected.
- No delete/UPDATE path. Merges union evidence (API exists, human-invoked).
- **Acceptance**: L0 incl. adversarial fixtures (paraphrased quote, quote spanning posts, unknown term alias, unicode/case). `tests/test_glossary.py`.

### T4 — Injection layer (`inject/`)

- Trigger match: case-sensitive whole-word over current scene text only (scan depth 1).
- Hard token budget (default 15% of configured context), priority = recently-updated then shortest, recursion ≤1.
- Pure function: `(scene_text, entries, budget) -> injected_cards`; token counter injected.
- **Acceptance**: L0 budget/drop-order/recursion tests. `tests/test_injection.py`.

### T5 — LLM client seam (`llm/`)

- `ChatClient` protocol: `chat(messages, tools) -> response`.
- `OpenAICompatibleClient` (v1 AgentClient shape: retries/backoff/timeout) + `ScriptedModel` (fixture-replay) + recording wrapper for L4.
- **Acceptance**: L2 stub-server tests (5xx, malformed JSON, schema drift); ScriptedModel replays fixtures exactly. `tests/test_llm.py`.

### T6 — Runner (`runner.py`, `tools/`)

- Scene loop: assemble context (system + digest + injected cards + scene), call model, dispatch tools (`propose_entry`, `update_entry`, `add_alias`, `fetch_entry`, `fetch_post`, `fetch_thread_range`, `recall_story`), append gist, record transcript, update `run_state`.
- Thread close: issue due merge calls; periodic re-verification queue hook (stub OK).
- Resume from `run_state` mid-thread.
- **Acceptance**: L1 end-to-end — ScriptedModel + fabricated 3-thread corpus, all L1 assertions from `dev-verification.md` incl. adversarial fixtures and kill/resume. `tests/test_runner.py`.

### T7 — Verify CLI (`cli.py`)

- `annotator verify <db>`: post-run invariant checker (quote validity vs corpus, provenance coverage, backlink integrity, budget compliance, resume-consistency probe). Doubles as dashboard v1.
- **Acceptance**: L0 tests: checker passes T6's fabricated run output, fails on seeded violations (one per invariant). `tests/test_verify.py`.

## Gated on Matt (not autonomous)

- **G1 — Model choice + credentials** for first real runs (Kimi API key or omp-RPC spike after validating the Python host-tool API against the omp repo source).
- **G2 — L3 smoke run** on threads 30265887 + 30305969, then L4 recording capture.
- **G3 — Push/PR policy** (origin exists; local-only so far).
- **G4 — Lag/agreement on entries-per-1k-posts band** after the first real pass produces numbers.
