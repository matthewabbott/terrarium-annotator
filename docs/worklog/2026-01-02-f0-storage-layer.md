# 2026-01-02 - Feature 0: Storage Layer Implementation

## Objective

Implement the SQLite-backed storage layer per docs/ROADMAP.md Feature 0 scope.

## Context

State of the codebase when I started:
- Current feature in progress: F0 (Storage Layer)
- Last completed work: Documentation infrastructure (previous session)
- Known issues: None

Existing code uses JSON files (`codex.py`, `state.py`). Per ADR 001, we're replacing with SQLite.

## Work Done

- [x] Create `storage/` module structure
- [x] Implement `base.py` (DB connection, migration runner)
- [x] Implement `migrations.py` (schema definitions)
- [x] Implement `glossary.py` (GlossaryStore with FTS5)
- [x] Implement `revisions.py` (RevisionHistory)
- [x] Implement `state.py` (ProgressTracker)
- [x] Write tests (26 tests, all passing)

### Files Created

```
src/terrarium_annotator/storage/
  __init__.py       # Public exports
  base.py           # Database connection, migration runner, utcnow()
  exceptions.py     # StorageError hierarchy
  glossary.py       # GlossaryStore with FTS5 search
  migrations.py     # Schema migrations (3 migrations)
  revisions.py      # RevisionHistory audit log
  state.py          # ProgressTracker + ThreadState
tests/
  test_storage.py   # 26 tests covering all components
```

## Decisions Made

### Decision: FTS5 in separate migration
**Options considered**: All schema in one migration vs split by feature
**Chose**: Split - FTS5 in migration 002
**Rationale**: FTS5 virtual tables have different syntax/behavior. Easier to debug migration issues when separated.

### Decision: Shared Database across stores
**Options considered**: Each store creates own connection vs share Database instance
**Chose**: Each store creates own Database but they share the same file
**Rationale**: Simpler API (just pass db_path). In F4 we can refactor to share a single Database if needed for transaction coordination.

### Decision: RevisionHistory foreign key constraint
**Options considered**: Allow orphan revisions vs enforce FK
**Chose**: Enforce FK (entry_id must exist)
**Rationale**: Audit log should only reference real entries. Deletion logs must be created before the entry is deleted.

## Open Questions

- None

## Test Results

```
27 passed in 0.91s
- 3 Database tests
- 3 normalize_term tests
- 11 GlossaryStore tests
- 3 RevisionHistory tests
- 6 ProgressTracker tests
- 1 existing codex test (still passing)
```

## Next Steps

1. Update `docs/ROADMAP.md` to mark F0 as complete
2. Begin F1 (Corpus Layer) - CorpusReader + SceneBatcher
3. Or begin F2 (Context Layer) if corpus is already sufficient

## Blockers

None

---
*Agent: Claude Opus 4.5*
