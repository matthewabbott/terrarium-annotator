# Development-Time Verification for v2

*2026-09-02 — how we verify the wiring without human-in-the-loop glossary judgment. Complements `v2-architecture.md` §6 (which covers glossary **quality**); this doc covers **correctness of the machinery** during agent-driven development.*

Key insight: quote-gated writes and structured provenance mean most wiring is *machine-checkable*. A run is verifiable without anyone judging whether entries are "good".

## Layers

### L0 — Unit tests, no LLM

Deterministic machinery, mirroring module structure:

- **Corpus reader**: scene grouping, thread boundaries, `story_post` default predicate + config override; fixtures fabricate minimal SQLite corpora (never touch `banished.db`).
- **Story log/tree**: append-only log, in-order block settlement, cover budget. Property-style invariants worth testing on many (T, budget) pairs: `len(cover(T, budget)) ≤ budget`; blocks are aligned powers of two; granularity is non-decreasing with distance from T; tree fully rebuildable from log alone; `forget` + re-merge restores a consistent tree.
- **Quote verification**: substring + term/alias presence, unicode/case edge cases, quote spanning posts rejected.
- **Injection**: case-sensitive whole-word trigger match, token budget enforcement, priority drop order, recursion cap ≤1.
- **Storage**: revision append-only (no UPDATE path exists), FTS sync, referential integrity.

### L1 — Scripted-model end-to-end (the wiring proof)

A `ScriptedModel` implementing the LLM client interface, replaying canned per-scene responses (including tool calls) from a fixture script, driving the **real runner** over a fabricated 3-thread corpus. Asserts after the run:

- every `propose_entry` with a valid quote committed; invalid-quote calls rejected with error fed back to the model
- one story-log gist per scene; merge calls issued at thread close in settlement order
- digest token count ≤ budget at every scene boundary
- checkpoint/resume: kill mid-thread, restart, run continues from the same scene with identical state
- transcript rows written per scene

This proves the loop end-to-end with zero model spend. The scripted fixture should include *adversarial* responses: paraphrased (non-verbatim) quotes, tool calls for nonexistent terms, oversized gists, merge summaries over the byte cap.

### L2 — HTTP client tests against a stub server

Minimal `/v1/chat/completions` stub exercising the real HTTP client: retries with backoff, malformed JSON, 5xx, timeout, tool-call schema drift. (v1 had this shape; rebuild small.)

### L3 — Invariant-checked smoke run (real model, tiny slice)

Run the real pipeline with the real budget model on the **two chronologically-first threads** of `banished.db`, then run an automated post-run checker over `annotator.db`:

- 100% quote validity, re-verified against the corpus (quote is substring of cited post, contains term/alias)
- provenance coverage = 100%, referential integrity of all backlinks
- digest ≤ budget at every recorded boundary; injected-token share ≤ 15%
- entries/1k posts inside the agreed band (calibrated after first run)
- resume consistency: second launch does no duplicate work

The slice is resolved by query, not hardcoded. Executable thread-ordering resolver (verified against `banished.db`):

```sql
-- Prefer the op_post-tagged post's time; fall back to earliest post time for
-- the 2 threads (of 278) that lack an op_post tag. GROUP BY absorbs duplicate
-- tag rows. Verified: no thread has >1 distinct OP post, and today OP time ==
-- earliest post time everywhere — but do not rely on that; future corpora
-- may not share the invariant.
SELECT t.id AS thread_id,
       COALESCE(MIN(CASE WHEN tg.name = 'op_post' THEN p.time END),
                MIN(p.time)) AS started
FROM thread t
JOIN post p ON p.thread_id = t.id
LEFT JOIN tag tg ON tg.post_id = p.id AND tg.name = 'op_post'
GROUP BY t.id
ORDER BY started ASC;   -- append LIMIT 2 for the L3 slice
```

Thread IDs are **opaque, not chronological**: the corpus mixes sources (thread id 12922 is story-thread 194 while id 31323984 is story-thread 20). This resolver yields **30265887** ("Banished Quest"), **30305969**, then **30392208** ("Banished Quest 3") — matching where v1's glossary sources start. The story-reading predicate (`story_post`) is applied separately when streaming posts within each thread.

Catches what fakes can't: tool-call format drift, paraphrased quotes, context-assembly ordering bugs. Costs ~2 threads of tokens. **On-demand only, never a CI gate** — it spends subscription budget and is non-deterministic; CI runs L0–L2 + L4 replays.

### L4 — Recorded replay

Record L3's raw request/response pairs as first-class fixtures; replay them deterministically in the test suite and **assert replay idempotence** (same recorded input stream → identical resulting DB state). Regression-tests the runner against *real model output* forever, without spending tokens. Refresh recordings only when prompts/schemas change deliberately.

### L5 — Quality evals (later)

`v2-architecture.md` §6: gold-set P/R/F1, faithfulness, trigger-utility, anti-baseline comparison. Not part of the dev loop.

## Dev-loop contract (for coding agents building v2)

- Every feature lands with its L0/L1 tests; `pytest -q` green + `ruff check` clean is the merge bar.
- L1 end-to-end must stay fast (<5s); it's the primary wiring regression net.
- L3 runs on demand before model-facing changes merge; its checker is a CLI (`annotator verify`) that doubles as the §6 dashboard's first metrics.
- New failure observed in a real run → first reproduce as an L1 adversarial fixture, then fix.

## Model serving

LLM access sits behind a minimal, provider-neutral `chat(messages, tools) -> response` interface — that interface is the primary seam. Two planned implementations:

1. **OpenAI-compatible HTTP** (boring default; v1's AgentClient shape): works for Moonshot/Kimi API direct, and later local serving (vLLM/llama.cpp for gemma 4 now, Laguna box when it arrives).
2. **omp RPC** — **proposed adapter, unverified.** omp is not an OpenAI-compatible HTTP server; integration would go through its RPC mode. The RPC wire protocol documents `set_host_tools`, and the TS helper wraps it as `setCustomTools`, but the Python `omp_rpc` client's exact host-tool/prompt/session semantics are **unconfirmed** (`omp_rpc` is not installed on this machine; validate against the `python/omp-rpc` README/source in the omp repo before coupling). If it holds, it uses the existing subscription login and our glossary tools become host tools called back into the annotator. Known machinery to tame regardless: session startup cost per run (amortized — one session per reading pass), ambient coding-agent features (compaction off — our digest owns memory; todo reminders; system prompt override), per-scene latency.

Start with (1) for the baseline entry-generation pass on Kimi; spike (2) when subscription-quota economics matter.
