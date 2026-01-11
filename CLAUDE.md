# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the annotator (requires terrarium-agent on localhost:8080)
python -m terrarium_annotator.cli run --corpus-db banished.db

# Reader mode (recommended - scene-based with glossary-as-memory)
python -m terrarium_annotator.cli run --reader-mode --corpus-db banished.db --annotator-db data/annotator.db

# Batch curator (post-processing cleanup)
python -m terrarium_annotator.cli curate --annotator-db data/annotator.db --dry-run

# Testing
pytest tests -q                     # All tests
pytest -m "not integration" -q      # Skip integration tests (no agent needed)
pytest -k test_storage -q           # Target specific component

# Linting
ruff check src tests
ruff format src tests
```

## Architecture Overview

Terrarium Annotator is a harness that walks a quest fiction corpus, calls a local LLM agent server, and curates a glossary via tool-based conversation.

### Data Flow
```
banished.db (read-only corpus) → CorpusReader/SceneBatcher
                                        ↓
                               AnnotationContext (builds OpenAI messages)
                                        ↓
                               AgentClient (POST /v1/chat/completions)
                                        ↓
                               ToolDispatcher → glossary/corpus/snapshot tools
                                        ↓
                               annotator.db (read-write: glossary, revisions, snapshots)
```

### Key Modules (src/terrarium_annotator/)

| Module | Purpose |
|--------|---------|
| `runner.py` | Main AnnotationRunner - orchestrates annotation loop with multiple modes |
| `curator.py` | CuratorFork (per-thread) + BatchCurator (post-processing cleanup) |
| `agent_client.py` | HTTP client to terrarium-agent:8080 (OpenAI-compatible) |
| `context/` | Context window management: message building, token counting, compaction |
| `tools/` | ToolDispatcher + tools (glossary CRUD, corpus reading, snapshots, upsert/lookup) |
| `storage/` | SQLite layer: GlossaryStore (FTS5), RevisionHistory, ProgressTracker, Snapshots |
| `corpus/` | CorpusReader, SceneBatcher - reads posts grouped by qm_post tags |

### Processing Modes

1. **Reader Mode** (recommended): Scene-based with glossary-as-memory
   - Near-stateless: context resets between scenes, glossary IS the memory
   - Uses `glossary_upsert` (create-or-update) and `glossary_lookup` tools
   - Very low context usage (~1-3%)

2. **Thread Mode**: Process entire threads at once
   - Higher context usage, can overwhelm agent on long threads

3. **Scene Mode** (legacy): Original scene-by-scene with history accumulation

### Context Management

The system uses ~112K token budget with rolling compaction:
- **Tier 0.5**: Summarize oldest chunk when ≥80% full
- **Tier 1**: Merge oldest thread into cumulative summary when ≥80%
- **Tier 3/4**: Emergency truncation at ≥85% (remove thinking blocks, truncate responses)

### Database Files

- `banished.db` - Read-only corpus (posts, threads, tags). Never modify.
- `data/annotator.db` - Read-write (glossary entries, revisions, snapshots, run state)

## Current State & Goals

### What We're Building
A glossary/codex for the "Banished Quest" story - a quest fiction corpus. The glossary captures:
- Fantasy terminology (Vys, Vatis, Rhynian, etc.)
- Named entities (characters, places, factions)
- English words with special in-universe meanings

### Current Progress
- **Reader mode v2** is running through the corpus (~63/278 threads complete)
- **Batch curator** ready for post-processing cleanup when run completes
- Quality improved significantly from v1 (45% fewer entries, 86% less junk)

### Known Issues Being Addressed
- Over-fragmentation: "Vys" spawns variants like "Vys energy", "Vys pool"
- Some junk still leaks through: dice rolls, platform terms
- Action phrases created as entries: "spend Vys", "Manipulate Vys"

### Next Steps
1. Wait for reader mode v2 to complete
2. Run batch curator to clean up glossary (merge fragments, delete junk)
3. Evaluate final glossary quality

## Agent Workflow Guidelines

From AGENTS.md - when working across sessions:

1. Check `docs/ROADMAP.md` for current feature in progress
2. Read recent `docs/worklog/` entries for context
3. Consult `docs/INTERFACES.md` for method signatures before implementing
4. Mirror source structure in `tests/` - test as you go
5. Run `pytest` before finishing

### Documentation Hierarchy

1. `SPEC.md` - What we're building
2. `docs/ARCHITECTURE.md` - Component interactions
3. `docs/INTERFACES.md` - Method signatures and contracts
4. `docs/SCHEMA.md` - Database structure
5. `docs/ROADMAP.md` - Feature status
6. `docs/worklog/` - Session logs (start here for recent context)
7. `docs/adr/` - Architecture decisions

### Key Files for Current Work

- `src/terrarium_annotator/context/prompts.py` - System prompts including `READER_MODE_PROMPT`
- `src/terrarium_annotator/curator.py` - `BatchCurator` for post-processing
- `src/terrarium_annotator/runner.py` - `_run_reader_mode()` for current processing approach
- `docs/worklog/2026-01-06-reader-mode.md` - Current session log with results and decisions
