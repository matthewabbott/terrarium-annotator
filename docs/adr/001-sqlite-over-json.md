# ADR 001: SQLite over JSON for Storage

## Status

Accepted

## Context

The original implementation used JSON files for codex/glossary storage:
- `data/codex.json` - glossary entries
- `data/state.json` - progress tracking

This was simple for prototyping but has limitations:
- No querying beyond full-file load
- No full-text search
- No transaction safety
- No relational integrity
- File locking issues for concurrent access
- Large files = slow load times

The annotator will process a large corpus (~133K posts) and build a substantial glossary. We need efficient querying, especially for:
1. Searching existing terms before creating duplicates
2. Finding entries by tag/status
3. Tracking revision history
4. Managing snapshots for context rehydration

## Decision

Use SQLite for all persistent state:
- Single `annotator.db` file
- FTS5 virtual table for full-text search on terms/definitions
- Proper transactions for atomic updates
- Revision history table for audit trail
- Snapshot tables for context serialization

Keep JSON/YAML as export formats for human review.

## Consequences

### Positive
- Fast queries with indexes
- Full-text search on glossary via FTS5
- Atomic transactions prevent corruption
- Single file, easy backup
- Built into Python stdlib (no deps)
- Supports complex queries (by tag, status, thread)
- Relational integrity via foreign keys

### Negative
- More complex than JSON read/write
- Need migration system for schema evolution
- Binary file (not git-diffable)
- Slightly higher learning curve

### Mitigations
- Export commands produce human-readable JSON/YAML
- Revision table provides change history (git-like for humans)
- Migration system (`storage/migrations.py`) handles schema evolution
- SQLite is well-documented and widely used

## Implementation Notes

See `docs/SCHEMA.md` for full DDL.

Key tables:
- `glossary_entry` - core glossary with FTS5
- `glossary_tag` - many-to-many tags
- `revision` - change audit log
- `snapshot` / `snapshot_context` / `snapshot_entry` - context serialization
- `run_state` / `thread_state` - progress tracking
