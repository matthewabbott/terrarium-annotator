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
- T2 complete: `memory/story_log.py` (StoryLog: append/close_thread/pending/settle/cover/forget over SQLite) + `tests/test_story_log.py` (20 tests). Merge bar: 32 passed total, ruff clean.
- T2 design refinement (documented in v2-architecture.md §1): aligned power-of-two blocks retained; settlement *eligibility* gated on closed threads instead of boundary-aligned blocks (which would break alignment); `cover()` falls back to raw entries instead of OptMem-style refusal; strict in-order settlement enforced.
- omp-RPC exploration (zero-model, no token spend): transport validated (ready frame, v1→v2 negotiation, clean shutdown); command channel validated (id-correlated responses; `set_host_tools` accepted a `propose_entry` schema and returned `toolNames`). Clients must tolerate unsolicited frames: `extension_ui_request`, `advisor_cost_changed`, `available_commands_update`, `notice`. **Key discovery** via `get_state`: the subscription model is served over OpenAI-completions HTTP at `https://api.kimi.com/coding/v1` (kimi-code/k3, 1M context, zero metered cost on subscription) — so the annotator's T5 OpenAI-compatible client could potentially talk to the subscription endpoint directly, with no RPC middleman, if Matt supplies/extracts the credential (his auth store, his call — possible ToS considerations for non-coding-agent use). Unexercised, still G1-gated: an actual `prompt` + host-tool round trip (requires model spend).
- Both annotation paths (Kimi HTTP direct, omp RPC) remain **unimplemented spikes** until T5/T6 exist; neither "works today".
- Model availability probe (`get_available_models`, zero-model): the `kimi-code` provider registry lists 7 models: `k3` (1M ctx, current default), `k3-256k`, `kimi-for-coding` (K2.7), `kimi-for-coding-highspeed`, `kimi-k2`, `kimi-k2-turbo-preview`, **`kimi-k2.5` (262k ctx)**. The registry has no availability/entitlement field — whether Matt's subscription entitles K2.5 specifically is **unresolved** until an approved `set_model` + one prompt round trip (model spend, G1-gated). Note for design budgets: k2.5 is 262k ctx vs k3's 1M — injection budgets must be computed from the selected model, not hardcoded.
- K2.5 selection probe (zero-model): `set_model(provider=kimi-code, modelId=kimi-k2.5)` succeeded; `get_state` confirms the session binds `kimi-k2.5` (262k ctx). This proves the provider/session accepts the selection — **not** that inference is entitled: that still requires one approved prompt (G1).
- Next: T3 glossary store + quote gate.
