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
| **F7: Snapshots** | :white_check_mark: Complete | SnapshotStore + summon workflow + CASCADE fix + 34 tests |
| **F7.5: Integration Tests** | :white_check_mark: Complete | Agent detection + e2e tests + 10 tests |
| **F8: Exporters** | :white_check_mark: Complete | JSON/YAML export + CLI + filtering + 14 tests |
| **F8.5: Inspect Commands** | :white_check_mark: Complete | status + inspect CLI + JSON output + 17 tests |

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

1. ThreadSummarizer (hybrid summaries with entry ID tracking)
2. ContextCompactor (60%/80%/90% thresholds, tiered compaction)
3. Cumulative summary merging
4. Tier 0.5: Intra-thread scene chunking (10 scenes per chunk)

### Dependencies

- F2 (TokenCounter)
- F4 (Runner integration)

### Compaction Tiers

| Tier | Trigger | Action |
|------|---------|--------|
| 0.5 | ≥80%, >2 completed chunks | Summarize oldest chunk → add to chunk_summaries |
| 1 | ≥80%, >1 completed threads | Summarize thread → merge into cumulative |
| 3 | ≥90% emergency | Trim thinking blocks |
| 4 | ≥90% emergency | Truncate old responses |

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

**Objective**: Point-in-time captures of annotation state with read-only summon capability.

### Scope

1. SnapshotStore implementation
2. Snapshot entry tracking (blame)
3. Summon/continue/dismiss workflow
4. Automatic checkpointing at thread boundaries
5. CASCADE fix for revision FK

### Dependencies

- F0 (snapshot tables)
- F2 (context serialization)
- F6 (thread completion trigger)

### Acceptance Criteria

- [x] SnapshotStore creates snapshots with context and entry state
- [x] Automatic checkpoint after curator completes at thread boundary
- [x] summon_snapshot loads read-only historical view
- [x] summon_continue records conversation in summoned context
- [x] summon_dismiss returns to normal operation
- [x] Write tools (create/update/delete) blocked during summon
- [x] Migration 004 fixes revision.entry_id CASCADE to SET NULL
- [x] Context serialization (to_dict/from_dict) for AnnotationContext
- [x] Context serialization (to_dict/from_dict) for CompactionState
- [x] All tests pass (34 tests: 20 storage + 14 tools)

---

## Feature 7.5: Integration Tests

**Objective**: End-to-end integration tests with live terrarium-agent server.

### Scope

1. Agent detection fixtures (auto-skip when unavailable)
2. Tier 1: Agent connection tests (health, chat, tokenize)
3. Tier 2: Tool calling tests (dispatch, create, search)
4. Tier 3: Full pipeline tests (context, multi-turn, annotation)

### Dependencies

- F0-F7 (all prior features)
- terrarium-agent server on localhost:8080

### Acceptance Criteria

- [x] `tests/conftest.py` with `agent_available` and `real_agent` fixtures
- [x] `pytest.ini` updated with `integration` marker
- [x] Tests skip gracefully when agent unavailable
- [x] Tests pass when agent is running (10 pass)
- [x] Unit tests still run without agent (`pytest -m "not integration"`)
- [x] All 217 tests pass (207 unit + 10 integration)

### Running Tests

```bash
# Run all tests (integration skipped if no agent)
pytest

# Run only unit tests
pytest -m "not integration"

# Run only integration tests (requires agent)
pytest -m integration
```

---

## Feature 8: Exporters

**Objective**: Human-readable glossary export with filtering.

### Scope

1. JSON exporter
2. YAML exporter
3. CLI export command with filtering

### Dependencies

- F0 (GlossaryStore.all_entries)

### Acceptance Criteria

- [x] `exporters/` module with base, json, yaml exporters
- [x] CLI `export` command with --format, --output, --status, --tags
- [x] Default filename generation (glossary_YYYY-MM-DD.{format})
- [x] Filtering by status and tags works correctly
- [x] PyYAML added to requirements.txt
- [x] All tests pass (14 exporter tests)

### CLI Usage

```bash
# Export all entries to JSON (default filename)
python -m terrarium_annotator.cli export

# Export to YAML
python -m terrarium_annotator.cli export --format yaml -o glossary.yaml

# Export only confirmed entries
python -m terrarium_annotator.cli export --status confirmed

# Export character entries only
python -m terrarium_annotator.cli export --tags character
```

---

## Feature 8.5: Inspect Commands

**Objective**: CLI commands for monitoring annotation run state and inspecting data.

### Scope

1. `status` command for quick overview
2. `inspect` subcommands for detailed examination
3. JSON output format option for machine-readable output

### Dependencies

- F0 (ProgressTracker, GlossaryStore, SnapshotStore)
- F8 (CLI patterns)

### Acceptance Criteria

- [x] `status` command shows run state, stats, glossary summary, snapshot count
- [x] `inspect snapshots` lists recent snapshots with metadata
- [x] `inspect snapshot <id>` shows full snapshot details including context
- [x] `inspect entries` lists recent entries with filters
- [x] `inspect entry <id>` shows full entry details
- [x] `inspect thread <id>` shows thread state and related data
- [x] All commands support `--format json` for machine-readable output
- [x] All 238 tests pass (221 existing + 17 new)

### CLI Usage

```bash
# Quick status overview
python -m terrarium_annotator.cli status
python -m terrarium_annotator.cli status --format json

# List recent snapshots
python -m terrarium_annotator.cli inspect snapshots
python -m terrarium_annotator.cli inspect snapshots --limit 5 --type checkpoint

# View specific snapshot
python -m terrarium_annotator.cli inspect snapshot 42

# List recent entries
python -m terrarium_annotator.cli inspect entries
python -m terrarium_annotator.cli inspect entries --status tentative --limit 20

# View specific entry
python -m terrarium_annotator.cli inspect entry 15

# View thread state
python -m terrarium_annotator.cli inspect thread 8
```

---

## Current Focus

**All planned features complete!**

Completed:
- F0 (Storage Layer) - 2026-01-02
- F1 (Corpus Layer) - 2026-01-02
- F2 (Context Layer) - 2026-01-02
- F3 (Tool Layer) - 2026-01-02
- F3.5 (Documentation & Logging) - 2026-01-02
- F4 (Runner MVP) - 2026-01-02
- F5 (Compaction) - 2026-01-02
- F6 (Curator) - 2026-01-02
- F7 (Snapshots) - 2026-01-02
- F7.5 (Integration Tests) - 2026-01-02
- F8 (Exporters) - 2026-01-02
- F8.5 (Inspect Commands) - 2026-01-02

Enhancements:
- F5.1 (Tier 0.5 Chunk Compaction) - 2026-01-03
  - Intra-thread scene chunking for long threads
  - Fixed doom loop bug in Tier 4
  - Simplified context structure (immediate thread merge)

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
| End-to-end smoke test | F7.5: Integration Tests |
| Codex schema validation | F0: Storage migrations |
| Benchmark harness | Future work |
