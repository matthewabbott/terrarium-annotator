# Terrarium Annotator

An always-on harness that walks the Banished Quest corpus, calls the local `terrarium-agent` server, and curates a glossary of people, places, items, and mechanics. The agent maintains a rolling conversation history with tool-based glossary updates so later passages inherit consistent terminology.

## Architecture

```
banished.db (read-only)     annotator.db (read-write)
      │                            │
      ▼                            ▼
┌─────────────┐            ┌─────────────────┐
│CorpusReader │            │  GlossaryStore  │ ◄── FTS5 search
│ SceneBatcher│            │ RevisionHistory │ ◄── audit trail
└─────────────┘            │ ProgressTracker │ ◄── run state
      │                    └─────────────────┘
      ▼                            │
┌─────────────────────────────────────────────┐
│              ToolDispatcher                 │
│  glossary_search | glossary_create          │
│  glossary_update | glossary_delete          │
│  read_post      | read_thread_range         │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│           AnnotationContext                 │
│  system_prompt + conversation_history       │
│  → OpenAI message format                    │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│              AgentClient                    │
│  POST /v1/chat/completions                  │
└─────────────────────────────────────────────┘
```

## How it Works

1. **CorpusReader** streams posts from `banished.db` (read-only corpus).
2. **SceneBatcher** groups contiguous `qm_post`-tagged posts into scenes.
3. **AnnotationContext** builds OpenAI-format messages with system prompt, summaries, and conversation history.
4. **AgentClient** calls `http://localhost:8080/v1/chat/completions`.
5. **ToolDispatcher** routes tool calls to glossary/corpus operations.
6. **GlossaryStore** persists entries to `annotator.db` with FTS5 search.
7. **RevisionHistory** logs all glossary changes for audit trail.
8. **ProgressTracker** saves run state for automatic resumption.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Ensure terrarium-agent server is running on localhost:8080
python -m terrarium_annotator.cli run --db banished.db
```

Useful flags:
- `--limit 50` - annotate only the next 50 posts
- `--no-resume` - restart from the first post
- `--agent-url http://localhost:8081` - use a different agent endpoint

## Storage

| Database | Purpose |
|----------|---------|
| `banished.db` | Corpus (read-only) - forum posts, threads, tags |
| `annotator.db` | Glossary, revisions, snapshots, run state (read-write) |

The annotator database uses SQLite with:
- **FTS5** full-text search for glossary terms
- **Migrations** for schema versioning
- **Foreign keys** for referential integrity

## Available Tools

The agent can call these tools via OpenAI function calling:

| Tool | Description |
|------|-------------|
| `glossary_search` | Search glossary by query, tags, status |
| `glossary_create` | Create new glossary entry |
| `glossary_update` | Update existing entry |
| `glossary_delete` | Delete entry with reason |
| `read_post` | Read a single post by ID |
| `read_thread_range` | Read posts in a thread range |

## Current Status

See `docs/ROADMAP.md` for feature status.

- [x] F0: SQLite storage layer with FTS5
- [x] F1: Scene-aware corpus batching
- [x] F2: Token counting and context building (partial)
- [x] F3: Tool dispatcher and implementations
- [x] F3.5: Documentation and basic logging
- [ ] F4: Runner MVP with new storage integration
- [ ] F5: Context compaction and summarization
- [ ] F6: Curator workflow
- [ ] F7: Snapshots
- [ ] F8: Exporters

## Documentation

- `docs/SPEC.md` - System specification
- `docs/INTERFACES.md` - Class interfaces and contracts
- `docs/SCHEMA.md` - Database schema
- `docs/ARCHITECTURE.md` - System architecture
- `docs/ROADMAP.md` - Feature roadmap and status
