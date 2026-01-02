# Roadmap

## Feature Status

| Feature | Status | Notes |
|---------|--------|-------|
| **F0: Storage Layer** | :white_check_mark: Complete | SQLite + migrations + FTS5 |
| **F1: Corpus Layer** | :white_check_mark: Complete | CorpusReader + SceneBatcher |
| **F2: Context Layer** | :white_check_mark: Complete | TokenCounter + AnnotationContext + ThreadSummarizer + ContextCompactor |
| **F3: Tool Layer** | :white_check_mark: Complete | ToolDispatcher + all 6 tools + 36 tests |
| **F3.5: Documentation & Logging** | :white_check_mark: Complete | Docstrings + basic logging |
| **F4: Runner MVP** | :white_check_mark: Complete | Scene-based iteration + tool call loop + 18 tests |
| **F5: Compaction** | :white_check_mark: Complete | ThreadSummarizer + ContextCompactor + 29 tests |
| **F6: Curator** | :white_check_mark: Complete | CuratorFork + decision parsing + 19 tests |
| F7: Snapshots | 🔴 Not Started | Save/load/summon + CASCADE fix |
| F8: Exporters | 🔴 Not Started | JSON/YAML export |

---

## Feature 0: Storage Layer

**Objective**: Replace JSON-based CodexStore with SQLite-backed GlossaryStore.

### Scope

1. Create `storage/` module structure
2. Implement schema migrations
3. Implement GlossaryStore with FTS5
4. Implement RevisionHistory
5. Implement ProgressTracker
6. Write tests

### Files to Create

```
src/terrarium_annotator/storage/
  __init__.py
  base.py           # Base DB connection, migration runner
  migrations.py     # Migration definitions
  glossary.py       # GlossaryStore
  revisions.py      # RevisionHistory
  state.py          # ProgressTracker
  exceptions.py     # Storage-specific exceptions
```

### Dependencies

- Python sqlite3 (stdlib)
- No external deps for storage layer

### Acceptance Criteria

- [x] `annotator.db` created with all tables from SCHEMA.md
- [x] FTS5 triggers working
- [x] GlossaryStore CRUD operations work
- [x] Revision history logged on updates/deletes
- [x] ProgressTracker saves/loads run state
- [x] All tests pass (27 tests)

### Migration from Existing Code

The existing `codex.py` uses JSON. We will:
1. Create new `storage/` module (don't modify `codex.py` yet)
2. Test new storage layer independently
3. Update `runner.py` to use new storage (in F4)
4. Delete `codex.py` after migration complete

---

## Feature 1: Corpus Layer

**Objective**: Scene-aware batching of corpus posts.

### Scope

1. Refactor CorpusReader for scene-aware iteration
2. Implement SceneBatcher (contiguous qm_post grouping)
3. Add thread boundary detection

### Dependencies

- F0 (for storage patterns, not direct dep)

---

## Feature 2: Context Layer

**Objective**: Token counting and context management.

### Scope

1. TokenCounter with vLLM + heuristic fallback
2. AnnotationContext for message building
3. Context serialization for snapshots

### Dependencies

- AgentClient (exists)

---

## Feature 3: Tool Layer

**Objective**: Agent tool implementations.

### Scope

1. ToolDispatcher routing
2. Glossary tools (CRUD)
3. Corpus tools (read_post, read_thread_range)
4. Tool schema definitions

### Dependencies

- F0 (GlossaryStore)
- F1 (CorpusReader)

---

## Feature 4: Runner MVP

**Objective**: Basic annotation loop without curator or compaction.

### Scope

1. State machine implementation
2. Scene iteration
3. Agent calling with tools
4. Glossary updates
5. Progress persistence

### Dependencies

- F0, F1, F2, F3

---

## Feature 5: Compaction

**Objective**: Context size management.

### Scope

1. ThreadSummarizer (hybrid summaries)
2. ContextCompactor (80% trigger, 70% target)
3. Cumulative summary merging

### Dependencies

- F2 (TokenCounter)
- F4 (Runner integration)

---

## Feature 6: Curator

**Objective**: End-of-thread evaluation of tentative entries.

### Scope

1. CuratorFork workflow
2. Context cloning
3. Decision parsing and application

### Dependencies

- F4 (thread boundary detection)
- F0 (revision logging)

### Acceptance Criteria

- [x] CuratorFork evaluates tentative entries at thread completion
- [x] CONFIRM/REJECT/MERGE/REVISE decisions implemented
- [x] Decisions logged to revision history
- [x] REJECT deletes entries from glossary
- [x] MERGE combines entries and deletes source
- [x] Conservative fallback (default to CONFIRM on errors)
- [x] All tests pass (19 tests)

---

## Feature 7: Snapshots

**Objective**: Context serialization and rehydration.

### Scope

1. SnapshotStore implementation
2. Snapshot entry tracking (blame)
3. Summon/continue/dismiss workflow

### Dependencies

- F0 (snapshot tables)
- F2 (context serialization)

---

## Feature 8: Exporters

**Objective**: Human-readable glossary export.

### Scope

1. JSON exporter
2. YAML exporter
3. CLI export command

### Dependencies

- F0 (GlossaryStore.all_entries)

---

## Current Focus

**Feature 7: Snapshots** is the next priority.

Completed:
- F0 (Storage Layer) - 2026-01-02
- F1 (Corpus Layer) - 2026-01-02
- F2 (Context Layer) - 2026-01-02
- F3 (Tool Layer) - 2026-01-02
- F3.5 (Documentation & Logging) - 2026-01-02
- F4 (Runner MVP) - 2026-01-02
- F5 (Compaction) - 2026-01-02
- F6 (Curator) - 2026-01-02

See `docs/worklog/` for session notes.

---

## Legacy Roadmap Items

The following items from the original roadmap are covered by the new feature breakdown:

| Original Item | New Location |
|---------------|--------------|
| Structured XML validation | F4: Runner MVP |
| Codex update parsing/validation | F3: Tool Layer |
| Rolling conversation summarization | F5: Compaction |
| Snapshot checkpoints | F7: Snapshots |
| Refinement pass mode | F4: Runner config |
| Manual fork interface | F7: Snapshots (summon) |
| Cross-linked terms (related_terms) | `[[markup]]` syntax in definitions |
| Bundled updates | F3: glossary_search tool |
| Unknown term tracking | F6: Curator (tentative entries) |
| End-to-end smoke test | Per-feature tests |
| Codex schema validation | F0: Storage migrations |
| Benchmark harness | Future work |
