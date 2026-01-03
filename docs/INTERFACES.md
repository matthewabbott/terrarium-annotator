# Component Interfaces

Explicit I/O contracts for all major components.

## Storage Layer

### GlossaryStore

```python
class GlossaryStore:
    """SQLite-backed glossary with FTS5 search."""

    def __init__(self, db_path: Path) -> None:
        """
        Connect to or create annotator.db.
        Runs migrations if needed.
        """

    def search(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        status: Literal["confirmed", "tentative", "all"] = "all",
        limit: int = 10
    ) -> list[GlossaryEntry]:
        """
        Full-text search on terms and definitions.

        Returns: Matching entries, ordered by relevance.
        Raises: DatabaseError on connection issues.
        """

    def get(self, entry_id: int) -> GlossaryEntry | None:
        """Fetch single entry by ID. Returns None if not found."""

    def create(
        self,
        term: str,
        definition: str,
        tags: list[str],
        *,
        post_id: int,
        thread_id: int,
        status: str = "tentative"
    ) -> int:
        """
        Insert new entry.

        Returns: New entry ID.
        Raises: DuplicateTermError if term_normalized exists.
        """

    def update(
        self,
        entry_id: int,
        *,
        term: str | None = None,
        definition: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        post_id: int,
        thread_id: int
    ) -> bool:
        """
        Update entry fields. Only provided fields modified.
        Logs to revision table.

        Returns: True if updated, False if entry not found.
        """

    def delete(self, entry_id: int, reason: str) -> bool:
        """
        Delete entry. Logs reason to revision table.

        Returns: True if deleted, False if not found.
        """

    def all_entries(self) -> Iterator[GlossaryEntry]:
        """Yield all entries. Use for export operations."""
```

### SnapshotStore

```python
class SnapshotStore:
    """Snapshot persistence for context rehydration."""

    def __init__(self, db_path: Path) -> None:
        """Connect to annotator.db."""

    def save(
        self,
        snapshot_type: Literal["checkpoint", "curator_fork", "manual"],
        *,
        context: AnnotationContext,
        glossary_state: dict[int, EntryState],
        post_id: int,
        thread_id: int,
        thread_position: int
    ) -> int:
        """
        Serialize and store snapshot.

        Returns: New snapshot ID.
        """

    def load(self, snapshot_id: int) -> Snapshot:
        """
        Deserialize snapshot.

        Raises: SnapshotNotFoundError if ID doesn't exist.
        """

    def list(
        self,
        *,
        thread_id: int | None = None,
        snapshot_type: str | None = None,
        limit: int = 20
    ) -> list[SnapshotSummary]:
        """Query snapshots. Returns metadata only, not full context."""
```

### ProgressTracker

```python
class ProgressTracker:
    """Run state persistence."""

    def __init__(self, db_path: Path) -> None: ...

    def get_state(self) -> RunState:
        """Load current run state from singleton row."""

    def update(
        self,
        *,
        last_post_id: int | None = None,
        last_thread_id: int | None = None,
        current_snapshot_id: int | None = None,
        posts_processed_delta: int = 0,
        entries_created_delta: int = 0,
        entries_updated_delta: int = 0
    ) -> None:
        """Update run state. Deltas are added to current totals."""
```

### RevisionHistory

```python
class RevisionHistory:
    """Audit log for glossary changes."""

    def __init__(self, db_path: Path) -> None: ...

    def log_change(
        self,
        entry_id: int,
        field_name: str,
        old_value: str | None,
        new_value: str,
        *,
        source_post_id: int | None = None,
        snapshot_id: int | None = None
    ) -> int:
        """
        Record a change to a glossary entry.

        Returns: Revision ID.
        """

    def get_history(
        self,
        entry_id: int,
        *,
        limit: int = 50
    ) -> list[Revision]:
        """Get change history for an entry, newest first."""
```

## Data Access Layer

### CorpusReader

```python
class CorpusReader:
    """Read-only access to banished.db."""

    def __init__(self, db_path: Path) -> None:
        """Connect to corpus database."""

    def get_post(self, post_id: int) -> StoryPost | None:
        """Fetch single post by ID."""

    def get_posts_range(
        self,
        thread_id: int,
        *,
        start_post_id: int | None = None,
        end_post_id: int | None = None,
        tag_filter: str | None = None
    ) -> list[StoryPost]:
        """Fetch posts in range, optionally filtered by tag."""

    def get_adjacent_posts(
        self,
        post_id: int,
        before: int = 2,
        after: int = 2
    ) -> list[StoryPost]:
        """Fetch posts surrounding a given post."""

    def iter_threads(self) -> Iterator[Thread]:
        """Yield all threads in chronological order."""
```

### SceneBatcher

```python
class SceneBatcher:
    """Group posts into scenes (contiguous qm_post runs)."""

    def __init__(self, corpus: CorpusReader) -> None: ...

    def iter_scenes(
        self,
        start_after_post_id: int | None = None
    ) -> Iterator[Scene]:
        """
        Yield scenes starting after given post.

        A scene is a contiguous run of qm_post-tagged posts
        within a single thread. Scene ends when:
        - Next post lacks qm_post tag
        - Thread boundary reached
        """
```

## Context Layer

### TokenCounter

```python
class TokenCounter:
    """Token counting with vLLM primary, heuristic fallback."""

    def __init__(
        self,
        agent_client: AgentClient,
        chars_per_token: float = 4.0
    ) -> None: ...

    def count(self, text: str) -> int:
        """Count tokens. Falls back to heuristic on vLLM failure."""

    def count_messages(self, messages: list[dict]) -> int:
        """Count tokens for message list including role overhead."""

    @property
    def using_fallback(self) -> bool:
        """True if vLLM tokenize failed and we're using heuristic."""
```

### AnnotationContext

```python
class AnnotationContext:
    """Conversation state and message building."""

    def __init__(
        self,
        system_prompt: str,
        max_turns: int = 12
    ) -> None: ...

    def build_messages(
        self,
        *,
        cumulative_summary: str | None,
        thread_summaries: list[ThreadSummary] | None,
        chunk_summaries: list[ChunkSummary] | None,
        current_scene: Scene,
        relevant_entries: list[GlossaryEntry],
        tools: list[dict]
    ) -> list[dict]:
        """Build full message list for agent call."""

    def record_turn(
        self,
        role: Literal["user", "assistant", "tool"],
        content: str,
        *,
        tool_call_id: str | None = None,
        thread_id: int | None = None,
        scene_index: int | None = None
    ) -> None:
        """Add turn to conversation history with compaction tags."""

    def remove_thread_turns(self, thread_id: int) -> int:
        """Remove all turns for a thread. Returns count removed."""

    def remove_chunk_turns(
        self,
        thread_id: int,
        first_scene_index: int,
        last_scene_index: int
    ) -> int:
        """Remove turns within a scene chunk. Returns count removed."""

    def get_history(self) -> list[dict]:
        """Get conversation history (for serialization)."""

    def clone(self) -> AnnotationContext:
        """Deep copy for forking (curator, summon)."""
```

### ContextCompactor

```python
class ContextCompactor:
    """Rolling context compaction with smart thresholds.

    Thresholds:
    - <60%: No compaction needed
    - ≥80%: Loop Tier 1 (summarize threads) until <60%
    - >3 summaries: Tier 2 merges oldest into cumulative
    - ≥90%: Emergency Tiers 3-4 (trim thinking, truncate)
    """

    def __init__(
        self,
        token_counter: TokenCounter,
        summarizer: ThreadSummarizer,
        agent_client: AgentClient | None = None,
        context_budget: int = 16000,
        soft_ratio: float = 0.60,
        thread_compact_ratio: float = 0.80,
        emergency_ratio: float = 0.90,
        target_ratio: float = 0.70
    ) -> None: ...

    def should_compact(self, messages: list[dict]) -> bool:
        """Check if over 80% (rolling compaction threshold)."""

    def should_emergency_compact(self, messages: list[dict]) -> bool:
        """Check if emergency compaction should trigger (≥90%)."""

    def compact(
        self,
        messages: list[dict],
        state: CompactionState,
        context: AnnotationContext | None = None
    ) -> tuple[list[dict], CompactionResult]:
        """
        Apply rolling compaction with smart thresholds.

        Modifies BOTH messages AND context.conversation_history
        so changes persist across scenes.

        Returns: (compacted_messages, CompactionResult)
        """

    def get_current_usage(self, messages: list[dict]) -> tuple[int, float]:
        """Get current token count and usage percentage."""
```

### ThreadSummarizer

```python
class ThreadSummarizer:
    """Generate hybrid summaries for completed threads."""

    def __init__(self, agent_client: AgentClient) -> None: ...

    def summarize(
        self,
        thread_context: list[dict],
        entries_created: list[int],
        entries_updated: list[int]
    ) -> str:
        """
        Generate hybrid summary: plot highlights + annotation progress.

        Returns: Summary text (target: ~500 tokens).
        """

    def merge_summaries(
        self,
        cumulative: str | None,
        new_summaries: list[str]
    ) -> str:
        """Merge multiple summaries into cumulative summary."""
```

## Tool Layer

### ToolDispatcher

```python
class ToolDispatcher:
    """Route tool calls to implementations."""

    def __init__(
        self,
        glossary: GlossaryStore,
        corpus: CorpusReader,
        snapshots: SnapshotStore,
        agent: AgentClient
    ) -> None: ...

    def dispatch(
        self,
        tool_call: dict,
        *,
        current_post_id: int,
        current_thread_id: int
    ) -> ToolResult:
        """
        Execute tool call.

        Returns: ToolResult with success status and XML response.
        """

    def get_tool_definitions(self) -> list[dict]:
        """Return OpenAI function calling schemas."""

    @property
    def has_active_summon(self) -> bool:
        """True if a summoned context is active."""
```

## Orchestration Layer

### AnnotationRunner

```python
class AnnotationRunner:
    """Main orchestration loop."""

    def __init__(self, config: RunnerConfig) -> None: ...

    def run(self, limit: int | None = None) -> RunResult:
        """
        Execute annotation loop.

        Args:
            limit: Max scenes to process (None = all)

        Returns: RunResult with stats and final state.
        """

    def checkpoint(self) -> int:
        """
        Force checkpoint now.

        Returns: Snapshot ID.
        """
```

### CuratorFork

```python
class CuratorFork:
    """End-of-thread evaluation of tentative entries."""

    def __init__(
        self,
        context: AnnotationContext,
        glossary: GlossaryStore,
        agent: AgentClient,
        thread_id: int
    ) -> None: ...

    def run(self) -> list[CuratorDecision]:
        """
        Evaluate tentative entries from previous thread.

        Returns: List of decisions made (CONFIRM/REJECT/MERGE/REVISE).
        """
```

## External

### AgentClient

```python
class AgentClient:
    """HTTP client for terrarium-agent."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        timeout: float = 60.0,
        max_retries: int = 3
    ) -> None: ...

    def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 768
    ) -> AgentResponse:
        """
        Send chat completion request.

        Raises: AgentClientError on persistent failure.
        """

    def tokenize(self, text: str) -> list[int]:
        """
        Get token IDs for text.

        Raises: AgentClientError if endpoint unavailable.
        """

    def health_check(self) -> bool:
        """Check if agent is reachable."""
```

## Data Classes

```python
@dataclass
class GlossaryEntry:
    id: int | None
    term: str
    term_normalized: str
    definition: str
    status: str
    tags: list[str]
    first_seen_post_id: int
    first_seen_thread_id: int
    last_updated_post_id: int
    last_updated_thread_id: int
    created_at: str
    updated_at: str

@dataclass
class StoryPost:
    post_id: int
    thread_id: int
    body: str
    author: str | None
    created_at: datetime | None
    tags: list[str]

@dataclass
class Scene:
    thread_id: int
    posts: list[StoryPost]
    is_thread_start: bool
    is_thread_end: bool
    scene_index: int

@dataclass
class ThreadSummary:
    thread_id: int
    position: int
    summary_text: str
    entries_created: list[int]
    entries_updated: list[int]

@dataclass
class ToolResult:
    tool_name: str
    call_id: str
    success: bool
    result: str  # XML response
    error: str | None

@dataclass
class RunState:
    last_post_id: int | None
    last_thread_id: int | None
    current_snapshot_id: int | None
    run_started_at: str | None
    run_updated_at: str | None
    total_posts_processed: int
    total_entries_created: int
    total_entries_updated: int

@dataclass
class CuratorDecision:
    entry_id: int
    entry_term: str
    action: Literal["CONFIRM", "REJECT", "MERGE", "REVISE"]
    target_id: int | None  # For MERGE
    revised_definition: str | None  # For REVISE
    reasoning: str
```

## Known Issues

### Revision CASCADE Deletion (Defer to F7)

**Issue:** The `revision` table has `ON DELETE CASCADE` on the `entry_id` foreign key
(see `storage/migrations.py`). When a glossary entry is deleted, all its revision
history is also deleted.

**Impact:** Deletion audit trail is lost. The deletion IS logged by `GlossaryTools.delete()`
but immediately cascade-deleted when `GlossaryStore.delete()` removes the entry.

**Workaround:** None currently. Deletion events cannot be audited after the fact.

**Resolution:** Planned for F7 (Snapshot System). Options under consideration:
- Change to `ON DELETE SET NULL` (preserves revisions with null entry_id)
- Remove FK constraint entirely (revisions become independent audit log)
- Add separate `deletion_log` table
