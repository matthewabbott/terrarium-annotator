# terrarium-annotator Specification (v2)

LLM harness that reads a fiction corpus sequentially and builds a glossary/wiki of setting-specific terminology, characters, and places — with every entry grounded in verifiable source quotes. Primary corpus: Banished Quest (`banished.db`). Design permits future corpora.

**Status (2026-09-04): the deterministic core is built, tested (155 tests), and smoke-proven on real model runs.** v1 was removed 2026-09-02 (git history preserves it; its glossary survives as an anti-baseline in `data/exports/`).

## Core ideas

1. **The glossary is the memory.** Persistent knowledge lives in structured entries, not conversation history. Conversation resets between batches.
2. **The story digest is continuity.** An append-only log of per-batch gists, compressed by a lazy, thread-gated binary merge tree (OptMem's algorithm, reimplemented in-process), yields a fixed-budget digest: recent batches verbatim, old ones as coarse summaries. See `docs/design/v2-architecture.md` §1–2.
3. **The corpus is ground truth.** Every entry and every definition revision requires a verbatim quote from a cited post, mechanically verified at write time. Backlinks `(thread_id, post_id, quote, mode)` are structured data and feed future wiki generation.
4. **History is recomputed, not snapshotted.** Revisions, story log, and per-batch transcripts are append-only, so the exact context the annotator had at any edit can be reconstructed — enabling blame and rehydration (interrogating a past annotator about its reasoning) without v1's 31.9GB snapshot store. See `docs/design/v2-architecture.md` §4.
5. **Quality is layered.** Admission policy (shadow deferral), epistemic modes on evidence (narrated/claimed/inferred), salience-based serving, and post-hoc researcher/critic agents — see `docs/design/critic-salience-epistemics.md`.

## Glossary: two tiers

- **Tier 1 — card**: term, trigger keys/aliases, 1–2 sentence standalone gloss, tags. Auto-injected when a key matches the current batch; hard token budget (~15% of model context); scan depth 1; recursion ≤1; budget pressure drops by salience (mention count × tag prior), but an exact trigger always injects.
- **Tier 2 — page** (not yet built): full markdown body with `[[Term]]` cross-refs, backlink list, append-only revision history. Pulled on demand via `fetch_entry`; the read path never *depends* on the pull.

Definitions are versioned, never overwritten. Reverts are new revisions (`reverts` marker). Renames auto-alias the old title. Duplicate merges union evidence. Deletion is a human/audit action, not an agent tool.

## Agent tools

Annotator: `propose_entry`, `update_entry`, `add_alias` (all quote-gated, evidence carries epistemic mode), `fetch_entry`, `fetch_post`, `fetch_thread_range`, `recall_story`, `search_glossary`. Read-only surfaces (chat, future researcher interrogation) get a dispatcher allowlist that rejects write tools. Full list with guards: `docs/design/v2-architecture.md` §5.

## Verification

Two planes (see `docs/design/dev-verification.md`):

- **Wiring (L0–L4, in the dev loop)**: unit tests of the deterministic machinery; scripted-model end-to-end on fabricated corpora; stub/fake servers for HTTP and RPC clients; recorded replays with idempotence assertions; `terrarium-annotator verify` re-checks every stored quote against the corpus (8 invariant checks).
- **Quality (L5, on real output)**: gold-set recall/precision against the human-curated wiki thread pages 3–40 (`data/exports/gold-set.json`), tripwire metrics (entries/1k posts, injected-token share), human review of reports like `docs/worklog/2026-09-04-shadow-calibration.md`.

## Storage

- `banished.db` — corpus, **read-only, never modify** (keep an off-repo backup). `post(id, thread_id, body, time)`, `tag(post_id, name)`, `thread(id, title)`, `link`. The annotator reads `story_post`-tagged posts (9,813; a strict subset of `qm_post`; predicate is configurable). Thread order is chronological by OP-post time (thread IDs are opaque).
- `data/annotator.db` — read-write: entries, aliases, tags, revisions, sources, story log/tree, transcripts, run state/meta, deferred candidates. Fresh schema per epoch (architecture §7 lists live columns, verified against the DB).

## Autonomy

Designed for unattended multi-day runs: checkpoint per batch, pass-id-scoped resume, per-call retry on transient model/RPC failures, failure diagnostics recorded to the transcript, failed batches never checkpointed. Quota-aware pacing is the operator's job (5h windows).

## Out of scope (for now)

Wiki rendering/export to steelbea.me (format pinned in `docs/design/wiki-format.md`), embedding-based retrieval on the read path, multi-corpus support, automated curation/deletion beyond the human merge queue, the IRC community-review bot.
