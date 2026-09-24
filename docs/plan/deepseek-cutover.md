# Deepseek v4.1 Flash cutover plan (Matt, 2026-09-14)

Local DGX Spark inference replaces the Kimi subscription as the bulk-reading
path: unlimited runs, gated by disk and tokens/s instead of weekly quota.
The ladder infrastructure (prompts-as-data, scorecard, telemetry, breaker)
is the onboarding instrument — this plan is sequenced to reuse it.

## Inputs (RESOLVED 2026-09-14, probes below)

- Endpoint: `http://127.0.0.1:8888/v1` (spark1), model id
  `DeepSeek-v4.1-Flash-EXL3`. vLLM `0.1.dev20904+g179dd0fa9-tp2`,
  EXL3 2.9bpw, TP=2 across both Sparks (~/Cluster README).
- Serving stack: vLLM with `--tool-call-parser deepseek_v41` and
  `--reasoning-parser deepseek_v41` enabled; thinking defaults ON
  (`reasoning_effort: high`).
- Served context: `max_model_len` 600000 (KV pool 774k tokens) — LARGER
  than kimi-k2.5's 262k; our RunnerConfig default stays conservative.

## Step 1 — endpoint probe (DONE 2026-09-14, local, zero quota)

Results (all against the live endpoint):
- **Basic completion**: works; `usage` carries REAL prompt/completion
  tokens INCLUDING `prompt_tokens_details.cached_tokens` — telemetry's
  `raw["usage"]` capture picks this up verbatim with zero changes. First
  real token counts in the project (kimi RPC never exposed them).
- **Native tool calling**: works. Proper `tool_calls` array, stringified
  JSON arguments, `finish_reason: "tool_calls"` — exactly the shape
  `parse_choice` (llm/base.py) consumes. No text-convention fallback
  needed unless ladder telemetry shows discipline slips.
- **Thinking mode**: reasoning separated into `reasoning` field by the
  parser; `content` clean; `reasoning_tokens` counted separately in
  usage. No empty-content slip observed (n=1 — watch it in ladder
  telemetry before building a retry).
- **Knob discovered**: `chat_template_kwargs.enable_thinking=false`
  disables reasoning — likely the right setting for batch annotation
  (latency/completion-token cost); ladder variants should A/B it.
  Official sampling for this checkpoint: temperature=1.0, top_p=0.95 —
  our client default is 0.4; flag for variant tuning.

## Step 2 — wiring (small build, L0-tested)

- CLI: `--base-url` + `--provider kimi|local` are NEW flags (model
  selection already exists as `--model`); env-sourced default
  (`TERRARIUM_BASE_URL`), per AGENTS.md config rules. Apply to run,
  research, adjudicate, AND chat. Provider selects OmpRpcClient vs
  OpenAICompatibleClient.
- `OpenAICompatibleClient` payload gaps (block thinking-off variants):
  it currently sends only messages/temperature/max_tokens/model/tools.
  Add passthrough for `chat_template_kwargs` (the enable_thinking knob)
  and `top_p`; both become ladder variant axes, so they must be
  client-settable and recorded in run_meta.
  verify its `parse_choice` handles the server's response shape (probe
  from step 1).
- CLI: `--base-url` / `--model` on run/research/adjudicate; env-sourced
  defaults (`TERRARIUM_BASE_URL`), per AGENTS.md config rules. Provider
  selection: `--provider kimi|local` choosing OmpRpcClient vs
  OpenAICompatibleClient.
- `--context-tokens` flag (RunnerConfig field exists; CLI never exposed
  it) — card budget derives from it, so the served window MUST be set
  correctly or injection silently over/under-fills.
- Empty-content retry gap: OmpRpcClient retries EmptyResponseError;
  OpenAICompatibleClient retries only transport/5xx. If the probe shows
  empty-content slips (reasoning models do this), add a content-empty
  retry to the OpenAI path with attempt-observer events (telemetry seam
  already exists). Gate on probe evidence — don't build speculatively.
- Quota breaker: no-op on the local path (no weekly window). Optional
  replacement: wall-clock/tokens-per-hour budget from telemetry. Not
  required for cutover.

## Step 3 — tool-convention decision (RESOLVED 2026-09-14)

Native `tools=`. Probe-verified on this deployment: one-shot tool call
returned a proper `tool_calls` array (stringified JSON args,
`finish_reason: "tool_calls"`), and a round-trip (assistant tool_call +
`role: tool` result → final content) completed cleanly — see the worklog.
The text `<tool_call>` convention is the documented fallback ONLY if
ladder telemetry shows discipline slips (malformed/missed calls); building
it is deferred until that evidence exists.

## Step 4 — ladder onboarding (the parity bar)

"Up to where we were with kimi-k2.5" = the ladder scorecard on threads
1–5, Deepseek arm(s) alongside the existing kimi arms:

- deepseek-flash + reader-v2 (incumbent prompt as-is) first: the scorecard
  (gold coverage exact-surface, flag rate, entries/1k, pair candidates,
  attempts-by-status — retry tax shows tool-discipline slips) tells us
  WHAT diverges: tool discipline, reasoning-empty slips, verbosity,
  admission behavior.
- Then tuned variants if needed (deepseek-friendly wording; text tool
  convention if native fails). Prompts stay short/contextual per the
  anti-Goodhart rule.
- **Parity bar is numeric**: DeepSeek arms report the SAME scorecard on
  the SAME threads as the kimi arms — baselines: v1 20/52 exact gold
  (243.5 entries/1k posts, flag rate 26.7%, 88k est tokens/covered), v3
  23/52 (539/1k, 42.2%, 191k). Parity = exact-surface coverage within a
  couple pairs of those, verify-clean, no tool-discipline retry spike.
- Scorecard upgrade task (Step 2/4 boundary): ladder.py aggregates only
  chars/4 ESTIMATES today; telemetry records carry real provider `usage`
  on this path. Make the scorecard prefer provider tokens when present
  and surface wall-clock + tokens/s per arm (durations are already in the
  records). Until that lands, cost columns are est-only for kimi arms and
  real for DeepSeek arms — label, never mix silently.
- **Confound to state plainly**: a Deepseek holdout run measures
  model+prompt, not prompt alone. The kimi Anthus holdout (blocked on
  quota until Sep 19) remains the clean prompt-only comparison if we
  still want it; otherwise the Deepseek ladder result IS the restart
  input and the kimi holdout is moot. Matt's call.

## Safety / acceptance gates (every DeepSeek run)

- Every arm writes a FRESH DB under data/exp/ — never banished.db
  (read-only always), never annotator-full.db / annotator-t1-40.db.
- `terrarium-annotator verify` exit 0 on every arm DB, or the arm is
  invalid (same rule as the kimi ladder).
- First-run smoke gate before any ladder arm: a real multi-round session
  (tool call → result → continuation) ending in a quote-GATED write that
  verify accepts — not just /v1/chat/completions probes.

## Step 5 — resume aspirations on the local path

- Full-run restart decision on Deepseek ladder results (unlimited runs →
  the 10–11-weekly-windows wall-time concern becomes a tokens/s concern;
  measure throughput on the ladder arms first).
- Critic: ChatGPT 5.6 Luna via API path (OpenAICompatibleClient + env
  key) — separate provider, uncorrelated blind spots, as planned.
- Efficiency laddering (threads 35–40 state-as-of-34) now unblocked:
  prefix-caching and context-trim variants are free to run locally.
- Specialist passes / NG+ reread: cheap enough locally to actually try.

## Risks / watch-items

- Quant quality: a heavily quantized flash model may slip on the
  quote-verbatim discipline (paraphrased quotes get rejected at the gate —
  watch rejection rates in telemetry; a spike = model can't copy verbatim,
  which is fatal for our write path and NOT fixable by prompting around).
- Context window: if the served window is small (e.g. 32k), per-batch
  card budgets shrink and the digest budget dominates; whole threads no
  longer fit. Ladder scores at the REAL served window, not the default.
- Stateful serving: if the server keeps sessions warm, prefix caching may
  come free across tool rounds — measure via telemetry durations.
