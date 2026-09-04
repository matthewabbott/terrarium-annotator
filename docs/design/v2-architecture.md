# terrarium-annotator v2 Architecture

*Design document — 2026-09-02. Companion: `research-memory-rag.md` (evidence base).*

v2 is a hard cutover from v1. v1's failure modes, which every decision below answers:

1. **Overzealous term extraction** — local models created entries for dice rolls, platform terms, and action phrases ("spend Vys"), plus fragment duplicates ("Vys energy", "Vys pool").
2. **Context mismanagement** — six compaction tiers, snapshots, curator forks, and accumulating conversation history; more machinery than the task needed.

Guiding principle for v2: **the glossary is the memory; the story digest is the continuity; the corpus is the ground truth.** Everything the agent knows comes from three deterministic sources, and every claim in the glossary carries a verifiable pointer back into the corpus.

---

## 1. OptMem assessment

[OptMem](https://github.com/VictorTaelin/OptMem) is a 426-token prompt + one dependency-free Python file (`memo`). Mechanism, from the source:

- **Append-only log** of fixed-width (320-byte) records; position *is* identity, so every lookup is one seek.
- **Lazy binary merge tree**: blocks of 2^n adjacent memories are compressed into one-line summaries (`#0-1`, `#2-3`, `#0-3`, …). Merges are performed *by the agent itself* when `note` reports them due; nothing runs in the background.
- **Age-decaying cover** (`cover(T, budget)`): at wake, tile history with aligned power-of-two blocks, keeping blocks whole iff `size ≤ α × age`. Recent memories stay verbatim; ancient ones collapse to one line. `WAKE_LINES` (default 96 ≈ 8k tokens) is a pure *reading* budget — resizeable in either direction with zero recomputation.
- **Retrieval is regex over the raw log** (`recall`), plus tree navigation (`zoom`). No embeddings.
- TREE/ is a cache, fully rebuildable from LOG.txt. Crash safety via partial-record truncation and a file lock.
- **Merge mechanics** (verified in source): `nap` settles the smallest pending block first — blocks of ≤16 memories compress from raw log lines, larger blocks from their two halves' summaries — with the prompt "Keep what has lasting effect, drop what does not. Invent nothing." Blocks settle strictly in order. `wake` refuses to render if a needed summary is missing and prints exactly the merge to do first. `forget` truncates a block and everything built on it; the log is never touched, so any summary is rebuildable.

### Verdict: adopt the algorithm, not the tool

The data structure fits sequential fiction reading almost perfectly: the corpus is ordered, so OptMem's time-decay **is** narrative-distance decay — thread 200 needs thread 199 verbatim and thread 12 as a gist. The merge operation ("compress these two short texts into one line") is the single easiest LLM task imaginable, which matters because our summarizer may be a small local model (BOOOOKSCORE: hierarchical merging beats incremental running-summary updates for coherence on weaker models).

Four deviations from stock OptMem:

1. **Reimplement in-process, don't shell out.** The harness drives the agent loop; merges become harness-scheduled LLM calls, not agent chores via a CLI. We keep the algorithm (fixed records, lazy tree, budgeted cover) and drop the subprocess/prompt-block integration.
2. **Thread-gated settlement on aligned blocks.** *(Refined at T2 implementation, 2026-09-03.)* Blocks stay aligned powers of two over the global log exactly as OptMem — but a block becomes *eligible to settle* only once every entry in it belongs to a closed thread, so merges never touch the thread being read (a block straddling a boundary waits for both threads). This preserves block alignment, which boundary-aligned blocks would break. One log entry per batch of story posts. Additionally, `cover()` falls back to raw log entries when a needed summary is unsettled rather than refusing like OptMem's wake — the digest is always renderable; the budget is guaranteed only when the tree is settled.
3. **Glossary lives outside the tree.** Entities must not decay with age. Glossary cards (§3) are always-on core context; the tree only carries *plot continuity*.
4. **Retrieval = deterministic, not semantic.** Keyword/grep over the log + glossary backlink index (§4). Small models can't be trusted to manage embeddings-based self-paging (MemGPT's "memory blindness"); deterministic paging degrades gracefully.

What we deliberately do **not** adopt: the 280-byte entry cap (scene gists get ~1–3 lines / ~500 bytes), the agent-facing CLI, and regex-only recall as the sole lookup path (backlinks cover entity lookup).

---

## 2. Story memory: the reading loop

```
For each batch (fixed-size run of story_post posts in a thread; plan T1):
  1. Assemble context:
       system prompt
     + story digest      (budgeted cover of the summary tree, finest near present)
     + glossary cards    (auto-injected on trigger match, §3)
     + current scene text
  2. Agent reads; may call tools (§5). Write-path tools require evidence quotes.
  3. Harness appends one gist entry to the story log
     (agent-written 1-3 line gist, or harness-extracted fallback).
At thread close:
  4. Harness issues merge calls for newly settleable tree blocks (§1:
     aligned blocks whose entries are all in closed threads, in order).
  5. Periodic re-verification queue updates (§6).
```

Conversation state resets between scenes (v1 reader mode's one good idea). Persistent state is exactly: story log + tree, glossary, per-scene transcripts, run position. No conversation compaction — the digest *is* the compaction, and it never exceeds its budget by construction. Historical context is never *stored* as snapshots; it is *recomputed* from append-only stores (§4, Rehydration).

---

## 3. Two-tier glossary

### Tier 1 — cards (auto-injected)

```
card = {
  term: str,              # canonical display name, "Dawn (character)" if disambiguated
  keys: [str],            # trigger surface forms: exact, case-sensitive, aliases
  gloss: str,             # 1-2 sentence STANDALONE definition (no [[refs]] expanded)
  tags: [str],            # character | location | mechanic | faction | item | ...
}
```

Injection is deterministic, SillyTavern World Info-style:

- **Trigger**: a key matches the current scene text (keyword, case-sensitive; whole-word).
- **Scan depth 1**: only the scene being read. The reader always faces fresh text; deep scans buy nothing.
- **Hard token budget**: ≤15% of model context. Overflow drops lowest-priority cards (priority = recently-updated, then shortest).
- **Recursion ≤1**: an injected card's gloss may trigger one more round, then stop.
- Cards must be **standalone** — only the gloss is injected, so the gloss must make sense without the term list or other cards.

This directly attacks v1 failure mode #1: overzealous extraction now *costs* budget and shows up in the injected-token-share metric (§6) before it poisons anything else.

### Tier 2 — pages (tool-pulled, never depended upon)

Full wiki-style entry, fetched on demand via `fetch_entry(term)`:

```
page = card + {
  body: markdown,         # extended definition; cross-refs as [[Term]]
  backlinks: [source],    # §4 — where this term appeared
  revisions: [...],       # append-only definition history with evidence
}
```

Page layout follows the live steelbea.me wiki's conventions — see `wiki-format.md` (type namespaces, quote-lead, fact-vs-speculation sections, chronological Plot narrative generated from `entry_source`, archive-URL backlinks via `thread_id`).

Small models fail to call needed tools 30–50% of the time (research §2), so the system must **degrade to card-only**, never fail: cards carry a one-line "full page available" hint, and nothing in the read path *requires* a page fetch. For frontier API models the balance can shift toward pull; for local models bias toward inject. Either way pages stay out of default context.

### Definitions are versioned, never overwritten

Canon drifts; fiction retcons. Every definition change appends a revision with its own evidence quotes (A-MEM's "memory evolution", Mem0's add-only turn). The card gloss is derived from the latest revision. Merges of duplicate entries **union** evidence lists — never discard.

---

## 4. Provenance and backlinks

Backlinks are structured data, not prose:

```
source = { thread_id, post_id, scene_index, quote, pass_id, added_at }
```

- **Quote-then-verify at write time**: `propose_entry` / `update_entry` require a verbatim quote from the current scene. Mechanical check before commit: the quote is a substring of the cited post *and* contains the term (or a registered alias). No quote, no entry.
- **Load-bearing discipline** (ALCE citation precision): a backlink that doesn't support the definition is noise; the LLM-judge audit (§6) samples and scores this.
- **Blame and retraction**: `pass_id` identifies the reading pass/prompt version that produced an entry. Bad prompt? Delete by `pass_id`. Non-canon thread? Delete by thread.
- Wiki generation (future) reads these directly: every entry page links its source threads/posts; `[[Term]]` cross-refs resolve against the canonical term table.

Corpus-side grounding: `banished.db` is immutable — `post(id, thread_id, body, time)`, `tag(post_id, name)`, `thread(id, title)`. Tags (distinct posts): `story_post` 9,813 (the QM's actual story updates; a strict subset of `qm_post`), `qm_post` 12,721 (adds 2,908 posts of QM meta/vote chatter), `op_post` 276 threads. (Tag rows are slightly higher — a few dozen posts are double-tagged.) v1 read `qm_post`; **v2's read predicate defaults to `story_post` and is a config knob**, so meta-heavy passes remain possible. **The DB is currently irreplaceable** (source server 502s) — read-only access, and keep a backup off this repo. Quotes are re-verifiable offline, forever.

### Blame: git-history for glossary entries

Every revision records *where in the story* it was written: `thread_id`, scene range, and `log_seq` (story-log position), plus the evidence quote. This gives entry pages a genuine history view — "definition as of thread 87" — and gives wiki generation the entry↔thread backlinks in both directions (entry page lists source threads; thread page lists entries first/last touched there).

### Rehydration: interrogating a past annotator

Requirement (Matt, 2026-09-02): go back to the annotator that changed an entry and ask *why* — with the context it actually had.

v1 did this with serialized context snapshots (the 31.9GB snapshot store). v2 makes snapshots **virtual**: per-scene context is small and fully determined by append-only state, so the context at any position P is *recomputed*, not stored:

```
context(P) = system prompt (versioned by pass_id)
           + digest(P)   = budgeted cover over story_log[≤P] via story_tree
           + cards(P)    = glossary folded from revisions[≤P], triggered by scene(P)
           + scene(P)    = corpus text (immutable)
```

For this to work, three stores must be replayable: `story_log` (append-only ✓), `story_tree` (**write-once**: nodes are never rewritten; a `forget`-style rebuild bumps `tree_version`, recorded on each revision), and `revision` (append-only ✓). One thing inputs alone can't reconstruct is the agent's *reasoning*, so the harness also appends a per-scene **`transcript`** (agent messages + tool calls, a few KB per scene) — cheap because v2 context is budget-bounded, unlike v1's accumulating 100K-token conversations.

"Talk to the annotator that made edit E" = host-side command (not an agent tool): reconstruct `context(E)`, replay the transcript up to E — **replayed tool calls are rendered as recorded messages/results, never re-executed**; only the new interrogation turn gets live tools, and those are read-only (fetch/recall; no glossary writes). Fallback if reconstruction ever proves fragile: materialize snapshots at thread boundaries only — small in v2, since context is budget-bounded.

---

## 5. Agent tools (complete list)

| Tool | Purpose | Guard |
|------|---------|-------|
| `propose_entry` | Create card+page with initial definition | Requires quote from current scene; rejected if term/alias already exists |
| `update_entry` | Append a revision (new/extended definition) | Requires quote; old revision retained |
| `add_alias` | Register a surface form for an existing entry | Same quote gate (verbatim quote containing the alias); recorded to `entry_source` with NULL revision (not a definition change) |
| `fetch_entry` | Pull full tier-2 page (body, backlinks, revisions) | — |
| `fetch_post` / `fetch_thread_range` | Re-read source posts (ReadAgent lookup) | Read-only, corpus only |
| `recall_story` | Grep the story log / zoom tree nodes | Read-only |

That's all. No delete (deletion is a human/audit action). Rehydration/interrogation (§4) is host-side, not an agent tool.

---

## 6. Verification

### Hard criteria — automated dashboard, run after every reading pass (must all pass)

| Metric | Target | Catches |
|--------|--------|---------|
| Provenance coverage: entries with ≥1 mechanically verified quote | = 100% at write time; ≥95% after merges | Ungrounded entries |
| Quote validity: quote is substring of cited post and contains term/alias | 100% | Fabricated sources |
| Dangling `[[links]]` | 0 | Broken wiki graph |
| Injected-token share of context | ≤15% budget, trend flat | **v1 overzealousness tripwire** |
| Entries per 1k posts | within agreed band (calibrate on eval range) | Over/under-extraction |
| Trigger-utility simulation: fraction of injections where the card changes interpretation of the post | ≥ threshold TBD by labeling | Precision that matters downstream |
| Duplicate clusters (embedding blocking → human merge queue) | queue drained; no auto-merge | "Vys energy"/"Vys pool" fragments |
| Backlink referential integrity (post IDs exist) | 100% | Corrupt provenance |

### Soft criteria — periodic eval on a fixed range (3–5 threads with known reveals)

- **Gold set**: ~100 hand-labeled candidate terms (worth / not-worth an entry) → type-level precision/recall/F1 for term selection.
- **Definition faithfulness**: decompose definitions into atomic claims; judge each against the entry's stored quotes (RAGAS-style). Target ≥0.85 supported. ARES-style PPI calibration keeps the human-label budget at a few hundred.
- **Comprehension QA**: 50–100 questions requiring setting knowledge, answered with glossary + digest only; failures identify recall holes (adversarial second pass with a different prompt/model as an alternative recall probe).
- **False-merge rate** on the merge queue (fiction trope: distinct characters sharing names) — every merge needs pair-judgment citing both entries' evidence.

The v1 glossary export (`data/exports/glossary-v2-full.json`, 3,623 entries) is an **anti-baseline** for the first eval run: per Matt it is full of false positives, so it is not a quality reference. Use it to (a) confirm v2 beats its precision/junk rate and (b) calibrate junk detectors against its known failure categories (dice rolls, platform terms, action phrases, fragment duplicates). Its provenance coverage (100% of entries have sources) is the one property worth matching.

---

## 7. Storage (fresh `annotator.db`, no migration from v1)

| Table | Contents |
|-------|----------|
| `entry` | id, term, term_normalized, gloss, status(tentative/confirmed), pass_id, created_at, updated_at (+ entry_alias, entry_tag side tables) |
| `revision` | id, entry_id, gloss, thread_id, batch_lo, batch_hi, log_seq, pass_id, tree_version, note, reverts, created_at — append-only |
| `entry_source` | id, entry_id, revision_id (NULL for alias registrations), thread_id, post_id, quote, mode (`narrated`/`claimed`/`inferred` — critic-salience-epistemics §2), created_at |
| `story_log` | seq, thread_id, batch_lo, batch_hi, gist, created_at — append-only |
| `story_tree` | lo, hi (log seq range), summary, tree_version — **write-once**, never rewritten in place |
| `transcript` | id, pass_id, thread_id, batch_index, log_seq, role, content, tool_calls, created_at — per-batch agent output, append-only |
| `deferred_candidate` | (shadow-only) id, term, term_normalized, quote, post_id, thread_id, created_at — proposals the specificity gate would defer; logged, never blocked (calibration verdict 2026-09-04: lexical heuristic NO-GO, stays shadow) |
| `run_state` | id=1 singleton: pass_id, thread_id, batch_index, updated_at |
| `run_meta` | key, value — run configuration (budget compliance is verified against it) |

Column lists verified against the live schema (annotator-shadow.db, 2026-09-04).

FTS5 over `entry(term, gloss)` for card lookup; no vector index until the duplicate-detection queue needs one (embedding blocking is an offline audit concern, not a read-path concern).

## 8. Explicitly out of scope (for now)

- Wiki page rendering/export to steelbea.me (format conventions pinned in `wiki-format.md`, harvested 2026-09-02 when the site came back online; generation itself is post-glossary work)
- Semantic/embedding retrieval on the read path
- Multi-corpus abstraction (building codes etc.) — design permits, implement when second corpus arrives
- Curator fork / batch curator — replaced by write-time verification + audit dashboard; reconsider only if metrics say so
