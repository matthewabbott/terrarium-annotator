# Architecture

## Component Overview

```
CLI -> Runner -> [Context, Compactor, Curator] -> ToolDispatcher -> Storage
                         |                              |
                         v                              v
                   AgentClient <----------------> terrarium-agent (vLLM)
```

## Layers

### CLI Layer
- `cli.py`: argparse, subcommands (run, export, snapshot)
- `config.py`: dataclass with validation

### Orchestration Layer
- `runner.py`: Main loop state machine
- `curator.py`: End-of-thread fork workflow

### Context Layer
- `context.py`: Message building, history tracking
- `summarizer.py`: Thread summary generation
- `compactor.py`: 80% threshold compaction
- `tokens.py`: vLLM + heuristic counting

### Tool Layer
- `tools/dispatcher.py`: Route calls, validate I/O
- `tools/glossary_tools.py`: CRUD
- `tools/corpus_tools.py`: Post retrieval
- `tools/context_tools.py`: Snapshot rehydration

### Storage Layer
- `storage/glossary.py`: SQLite + FTS5
- `storage/snapshots.py`: Context serialization
- `storage/revisions.py`: Audit log
- `storage/state.py`: Progress tracking

### Data Access Layer
- `corpus/reader.py`: banished.db access
- `corpus/batcher.py`: Scene grouping by qm_post continuity

### External
- `agent_client.py`: HTTP to terrarium-agent:8080

## State Machine (Runner)

```
INIT -> IDLE -> [NO_MORE -> EXIT]
          |
          v
      COMPACTING -> PREPARING -> CALLING -> [RETRYING -> HALTING -> EXIT]
                                    |
                                    v
                               PROCESSING -> [TOOL_EXEC -> loop] | PARSING
                                    |
                                    v
                               RECORDING -> BOUNDARY? -> [CURATING] -> CHECKPOINT -> IDLE
```

### State Descriptions

| State | Description |
|-------|-------------|
| INIT | Load configuration, connect to databases, restore previous state if resuming |
| IDLE | Ready to process next scene, check if more work exists |
| NO_MORE | No more scenes to process, clean exit |
| COMPACTING | Check token count, trigger compaction if above 80% threshold |
| PREPARING | Build the full message context for the agent call |
| CALLING | Execute HTTP request to terrarium-agent |
| RETRYING | Handle transient failures with exponential backoff |
| HALTING | Persistent failure - checkpoint and exit with error |
| PROCESSING | Dispatch based on response type (tool calls vs text) |
| TOOL_EXEC | Execute requested tool, return result, continue dialog |
| PARSING | Parse codex_updates from text response, validate and apply |
| RECORDING | Record turns in conversation history |
| BOUNDARY? | Check if scene ends a thread |
| CURATING | Run curator fork workflow at thread boundaries |
| CHECKPOINT | Persist all state changes |

## Context Window Layout

| Section | Tokens (approx) |
|---------|-----------------|
| System prompt | 2000 |
| Cumulative summary | 1000-2000 |
| Thread n-2 summary | 500 |
| Thread n-1 full | 20000-40000 |
| Current scene | 2000-10000 |
| Relevant entries | 1000-3000 |
| Tool definitions | 1500 |

Target: <80% of limit. Compact trigger: 80%. Compact target: 70%.

## Compaction Algorithm

```python
while tokens > target and has_compactable:
    if completed_threads > 1:
        summarize_oldest_thread()
    elif summaries > 3:
        merge_oldest_into_cumulative()
    elif has_old_thinking_tokens:
        trim_thinking(preserve_recent=4)
    elif has_long_old_responses:
        truncate(max_age=8, max_len=500)
```

### Compaction Priority

1. **Summarize oldest completed thread**: Generate hybrid summary (plot + annotation progress), replace full content
2. **Merge old summaries**: Combine multiple thread summaries into cumulative summary, deduplicate
3. **Trim thinking tokens**: Remove `/think` blocks from turns older than 4, preserving recent reasoning
4. **Truncate responses**: Shorten old assistant responses to 500 chars max

## Snapshot Rehydration

1. Load snapshot from SQLite (`snapshot` + `snapshot_context` tables)
2. Reconstruct AnnotationContext with full history
3. Build message list with summoned query appended
4. Call agent, return response to current context
5. `summon_continue`: append exchange to summoned history, call again
6. `summon_dismiss`: log interaction, discard summoned context

### Summoned Context Properties

- Read-only: cannot modify main glossary
- Isolated: changes don't persist after dismiss
- Dialogic: supports multi-turn conversation
- Stateful: maintains its own conversation history during summon

## Curator Fork

1. Clone current context at thread end (deep copy)
2. Replace system prompt with curator-specific prompt
3. Query tentative entries from previous thread (thread N-1)
4. For each entry:
   - Retrieve first-appearance context (±3 posts)
   - Find similar existing entries
   - Present to curator agent
   - Parse decision: CONFIRM/REJECT/MERGE/REVISE
5. Apply decisions to main glossary (not fork)
6. Log all decisions to revision table
7. Discard fork context

## File Structure

```
src/terrarium_annotator/
  __init__.py
  cli.py                    # CLI entry point
  config.py                 # Configuration dataclass
  runner.py                 # Main AnnotationRunner
  curator.py                # CuratorFork workflow
  context.py                # AnnotationContext
  summarizer.py             # ThreadSummarizer
  compactor.py              # ContextCompactor
  tokens.py                 # TokenCounter
  agent_client.py           # HTTP client
  types.py                  # Shared protocols/dataclasses
  exceptions.py             # Custom exceptions

  tools/
    __init__.py
    dispatcher.py           # ToolDispatcher
    schemas.py              # OpenAI function schemas
    glossary_tools.py       # Glossary CRUD
    corpus_tools.py         # Post retrieval
    context_tools.py        # Snapshot rehydration

  storage/
    __init__.py
    glossary.py             # GlossaryStore (SQLite + FTS5)
    snapshots.py            # SnapshotStore
    revisions.py            # RevisionHistory
    state.py                # ProgressTracker
    migrations.py           # Schema migrations

  corpus/
    __init__.py
    reader.py               # CorpusReader
    batcher.py              # SceneBatcher
    models.py               # StoryPost, Scene

  exporters/
    __init__.py
    base.py                 # Exporter protocol
    json_exporter.py
    yaml_exporter.py

tests/
  conftest.py               # Fixtures
  fixtures/
    sample_corpus.db
    sample_glossary.db
  test_*.py

docs/
  ARCHITECTURE.md           # This file
  TOOLS.md                  # Tool interfaces
  SCHEMA.md                 # SQLite DDL

data/                       # Runtime artifacts (gitignored)
  annotator.db
  exports/
  logs/
```

## Key Protocols

```python
class TokenCounterProtocol(Protocol):
    def count(self, text: str) -> int: ...
    def count_messages(self, messages: list[dict]) -> int: ...

class GlossaryStoreProtocol(Protocol):
    def search(self, query: str, ...) -> list[GlossaryEntry]: ...
    def get(self, entry_id: int) -> Optional[GlossaryEntry]: ...
    def create(self, entry: GlossaryEntry) -> int: ...
    def update(self, entry_id: int, **fields) -> bool: ...
    def delete(self, entry_id: int, reason: str) -> bool: ...

class SnapshotStoreProtocol(Protocol):
    def save(self, snapshot: Snapshot) -> int: ...
    def load(self, snapshot_id: int) -> Snapshot: ...
    def list(self, ...) -> list[SnapshotSummary]: ...

class CorpusReaderProtocol(Protocol):
    def iter_scenes(self, start_after_post_id: Optional[int]) -> Iterator[Scene]: ...
    def get_post(self, post_id: int) -> Optional[StoryPost]: ...
```
