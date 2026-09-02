# Context Handling Improvements & Complexity Busting

*Design document for terrarium-annotator — February 2026*

---

## Part 1: Context Handling Improvements

### 1. Semantic Retrieval Over Glossary/Codex

#### North Star

The agent should receive the *right* glossary entries for each scene — not just entries whose terms appear literally in the text. When a passage describes "the glowing orb pulsed with inner light," the agent should see the `archeota` entry even if that word doesn't appear. The glossary is the agent's long-term memory; it deserves the same retrieval power as a proper knowledge base.

#### Current State

`AnnotationContext.build_messages()` receives `relevant_entries: list[GlossaryEntry]` and formats them as `<known_glossary>` XML. The caller (runner) selects entries via `GlossaryStore` queries — currently term-matching and tag-based lookups. This works for exact mentions but misses semantic relationships.

#### Design

**Embedding index over glossary definitions:**

```
┌─────────────────────────────────┐
│  GlossaryIndex                  │
│  ─────────────                  │
│  - sqlite-vec or numpy store    │
│  - embed(term + definition)     │
│  - hybrid_search(query, k)      │
│    → BM25 over term+definition  │
│    → cosine over embeddings     │
│    → weighted merge             │
└─────────────────────────────────┘
```

**Hybrid search (BM25 + vector):**

- **BM25 component**: FTS5 index over `term || ' ' || definition` in `annotator.db`. Catches exact term mentions, capitalized proper nouns, specific jargon.
- **Vector component**: Embeddings of `"{term}: {definition}"` stored in a `vec0` virtual table (sqlite-vec) or numpy array. Catches semantic similarity: "glowing sphere" ↔ "archeota (luminous artifact)".
- **Merge**: `score = α × vector_score + (1-α) × bm25_score`, with α ≈ 0.6 (tunable).

**Embedding source**: Use the same vLLM endpoint if the model supports embeddings, or a small local model (e.g., `nomic-embed-text` via llama.cpp). Embedding at annotation time is cheap — the glossary grows slowly (~100s of entries per thread).

**When to re-embed**: On `glossary_create` and `glossary_update` tool calls. The `ToolDispatcher` already handles these — add an `on_entry_changed(entry_id)` hook that triggers async re-embedding.

**Query construction**: For each scene, construct a query from:
1. Scene text (truncated to ~500 chars)
2. Any novel terms detected by `NovelTermDetector`
3. Character names mentioned

Retrieve top-k entries (k=20-30) and inject as `<known_glossary>`.

#### Interface Changes

```python
# New: src/terrarium_annotator/retrieval/glossary_index.py

class GlossaryIndex:
    def __init__(self, db_path: Path, embed_fn: Callable[[str], list[float]]):
        ...

    def upsert(self, entry: GlossaryEntry) -> None:
        """Re-embed and store entry."""

    def delete(self, entry_id: int) -> None:
        """Remove from index."""

    def search(self, query: str, k: int = 25,
               alpha: float = 0.6) -> list[tuple[GlossaryEntry, float]]:
        """Hybrid BM25 + vector search."""

    def rebuild(self) -> None:
        """Full reindex from GlossaryStore."""
```

```python
# Modified: runner.py — replace direct glossary queries with index search

# Before:
relevant = self.glossary.search_by_terms(scene_terms)

# After:
query = self._build_retrieval_query(scene, detected_terms)
relevant = [entry for entry, score in self.index.search(query, k=25)]
```

#### Implementation Plan

| # | Task | Files | Complexity |
|---|------|-------|------------|
| 1 | Add sqlite-vec / FTS5 tables to `annotator.db` | `storage/migrations.py` | Low |
| 2 | Implement `GlossaryIndex` class | New: `retrieval/glossary_index.py` | Medium |
| 3 | Hook index updates into `ToolDispatcher` | `tools.py` | Low |
| 4 | Add embedding function (local model or vLLM) | New: `retrieval/embeddings.py` | Medium |
| 5 | Replace entry selection in runner with index search | `runner.py` | Low |
| 6 | Add `--rebuild-index` CLI flag for re-embedding | `__main__.py` | Low |
| 7 | Tune α and k with A/B comparison | — | Ongoing |

**Dependencies**: sqlite-vec Python bindings (`pip install sqlite-vec`), embedding model.

---

### 2. Pre-Compaction Flush

#### North Star

Compaction should never destroy unrealized intent. If the agent was building toward a glossary entry across several turns and compaction fires, that in-progress understanding is lost. A flush turn gives the agent one last chance to commit its work before context is summarized away.

#### Current State

`ContextCompactor.compact()` is called from `runner.py` when token thresholds are crossed. It immediately begins summarizing and removing turns. There is no agent interaction before compaction.

#### Design

**Flush mechanism**: Before `compact()` modifies any messages, inject a single-turn exchange:

```
System: "Context compaction imminent. You have one turn to emit any pending 
glossary/codex updates before older conversation is summarized. If you have 
no pending updates, respond with FLUSH_OK."

Agent: [emits tool calls or FLUSH_OK]
```

**Implementation in runner's tool loop:**

```python
# In runner.py, before calling compactor.compact():

def _pre_compaction_flush(self, context: AnnotationContext, messages: list[dict]) -> list[dict]:
    """Give agent one turn to emit pending tool calls before compaction."""
    flush_messages = list(messages)
    flush_messages.append({
        "role": "system",
        "content": PRE_COMPACTION_FLUSH_PROMPT,
    })
    flush_messages.append({
        "role": "user",
        "content": "Emit any pending glossary/codex updates now, or respond FLUSH_OK.",
    })

    response = self.agent.chat(
        messages=flush_messages,
        tools=self.tool_dispatcher.tool_schemas,
        max_tokens=self.config.max_tokens,
        temperature=0.3,
    )

    # Process any tool calls from the flush turn
    if response.tool_calls:
        for call in response.tool_calls:
            self.tool_dispatcher.dispatch(call)

    # Record the flush turn in context history
    context.record_turn("assistant", response.message.get("content", ""),
                        thread_id=..., scene_index=...)

    return context.build_messages(...)
```

**Constraints:**
- Max 1 flush turn per compaction cycle (prevent infinite loops)
- `max_tokens` capped low (512) to minimize flush cost
- Flush only fires at Tier 0.5+ (don't flush when under soft threshold)
- Track flush in `CompactionState` to prevent re-triggering

**Cost**: One extra inference call per compaction event. At ~7 scenes per chunk with 80% threshold, this is rare — maybe once every 20-50 scenes.

#### Implementation Plan

| # | Task | Files | Complexity |
|---|------|-------|------------|
| 1 | Add `PRE_COMPACTION_FLUSH_PROMPT` | `context/prompts.py` | Low |
| 2 | Add `flush_triggered` flag to `CompactionState` | `context/compactor.py` | Low |
| 3 | Implement `_pre_compaction_flush()` in runner | `runner.py` | Medium |
| 4 | Wire flush into compaction decision path | `runner.py` | Low |
| 5 | Add tests for flush → tool call → compaction flow | `tests/` | Medium |

---

### 3. Structured Cumulative Summary

#### North Star

A single cumulative summary string inevitably drifts — each LLM rewrite loses detail and accumulates bias. Instead, maintain structured sections that are updated independently, so character information doesn't compete with world-building for summary space.

#### Current State

`CompactionState.cumulative_summary` is a single `str`. `ContextCompactor._merge_summaries()` uses an LLM call (or heuristic concatenation) to merge thread summaries into this blob. The merge prompt (`CUMULATIVE_SUMMARY_PROMPT`) asks for a 500-word cohesive summary.

#### Design

**Replace single string with structured sections:**

```python
@dataclass
class StructuredSummary:
    """Cumulative summary broken into independently-managed sections."""

    characters: str = ""        # Who's who, relationships, arcs
    active_plot: str = ""       # Current narrative threads, unresolved tensions
    world_state: str = ""       # Locations, factions, political landscape
    mechanics: str = ""         # Magic systems, rules, cultivation stages
    annotation_progress: str = ""  # What's been glossarized, coverage gaps

    def to_xml(self) -> str:
        sections = []
        for field_name in ["characters", "active_plot", "world_state",
                          "mechanics", "annotation_progress"]:
            content = getattr(self, field_name)
            if content:
                sections.append(f"<{field_name}>{content}</{field_name}>")
        return f"<cumulative_summary>\n{''.join(sections)}\n</cumulative_summary>"

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in
                ["characters", "active_plot", "world_state",
                 "mechanics", "annotation_progress"]}

    @classmethod
    def from_dict(cls, data: dict) -> StructuredSummary:
        return cls(**{k: v for k, v in data.items()
                     if k in cls.__dataclass_fields__})
```

**Section-aware merging**: When merging a new thread summary, the LLM updates only the relevant sections:

```python
STRUCTURED_MERGE_PROMPT = """Given a new thread summary and existing structured summary sections,
update ONLY the sections that have new information. Return JSON with the section names as keys.

Existing sections:
{sections_xml}

New thread summary:
{thread_summary}

Return JSON: {"characters": "...", "active_plot": "...", ...}
Only include sections that need updating. Omit unchanged sections."""
```

**Benefits:**
- Each section stays focused — character info doesn't crowd out mechanics
- Sections can have independent size budgets
- Easier to audit what the summary "knows"
- Natural alignment with glossary tags (character entries ↔ characters section)

#### Migration

Existing `cumulative_summary` string → parse into sections via one-time LLM call, or start fresh on next run.

#### Implementation Plan

| # | Task | Files | Complexity |
|---|------|-------|------------|
| 1 | Define `StructuredSummary` dataclass | `context/models.py` | Low |
| 2 | Update `CompactionState` to use `StructuredSummary` | `context/compactor.py` | Medium |
| 3 | Write section-aware merge prompt | `context/prompts.py` | Low |
| 4 | Update `_merge_summaries()` for structured output | `context/compactor.py` | Medium |
| 5 | Update `AnnotationContext.build_messages()` XML formatting | `context/annotation.py` | Low |
| 6 | Update snapshot serialization | `storage/snapshots.py` | Low |
| 7 | Migration helper for existing summary strings | `storage/migrations.py` | Low |

---

### 4. Snapshot Search

#### North Star

When the agent needs historical context (via `explicate` or `summon`), it shouldn't have to guess which snapshot to load. Snapshots should be semantically searchable — "find the snapshot where Dawn's true nature was revealed" should return the right checkpoint.

#### Current State

`SnapshotStore` stores serialized `AnnotationContext` objects keyed by type, thread_id, and timestamp. `list_snapshots` returns metadata. The agent must manually browse and select. The `explicate` protocol fuzzy-matches quotes to entry sections but doesn't search snapshots.

#### Design

**Snapshot metadata index:**

Each snapshot gets a text summary (already partially available from thread summaries) plus an embedding. Store in a `snapshot_index` table:

```sql
CREATE TABLE snapshot_index (
    snapshot_id INTEGER PRIMARY KEY REFERENCES snapshots(id),
    summary_text TEXT NOT NULL,
    thread_id INTEGER,
    post_range TEXT,  -- "1234-5678"
    created_at TEXT
);

-- FTS5 for keyword search
CREATE VIRTUAL TABLE snapshot_index_fts USING fts5(
    summary_text, content=snapshot_index, content_rowid=snapshot_id
);

-- sqlite-vec for semantic search (if available)
CREATE VIRTUAL TABLE snapshot_index_vec USING vec0(
    embedding float[384]
);
```

**Auto-summarize on snapshot creation**: When `SnapshotStore.create_checkpoint()` fires, generate a 1-2 sentence summary of what's in the snapshot (use the most recent thread summary + scene range).

**Search tool for agent:**

```python
# Add to agent tools:
def snapshot_search(query: str, k: int = 5) -> list[dict]:
    """Search snapshots by content. Returns snapshot metadata + summaries."""
```

**Integration with explicate**: When `explicate` can't find a source post via blame tracking, fall back to snapshot search using the quoted text as query.

#### Implementation Plan

| # | Task | Files | Complexity |
|---|------|-------|------------|
| 1 | Add `snapshot_index` table | `storage/migrations.py` | Low |
| 2 | Auto-summarize on checkpoint creation | `storage/snapshots.py` | Medium |
| 3 | Add hybrid search over snapshot summaries | New: `retrieval/snapshot_index.py` | Medium |
| 4 | Add `snapshot_search` tool | `tools.py` | Low |
| 5 | Wire into explicate fallback | `tools.py` (explicate handler) | Low |

---

## Part 2: Complexity Busting

### North Star

The current system was engineered for a specific model (Qwen3-80B) with specific limitations: ~128K effective context, imperfect instruction following, tendency to lose track of long conversations. Much of the compaction machinery, external curator checks, and multi-mode processing exists to work around these limitations.

With stronger models (GLM-4 Flash, Kimi K2.5, future 1M+ context models), we should be able to dramatically simplify the pipeline. The goal: **reader mode as the default, compaction as a fallback, and the glossary as the sole persistent memory.**

### What Could Be Simplified

#### Safety Gates to Re-evaluate

| Gate | Current Purpose | With Stronger Model |
|------|----------------|-------------------|
| Tiered compaction (0.5→5) | Prevent context overflow | With 1M context, most corpora fit without compaction. Keep Tier 1 (thread summary) and Tier 5 (nuclear) as emergency only. Remove Tiers 0.5, 0.5b, 0.5c, 3, 4. |
| `NovelTermDetector` | Pre-filter terms to reduce false positives | A stronger model can identify novel terms in-context without pre-detection. Make optional. |
| `CuratorFork` | Post-hoc quality control on tentative entries | A stronger model produces fewer false positives. Run curator less frequently (every N threads instead of every thread), or make it a batch post-processing step. |
| `max_tool_rounds=10` | Prevent runaway tool loops | Keep but raise to 20-30. Stronger models self-terminate more reliably. |
| Scene batching (`SceneBatcher`) | Feed manageable chunks | With larger context, feed entire threads (already done in thread mode). |
| Chunk summaries | Intra-thread memory | Unnecessary with sufficient context window. |

#### Reader Mode as Default Pipeline

Reader mode (`build_reader_messages`) is already the cleanest path:

```
For each scene:
  1. Build: system_prompt + story_summary + glossary_context + scene
  2. Agent reads, emits tool calls
  3. Reset conversation (glossary persists)
  4. Update story_summary periodically
```

This is architecturally elegant: **the glossary IS the memory**. No compaction needed because conversation doesn't accumulate. The only growing state is the story summary, which can be structured (Part 1, §3).

**To make reader mode the primary pipeline:**

1. Improve glossary retrieval (Part 1, §1) so the agent gets rich context per scene
2. Use structured summary (Part 1, §3) for story continuity
3. Remove scene-mode compaction entirely — it's unnecessary when conversation resets
4. Keep thread-mode as an option for models that benefit from multi-scene context accumulation

#### Minimal Viable Pipeline

```
┌──────────────────────────────────────────────┐
│  Minimal Pipeline (strong model)             │
│                                              │
│  corpus → scene iterator                     │
│       ↓                                      │
│  For each scene:                             │
│    query = scene_text + detected_terms       │
│    entries = glossary_index.search(query)     │
│    summary = structured_summary.to_xml()     │
│    messages = [system, summary, entries,      │
│                scene]                         │
│    response = llm(messages, tools)            │
│    process tool calls → glossary updates      │
│    if thread_boundary:                        │
│      update structured_summary               │
│                                              │
│  Post-run:                                   │
│    batch curator pass (optional)             │
│    export glossary                            │
└──────────────────────────────────────────────┘

vs.

┌──────────────────────────────────────────────┐
│  Current Pipeline                            │
│                                              │
│  corpus → scene/thread iterator              │
│       ↓                                      │
│  AnnotationContext (accumulating history)     │
│  CompactionState (6 tiers)                   │
│  NovelTermDetector (pre-filtering)           │
│  ToolDispatcher (10-round limit)             │
│  CuratorFork (per-thread)                    │
│  SnapshotStore (checkpoints)                 │
│  ThreadSummarizer + ChunkSummarizer          │
│  TokenCounter (heuristic + vLLM)             │
│                                              │
│  Much more state to manage and debug.        │
└──────────────────────────────────────────────┘
```

The minimal pipeline has **one moving part** (structured summary) instead of **seven** (compaction state, conversation history, chunk summaries, thread summaries, cumulative summary, snapshots, curator state).

### Comparative Evaluation Framework

To validate that simplification doesn't hurt quality, we need a systematic way to compare runs.

#### Framework Design

```python
# New: src/terrarium_annotator/eval/comparator.py

@dataclass
class EvalConfig:
    """Configuration for a single evaluation run."""
    name: str                    # "reader-mode-gpt4" or "thread-mode-qwen3"
    corpus_range: tuple[int, int]  # (start_thread_id, end_thread_id)
    runner_config: RunnerConfig
    model_endpoint: str
    description: str = ""

@dataclass
class EvalResult:
    """Metrics from a single run."""
    config_name: str
    entries_created: int
    entries_by_tag: dict[str, int]      # character: 12, location: 8, ...
    entries_by_status: dict[str, int]   # confirmed: 45, tentative: 30
    avg_definition_length: float
    cross_ref_count: int                # [[Term]] references
    duplicate_candidates: int           # entries with >0.9 cosine similarity
    tool_calls_total: int
    inference_calls: int
    total_tokens: int
    wall_time_seconds: float
    # Qualitative (human-scored after)
    sample_entries: list[GlossaryEntry]  # Random sample for human review

class EvalComparator:
    """Run multiple configurations on the same corpus range and compare."""

    def run_eval(self, configs: list[EvalConfig]) -> list[EvalResult]:
        """Run each config on the specified corpus range."""

    def diff_entries(self, a: EvalResult, b: EvalResult) -> EntryDiff:
        """Compare glossary outputs: shared terms, unique to A, unique to B."""

    def export_report(self, results: list[EvalResult], path: Path) -> None:
        """Write markdown comparison report."""
```

#### Key Metrics

**Quantitative (automated):**
- Entry count (total, by tag, by status)
- Duplicate rate (entries with high cosine similarity to each other)
- Cross-reference density
- Definition quality proxy: avg length, presence of source attribution
- Cost: total tokens, inference calls, wall time

**Qualitative (human review):**
- Random sample of 20 entries per run → score 1-5 on accuracy, completeness, usefulness
- Side-by-side diff of shared terms — which definition is better?
- False positive rate: how many entries shouldn't exist?
- Coverage: did it miss obvious terms? (Check against a gold-standard list for the test range)

#### Test Protocol

1. Pick a 3-5 thread corpus range that has known complexity (new characters, magic system reveals, etc.)
2. Run with current pipeline (baseline)
3. Run with reader mode + semantic retrieval
4. Run with minimal pipeline
5. Compare using `EvalComparator`
6. Human scores a sample of entries from each run

#### Implementation Plan

| # | Task | Files | Complexity |
|---|------|-------|------------|
| 1 | Define `EvalConfig` and `EvalResult` | New: `eval/models.py` | Low |
| 2 | Implement `EvalComparator.run_eval()` | New: `eval/comparator.py` | Medium |
| 3 | Implement `diff_entries()` | `eval/comparator.py` | Medium |
| 4 | Implement `export_report()` markdown output | `eval/comparator.py` | Low |
| 5 | CLI integration: `annotator eval --config eval.yaml` | `__main__.py` | Low |
| 6 | Create gold-standard entry list for test range | Manual | Medium |
| 7 | Run baseline + 2-3 variants, write up results | — | Ongoing |

### Simplification Roadmap (Ordered)

1. **Phase 1**: Implement semantic retrieval (§1) — prerequisite for reader mode working well
2. **Phase 2**: Implement structured summary (§3) — prerequisite for reader mode continuity
3. **Phase 3**: Build eval framework — prerequisite for validating any simplification
4. **Phase 4**: Run comparative evals with current vs. reader-mode pipeline
5. **Phase 5**: Based on results, strip unused compaction tiers, make curator optional
6. **Phase 6**: Make reader mode the default, keep thread mode as `--legacy` flag

Each phase is independently valuable and testable.
