# SQLite Schema

## annotator.db

All annotator state persisted in single SQLite database.

---

## Glossary Tables

### glossary_entry

Core glossary entries.

```sql
CREATE TABLE glossary_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    term_normalized TEXT NOT NULL,
    definition TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'tentative'
        CHECK (status IN ('confirmed', 'tentative')),
    first_seen_post_id INTEGER NOT NULL,
    first_seen_thread_id INTEGER NOT NULL,
    last_updated_post_id INTEGER NOT NULL,
    last_updated_thread_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(term_normalized)
);

CREATE INDEX idx_glossary_term ON glossary_entry(term_normalized);
CREATE INDEX idx_glossary_status ON glossary_entry(status);
CREATE INDEX idx_glossary_updated ON glossary_entry(updated_at);
```

### glossary_tag

Free-form tags for entry classification.

```sql
CREATE TABLE glossary_tag (
    entry_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (entry_id, tag),
    FOREIGN KEY (entry_id) REFERENCES glossary_entry(id) ON DELETE CASCADE
);

CREATE INDEX idx_tag ON glossary_tag(tag);
```

### glossary_fts

Full-text search on terms and definitions.

```sql
CREATE VIRTUAL TABLE glossary_fts USING fts5(
    term,
    definition,
    content='glossary_entry',
    content_rowid='id'
);

-- Sync triggers
CREATE TRIGGER glossary_fts_insert AFTER INSERT ON glossary_entry BEGIN
    INSERT INTO glossary_fts(rowid, term, definition)
    VALUES (NEW.id, NEW.term, NEW.definition);
END;

CREATE TRIGGER glossary_fts_update AFTER UPDATE ON glossary_entry BEGIN
    INSERT INTO glossary_fts(glossary_fts, rowid, term, definition)
    VALUES ('delete', OLD.id, OLD.term, OLD.definition);
    INSERT INTO glossary_fts(rowid, term, definition)
    VALUES (NEW.id, NEW.term, NEW.definition);
END;

CREATE TRIGGER glossary_fts_delete AFTER DELETE ON glossary_entry BEGIN
    INSERT INTO glossary_fts(glossary_fts, rowid, term, definition)
    VALUES ('delete', OLD.id, OLD.term, OLD.definition);
END;
```

---

## Revision History

### revision

Git-like change log for human audit (not exposed to model).

```sql
CREATE TABLE revision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    snapshot_id INTEGER,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    source_post_id INTEGER,
    FOREIGN KEY (entry_id) REFERENCES glossary_entry(id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES snapshot(id) ON DELETE SET NULL
);

CREATE INDEX idx_revision_entry ON revision(entry_id, changed_at);
CREATE INDEX idx_revision_snapshot ON revision(snapshot_id);
```

**field_name values:**
- `term`
- `definition`
- `status`
- `tags` (JSON array)
- `curator_decision` (JSON: `{"action": "CONFIRM", "reasoning": "..."}`)

---

## Snapshot Tables

### snapshot

Snapshot metadata.

```sql
CREATE TABLE snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_type TEXT NOT NULL
        CHECK (snapshot_type IN ('checkpoint', 'curator_fork', 'manual')),
    created_at TEXT NOT NULL,
    last_post_id INTEGER NOT NULL,
    last_thread_id INTEGER NOT NULL,
    thread_position INTEGER NOT NULL,
    glossary_entry_count INTEGER NOT NULL,
    context_token_count INTEGER,
    metadata TEXT
);

CREATE INDEX idx_snapshot_created ON snapshot(created_at);
CREATE INDEX idx_snapshot_thread ON snapshot(last_thread_id);
CREATE INDEX idx_snapshot_type ON snapshot(snapshot_type);
```

### snapshot_context

Serialized context for rehydration.

```sql
CREATE TABLE snapshot_context (
    snapshot_id INTEGER PRIMARY KEY,
    system_prompt TEXT NOT NULL,
    cumulative_summary TEXT,
    thread_summaries TEXT NOT NULL,
    conversation_history TEXT NOT NULL,
    current_thread_id INTEGER,
    FOREIGN KEY (snapshot_id) REFERENCES snapshot(id) ON DELETE CASCADE
);
```

**JSON fields:**
- `thread_summaries`: `[{"thread_id": 42, "position": 42, "summary": "...", "entries_created": [1,2,3]}]`
- `conversation_history`: `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`

### snapshot_entry

Entry states at snapshot time (for blame tracking).

```sql
CREATE TABLE snapshot_entry (
    snapshot_id INTEGER NOT NULL,
    entry_id INTEGER NOT NULL,
    definition_at_snapshot TEXT NOT NULL,
    status_at_snapshot TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, entry_id),
    FOREIGN KEY (snapshot_id) REFERENCES snapshot(id) ON DELETE CASCADE,
    FOREIGN KEY (entry_id) REFERENCES glossary_entry(id) ON DELETE CASCADE
);

CREATE INDEX idx_snapshot_entry_entry ON snapshot_entry(entry_id);
```

---

## State Tables

### run_state

Singleton row tracking current run state.

```sql
CREATE TABLE run_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_post_id INTEGER,
    last_thread_id INTEGER,
    current_snapshot_id INTEGER,
    run_started_at TEXT,
    run_updated_at TEXT,
    total_posts_processed INTEGER DEFAULT 0,
    total_entries_created INTEGER DEFAULT 0,
    total_entries_updated INTEGER DEFAULT 0,
    FOREIGN KEY (current_snapshot_id) REFERENCES snapshot(id) ON DELETE SET NULL
);

-- Initialize singleton
INSERT INTO run_state (id) VALUES (1);
```

### thread_state

Per-thread tracking for sliding window.

```sql
CREATE TABLE thread_state (
    thread_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'completed')),
    summary TEXT,
    posts_processed INTEGER DEFAULT 0,
    entries_created INTEGER DEFAULT 0,
    entries_updated INTEGER DEFAULT 0,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX idx_thread_status ON thread_state(status);
```

---

## Corpus Database (banished.db)

Read-only. Pre-existing schema.

```sql
-- Threads
CREATE TABLE thread (
    id INTEGER PRIMARY KEY,
    title TEXT
);

-- Posts
CREATE TABLE post (
    thread_id INTEGER,
    id INTEGER PRIMARY KEY,
    name TEXT,
    trip_code TEXT,
    subject TEXT,
    time INTEGER,
    file_url TEXT,
    file_name TEXT,
    body TEXT,
    FOREIGN KEY (thread_id) REFERENCES thread(id)
);

-- Tags
CREATE TABLE tag (
    post_id INTEGER,
    name TEXT,
    FOREIGN KEY (post_id) REFERENCES post(id)
);
-- Tag values: 'qm_post', 'op_post', 'story_post', 'vote_tally_post', 'dropped_trip'

-- Cross-post links
CREATE TABLE link (
    link_from INTEGER,
    link_to INTEGER,
    FOREIGN KEY (link_from) REFERENCES post(id),
    FOREIGN KEY (link_to) REFERENCES post(id)
);
```

---

## Migrations

Schema versioning via simple version table:

```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

Migration files in `storage/migrations/`:
- `001_initial.sql`
- `002_add_fts.sql`
- etc.
