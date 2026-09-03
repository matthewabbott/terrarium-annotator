# 2026-09-03 — T1 corpus reader (overnight run start)

**Author**: omp agent session (driver: Matt-launched)

## Protocol

Following `docs/plan/v2-foundation.md`: T1 flipped to `in progress` in the start commit; this file is the session log.

## T1 scope

Corpus reader per plan: `thread_order()` (verified COALESCE resolver), `story_posts(thread_id)` (tag-predicate filtered, time-ordered), `batches(thread_id, batch_size)` (size-based, thread-boundary breaks, no gap heuristics — T8), read-only enforcement (`mode=ro` + `uri=True`).

## Notes

(append as work proceeds)
