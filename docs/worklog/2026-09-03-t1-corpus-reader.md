# 2026-09-03 — T1 corpus reader (overnight run start)

**Author**: omp agent session (driver: Matt-launched)

## Protocol

Following `docs/plan/v2-foundation.md`: T1 flipped to `in progress` in the start commit; this file is the session log.

## T1 scope

- Corpus reader per plan: `thread_order()` (verified COALESCE resolver), `story_posts(thread_id)` (tag-predicate filtered, time-ordered), `batches(thread_id, batch_size)` (size-based, thread-boundary breaks, no gap heuristics — T8), read-only enforcement (`mode=ro` + `uri=True`).

## Notes

- T1 complete: `corpus/` (models, reader, `__init__`) + `tests/test_corpus.py` (12 tests). Merge bar: 12 passed, ruff check + format clean.
- Verified against real corpus (read-only): resolver's first three threads are 30265887/30305969/30392208 as documented.
- Decisions: `Self` return avoided (project floor 3.10; PYI034 noqa'd instead of bumping requires-python — kept T1 free of unrelated metadata changes). Batch text joins bodies with blank lines; raw markup untouched.
- Next: T2 story log + merge tree.
- omp-RPC adapter spike: omp 18.1.2 installed locally, but the `omp-rpc` Python package is not on PyPI and not on disk — adapter would be a thin hand-rolled JSONL client per `omp://rpc.md` (v1 framing is trivial), or fetch the bundled client from the omp source repo. Still G1-gated.
