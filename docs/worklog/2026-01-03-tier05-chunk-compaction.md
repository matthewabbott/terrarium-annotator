# 2026-01-03 - Tier 0.5 Intra-Thread Chunk Compaction

## Objective

Fix the compaction doom loop and implement intra-thread scene chunking to handle
very long threads that exceed context budget before thread boundaries.

## Context

State of the codebase when I started:
- Current feature in progress: F5 Compaction refinements
- Last completed work: Rolling compaction with 60/80/90% thresholds, entry ID tracking
- Known issues:
  - "Doom loop" where compaction at max context couldn't free itself
  - Tier 4 repeatedly truncating same messages (71 responses, 0 token reduction)
  - No compaction possible when stuck in long first thread (no completed threads)

## Work Done

### Bug Fixes

- [x] Fixed doom loop in Tier 4: Added check for already-truncated messages (`... [truncated]` suffix)
- [x] Added no-progress detection: Break loop when `current_tokens >= prev_tokens`

### New Feature: Tier 0.5 Chunk Compaction

- [x] Added `ChunkSummary` dataclass to `context/models.py`
- [x] Added `scene_index` parameter to `record_turn()` in `annotation.py`
- [x] Added `remove_chunk_turns()` method to `AnnotationContext`
- [x] Added `_format_chunk_summaries()` helper for XML formatting
- [x] Added `chunk_summaries` parameter to `build_messages()`
- [x] Added `CHUNK_SUMMARY_PROMPT` to `context/prompts.py`
- [x] Added `summarize_chunk()` method to `ThreadSummarizer`
- [x] Extended `CompactionState` with chunk tracking fields:
  - `chunk_summaries`, `current_thread_id`, `current_scene_index`
  - `summarized_chunk_indices`, helper methods (`start_new_thread`, `advance_scene`, etc.)
- [x] Added `chunks_summarized` to `CompactionResult`
- [x] Implemented Tier 0.5 logic in `ContextCompactor.compact()`
- [x] Updated `runner.py` to track `scene_index` and call chunk state methods
- [x] Added 10 new tests for chunk compaction

### Simplified Design

- [x] Thread summaries now merge immediately into `cumulative_summary` at thread boundaries
- [x] Removed need for separate `thread_summaries` list (legacy support retained)
- [x] Simpler context structure: cumulative_summary + chunk_summaries + recent conversation

## Decisions Made

### Decision: Intra-thread scene chunking vs alternatives
**Options considered**:
1. Just raise context limit (kicks the can)
2. Smarter thread splitting (complex, arbitrary boundaries)
3. Intra-thread scene chunking (natural batches)
**Chose**: Option 3 - Intra-thread scene chunking
**Rationale**: Scenes are already natural processing units. Grouping 10 scenes
into "chunks" and summarizing old chunks preserves context while freeing tokens.
Preserving last 2 chunks (20 scenes) maintains enough recent context for coherent
annotation.

### Decision: Immediate thread summary merging
**Options considered**:
1. Keep 3 thread summaries, merge oldest 2 when >3 (original design)
2. Merge immediately into cumulative at thread boundary
**Chose**: Option 2 - Immediate merging
**Rationale**: User feedback: "if we're getting to the point where we need
within-thread summaries, we should first have at most 1 previous thread summary.
Maybe even none. Just 'story so far', chunk summaries, full convo."

## Configuration Parameters

New parameters added to `ContextCompactor`:
- `scenes_per_chunk: int = 10` - Scenes per chunk for Tier 0.5
- `preserve_recent_chunks: int = 2` - Keep last N chunks intact

## Open Questions

- Should we save a large-context integration test snapshot?
- Are 10 scenes per chunk optimal? May need tuning based on actual usage.
- Should chunk summaries include more/less entry ID tracking?

## Next Steps

1. Monitor live run for compaction behavior
2. If chunk compaction works well, consider saving test snapshot
3. Tune `scenes_per_chunk` based on observed behavior

## Test Results

All 262 tests pass:
- 29 compactor tests (including 10 new chunk tests)
- 18 runner tests (updated for scene_index param)
- Full suite passes

## Files Changed

- `src/terrarium_annotator/context/models.py` - Added ChunkSummary
- `src/terrarium_annotator/context/annotation.py` - scene_index, chunk methods
- `src/terrarium_annotator/context/prompts.py` - CHUNK_SUMMARY_PROMPT
- `src/terrarium_annotator/context/summarizer.py` - summarize_chunk()
- `src/terrarium_annotator/context/compactor.py` - Tier 0.5, simplified Tier 1
- `src/terrarium_annotator/context/__init__.py` - exports
- `src/terrarium_annotator/runner.py` - scene tracking, chunk state
- `tests/test_compactor.py` - 10 new tests
- `tests/test_runner.py` - scene_index param updates
- `docs/ARCHITECTURE.md` - Updated compaction docs

---

## F5.2 Adaptive Compaction (Session 2)

### Problem

Compaction was getting stuck in a "doom loop" at 80-90% usage where:
- Not enough completed chunks to trigger Tier 0.5 (needed 3 with preserve=2)
- No completed threads to trigger Tier 1
- Emergency (90%) not reached yet

### Solution

1. **Smaller chunks**: `scenes_per_chunk: 10 → 7`
   - More frequent chunk boundaries = more compaction opportunities

2. **Lower emergency threshold**: `emergency_ratio: 0.90 → 0.85`
   - Earlier intervention before hitting hard limits

3. **Adaptive preserve_recent reduction**: Try preserve values 2 → 1 → 0
   - Even with just 1 completed chunk, can now compact at preserve=0

4. **Tier 0.5b Partial chunk fallback**: If no full chunks but 6+ scenes:
   - Summarize first half of scenes as a "partial chunk"
   - Uses negative chunk indices to distinguish from regular chunks

### Implementation

```python
# Tier 0.5: Adaptive chunk compaction
for preserve_n in [2, 1, 0]:
    if can_summarize_chunk(preserve_n):
        summarize_oldest_chunk()
        break
else:
    # Tier 0.5b: Partial chunk fallback
    if current_scene_index >= 6 and no_chunks_summarized:
        summarize_first_half_of_scenes()
```

### Files Changed

- `src/terrarium_annotator/context/compactor.py` - Adaptive loop, partial chunk method
- `docs/ARCHITECTURE.md` - Updated algorithm and tier table
- `docs/ROADMAP.md` - Added F5.2 enhancement entry
- `tests/test_compactor.py` - 6 new tests, 1 updated test

### Test Results

All 262 tests pass (35 compactor tests including 6 new adaptive tests)

---
*Agent: Claude Opus 4.5*
*Duration: ~2.5 hours total (F5.1 + F5.2)*
