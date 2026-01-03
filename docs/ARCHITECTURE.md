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

### Context Layer (`context/` package)
- `annotation.py`: AnnotationContext - message building, history tracking
- `summarizer.py`: ThreadSummarizer - thread summary generation
- `compactor.py`: ContextCompactor - rolling compaction with 60/80/90% thresholds
- `token_counter.py`: TokenCounter - vLLM + heuristic counting
- `models.py`: ThreadSummary, SummaryResult dataclasses
- `metrics.py`: CompactionStats for observability
- `prompts.py`: Summarization prompt templates

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

```
┌─────────────────────────────────────────────────────────────────┐
│ System prompt + tool definitions           (3000-5000 tokens)   │
├─────────────────────────────────────────────────────────────────┤
│ Cumulative summary: "The Story So Far"     (200-500 tokens)     │
├─────────────────────────────────────────────────────────────────┤
│ Thread summaries (3 max, with entry IDs)   (150-450 tokens)     │
├─────────────────────────────────────────────────────────────────┤
│ Full conversation history (recent threads)  (variable)          │
├─────────────────────────────────────────────────────────────────┤
│ Current scene + relevant glossary entries  (3000-13000 tokens)  │
└─────────────────────────────────────────────────────────────────┘
```

## Compaction Thresholds

| Usage | Action |
|-------|--------|
| <60% | No compaction needed |
| 60-80% | Healthy range, no action |
| ≥80% | Loop Tier 1 (summarize threads) until <60% |
| >3 summaries | Tier 2: Merge oldest into cumulative |
| ≥90% | Emergency: All tiers (3-4) |

## Compaction Algorithm

```python
# Rolling compaction - runs at thread boundaries or when triggered
while tokens > soft_threshold (60%) and has_compactable:
    # Tier 1: Summarize oldest completed thread
    if completed_threads > 1:
        summarize_oldest_thread()  # Keep looping until <60%
        continue

    # Tier 2: Merge old summaries (runs when >3 summaries)
    if summaries > 3:
        merge_oldest_two_into_cumulative()  # Keeps 3 recent
        continue

    # Below here: emergency-only (≥90% threshold)
    if not is_emergency:
        break

    # Tier 3: Trim thinking blocks
    if has_old_thinking_tokens:
        trim_thinking(preserve_recent=4)
        continue

    # Tier 4: Truncate old responses
    if has_long_old_responses:
        truncate(max_age=8, max_len=500)
```

### Compaction Tiers

| Tier | Trigger | Action |
|------|---------|--------|
| 1 | ≥80%, >1 completed threads | Summarize oldest thread → add to thread_summaries |
| 2 | >3 thread summaries | Merge 2 oldest into cumulative (keeps 3 recent with entry IDs) |
| 3 | ≥90% emergency | Remove `<thinking>` blocks from old messages |
| 4 | ≥90% emergency | Truncate old assistant responses to 500 chars |

### Thread Summary Format

Thread summaries include entry IDs to help the agent track which glossary entries
it created or updated in each thread. This enables proper updates when details
are discovered later (e.g., renaming "weird sphere" to "archeota").

```xml
<thread_summaries>
  <thread id="5" position="0" entries="12,15,18">
    Soma introduced the party to the questmaster guild. Created entries for
    Soma (questmaster NPC) and Dawn (warrior PC).
  </thread>
  <thread id="6" position="1" entries="20,21">
    Party explored the ancient ruins. Updated Dawn's entry with combat style.
  </thread>
</thread_summaries>
```

The `entries` attribute contains comma-separated entry IDs (both created and updated).

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
  agent_client.py           # HTTP client
  types.py                  # Shared protocols/dataclasses
  exceptions.py             # Custom exceptions

  context/                  # Context management package
    __init__.py             # Exports: AnnotationContext, TokenCounter, etc.
    annotation.py           # AnnotationContext - message building
    compactor.py            # ContextCompactor - rolling compaction
    summarizer.py           # ThreadSummarizer - thread summaries
    token_counter.py        # TokenCounter - vLLM + heuristic
    models.py               # ThreadSummary, SummaryResult
    metrics.py              # CompactionStats
    prompts.py              # Summarization prompts

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
