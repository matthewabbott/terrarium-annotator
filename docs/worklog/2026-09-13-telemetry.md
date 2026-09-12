# 2026-09-13 — Usage telemetry (gated task)

Session goal: implement usage telemetry per `docs/plan/aspirations.md`
§"NEXT: usage telemetry". Gated: all acceptance criteria met, or stop and
report the blocker. Nothing else in scope (adjudication, laddering,
Deepseek are later phases).

This file + the commits are the durable record.


## Investigation (before any wrapper code)

### Q1: does the omp RPC path expose provider `usage`?

**Verdict: UNKNOWN at protocol level; currently uncaptured.**

Evidence:
- `omp://rpc.md` (harness RPC protocol doc, `agent_end` section): "`agent_end`
  has this session-level shape (**in addition to optional telemetry fields**)"
  — telemetry fields are acknowledged to exist but are NOT enumerated anywhere
  in `rpc.md` / `sdk.md` (greps for `usage`, `prompt_tokens`, `inputTokens`,
  `cost` find no field definitions). `get_state` exposes `contextUsage.tokens`
  (session-level context fill), not per-call prompt/completion usage.
- `OmpRpcClient._exchange` (src/terrarium_annotator/llm/omp_rpc.py:248-252)
  reads only `frame["messages"]` from the terminal `agent_end` frame and
  discards everything else — so even if omp emits telemetry, we drop it.
- The installed `omp` is a compiled ELF binary (no source to inspect), and a
  live probe is out of scope (goal stop condition: no LLM quota spend).

Resolution (no invention): capture the terminal `agent_end` frame's extra
fields into `ChatResponse.raw["agent_end"]` so whatever telemetry arrives is
recorded verbatim; the telemetry layer extracts `usage` only if a `usage`
dict is actually present. On this path provider usage stays null until omp
is observed emitting it.

### Q2: what does `ChatResponse.raw` carry on the OpenAI-compatible path?

`parse_choice` (src/terrarium_annotator/llm/base.py:69-71) sets
`ChatResponse.raw = raw` — the full response body. OpenAI-style endpoints
report `usage: {prompt_tokens, completion_tokens, total_tokens}` in that
body; when present it is ground truth and available via `raw.get("usage")`.
When absent: null, and char counts (chars/4 token estimates, labeled as
estimates) are the fallback.

### Q3: where do retries live today?

- `OmpRpcClient.chat` (omp_rpc.py:160-194): internal per-call loop,
  `attempts` (default 2), fresh process per attempt; retries ONLY on
  `EmptyResponseError` / `RPCTimeoutError`; other `ChatClientError`s
  propagate immediately.
- `OpenAICompatibleClient.chat` (openai_client.py:64-83): internal loop,
  `max_retries` (default 3), backoff 0.5·2^n; retries on
  `requests.RequestException` and HTTP 5xx; 4xx and non-JSON bodies fail
  fast.
- `ScriptedModel` / `RecordingClient` / `ReplayClient`: no retries.

Consequence: an outer wrapper sees only the FINAL outcome of each `chat()`;
internal retry attempts are invisible without a seam. Both production
clients need an attempt observer.


## Design decisions

- **Attempt-observation seam**: `AttemptEvent` + `AttemptObserver` live in
  `llm/base.py` (clients already import base; no cycles). Both internally-
  retrying clients take `attempt_observer=None` (default: zero behavior
  change) and emit one event per attempt, numbered 1..N with status and
  error type. The attribute is read at call time so `InstrumentedClient`
  can (re)wire it per call, composing with any pre-existing observer and
  restoring it afterwards. Retry responsibility did NOT move.
- **InstrumentedClient** (`llm/telemetry.py`): one JSONL record per chat()
  with nested `attempts[]`. If the inner client emitted attempt events,
  those are the record's attempts; otherwise the wrapper synthesizes one
  attempt from the outer outcome (ScriptedModel/RecordingClient path), so
  the schema is uniform. Errors are recorded in `finally` — a raised call
  still writes its record before propagating.
- **Provider usage**: extracted only from `raw["usage"]` (OpenAI path) or
  `raw["agent_end"]["usage"]` (RPC path) when a dict is actually present;
  null otherwise, never invented. To make the RPC path *capable* of
  reporting, `OmpRpcClient._exchange` now preserves the terminal
  `agent_end` frame's extra fields into `ChatResponse.raw["agent_end"]`
  (previously discarded at omp_rpc.py:248-252). Field names remain
  undocumented upstream — Q1 verdict stands.
- **Context components**: `set_context({system, cards, digest, scene})` /
  `set_call_type(label)` setters; unknown keys rejected to keep the schema
  stable; unreported components are the literal string `"unknown"`.
  `Runner` gained an optional `telemetry` handle (default None) and reports
  exact char sizes at assembly — annotation calls label "annotation",
  merge-settle calls label "merge-settle" with cards/digest/scene = 0
  (genuinely absent, not unknown). Researcher/chat sessions label
  "researcher"/"chat" with all components unknown.
- **Observe success only after a usable response exists** (advisory fix):
  in `OpenAICompatibleClient`, a 200 whose body fails `parse_choice`
  (malformed envelope/tool call) records an ERROR attempt, not a false
  success; regression test `test_openai_malformed_200_is_error_not_success`.
- **Durable output**: `data/recordings/usage/<run_id>.jsonl` (gitignored
  data/), `run_id = <pass_id>-<UTC start>`; filename sanitized, record
  field verbatim. Always-on for the `run`, `research`, and `chat` CLI
  commands; `--usage-dir` overrides the location.
- **Wiring order**: telemetry innermost, `RecordingClient` outside it, so
  `--record` sessions keep internal retries observable.
- **Aggregates**: `summarize(path)` (file or dir) → per-run and
  per-call-type buckets: calls, errors (+types), chars in/out, tool calls,
  attempts-by-status, est tokens (chars/4, labeled est_), count of records
  carrying provider usage. CLI: `terrarium-annotator usage-summary [path]`.

## What shipped

- `llm/base.py`: `AttemptEvent`, `AttemptObserver`.
- `llm/omp_rpc.py`: `attempt_observer` param; per-attempt observation incl.
  non-retried ChatClientErrors; `agent_end` extras captured into raw.
- `llm/openai_client.py`: `attempt_observer` param; retry loop restructured
  (behavior-preserving: same retry/backoff/fail-fast semantics) with
  per-attempt observation.
- `llm/telemetry.py` (new): `InstrumentedClient`, `UsageLog`,
  `summarize()`/`format_summary()`, `make_run_id()`/`usage_log_path()`.
- `runner.py`: optional `telemetry` handle; exact context-component sizes
  reported at assembly; call-type labels at annotation/merge-settle
  boundaries. User-message assembly refactored to build component strings
  first — byte-identical output.
- `cli.py`: telemetry wired into run/research/chat (always on,
  `--usage-dir`); new `usage-summary` subcommand.
- `tests/fake_omp.py`: optional `FAKE_OMP_USAGE` env adds a `usage` field
  to the agent_end frame.
- `tests/test_telemetry.py` (new): 11 L0 tests — record contents, null vs
  reported provider usage, error-then-success, context reporting,
  internal-retry proofs for BOTH clients (fake_omp empty-response retry;
  stub-server 5xx retry), RPC usage plumbing end-to-end, summarize
  aggregates, run-id sanitization.

## Evidence (merge bar)

- `.venv/bin/python -m pytest tests -q` → **187 passed** (was 175; +12 new)
  in 85s, after `ruff format` (semantics-preserving reformat only).
- `.venv/bin/ruff check src tests` → All checks passed.
- `.venv/bin/ruff format --check src tests` → 41 files already formatted.
- Sample record (scripted model, no live calls):
  `data/recordings/usage/telemetry-sample-20260912T102117.jsonl` —
  ```json
  {"run_id": "telemetry-sample-20260912T102117", "call_type": "annotation",
   "seq": 1, "status": "success", "error_type": null,
   "attempts": [{"attempt": 1, "status": "success", "error_type": null}],
   "prompt_chars": 12127, "completion_chars": 38, "tool_calls": 1,
   "context": {"system": 2413, "cards": 512, "digest": 1100, "scene": 8023},
   "usage": null, "est_prompt_tokens": 3031, "est_completion_tokens": 9}
  ```
- `usage-summary` over `data/recordings/usage/` (includes files written by
  the CLI test suite through `main()` — wiring proof for run/chat/research):
  per-call-type buckets printed for annotation (13 calls), chat (3),
  merge-settle (4, 1 error), researcher (9) with chars, tool calls,
  attempts-by-status, est tokens.

## Open questions / next steps

- **omp usage field names remain UNKNOWN** (Q1). When quota allows, one
  cheap live RPC call with the instrumented client will settle it: whatever
  the runtime emits now lands in `raw["agent_end"]` and the record.
- **Adjudication goal setup** (next session, per handover): the researcher
  quote-audit of ~40–60 shadow-flagged candidates now gets a bounded cost
  measurement for free — run it via the CLI `research` command and read
  `usage-summary` per-call-type afterwards. Telemetry makes measurable:
  chars in/out per call type, retry tax (attempts-by-status +
  error_types), context growth slope (context.component series over seq),
  and provider-vs-estimated token divergence once usage fields appear.
- Existing CLI tests write telemetry files to the default
  `data/recordings/usage/` (gitignored) — harmless, but a future cleanup
  could point them at tmp dirs.

