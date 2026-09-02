# terrarium-annotator Specification (v2)

LLM harness that reads a fiction corpus sequentially and builds a glossary/wiki of setting-specific terminology, characters, and places — with every entry grounded in verifiable source quotes. Primary corpus: Banished Quest (`banished.db`). Design permits future corpora.

**v2 is a clean-slate rewrite.** v1 code, schema, and docs were removed 2026-09-02 (git history preserves them). The v1 glossary output survives as `data/exports/glossary-v2-full.json` (3,623 entries) for use as an evaluation baseline.

## Core ideas

1. **The glossary is the memory.** Persistent knowledge lives in structured entries, not conversation history. Conversation resets between scenes.
2. **The story digest is continuity.** An append-only log of per-scene gists, compressed by a lazy, thread-aligned binary merge tree (OptMem's algorithm, reimplemented in-process), yields a fixed-budget digest: recent scenes verbatim, old ones as coarse summaries. See `docs/design/v2-architecture.md` §1–2.
3. **The corpus is ground truth.** Every entry and every definition revision requires a verbatim quote from a cited post, mechanically verified at write time. Backlinks `(thread_id, post_id, quote)` are structured data and feed future wiki generation.

4. **History is recomputed, not snapshotted.** Revisions, story log, and per-scene transcripts are append-only, so the exact context the annotator had at any edit can be reconstructed — enabling blame ("where in the story was this written") and rehydration (interrogating a past annotator about its reasoning) without v1's 31.9GB snapshot store. See `docs/design/v2-architecture.md` §4.

## Glossary: two tiers

- **Tier 1 — card**: term, trigger keys/aliases, 1–2 sentence standalone gloss, tags. Auto-injected when a key matches the current scene; hard token budget (~15% of context); scan depth 1; recursion ≤1.
- **Tier 2 — page**: full markdown body with `[[Term]]` cross-refs, backlink list, append-only revision history. Pulled on demand via `fetch_entry`; the read path never *depends* on the pull.

Definitions are versioned, never overwritten. Duplicate merges union evidence. Deletion is a human/audit action, not an agent tool.

## Agent tools

`propose_entry`, `update_entry`, `add_alias` (all quote-gated), `fetch_entry`, `fetch_post`, `fetch_thread_range`, `recall_story`. Full list with guards: `docs/design/v2-architecture.md` §5.

## Verification

Hard dashboard after every pass (provenance coverage, quote validity, dangling links, injected-token share ≤ budget, trigger-utility simulation, duplicate-cluster queue); soft evals on a fixed gold range (term-selection P/R/F1, faithfulness ≥0.85, comprehension QA). Details and rationale: `docs/design/v2-architecture.md` §6, evidence base in `docs/design/research-memory-rag.md` §4.

## Storage

- `banished.db` — corpus, **read-only, never modify** (currently irreplaceable: source server 502s). `post(id, thread_id, body, time)`, `tag(post_id, name)`, `thread(id, title)`, `link`. The annotator reads `story_post`-tagged posts (9,813; a strict subset of `qm_post`, which adds QM meta/vote chatter).
- `data/annotator.db` — read-write: entries, revisions, sources, story log/tree, transcripts, run state. Fresh schema (design doc §7); no migration from v1.

## Autonomy

Designed for unattended multi-day runs. Checkpoint = run position only; all knowledge is in append-only stores (glossary revisions, story log/tree, transcripts), which also makes any historical context reconstructible for blame/rehydration.

## Out of scope (for now)

Wiki rendering/export (steelbea.me examples pending), embedding-based retrieval on the read path, multi-corpus support, automated curation/deletion.
