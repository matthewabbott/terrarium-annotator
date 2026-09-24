# 2026-09-14 — Deepseek cutover: plan + endpoint probes

Matt pivoted: local inference on the 2× DGX Spark cluster replaces the Kimi
subscription as the bulk-reading path. Plan: docs/plan/deepseek-cutover.md
(commit 82992f5). This worklog records the probe evidence.

## Endpoint (from ~/Cluster README + live probes)

- `http://127.0.0.1:8888/v1`, model id `DeepSeek-v4.1-Flash-EXL3`.
- vLLM `0.1.dev20904+g179dd0fa9-tp2`, EXL3 2.9bpw, TP=2 over CX7;
  `--tool-call-parser deepseek_v41`, `--reasoning-parser deepseek_v41`;
  `max_model_len` 600000 (KV pool 774k tokens).

## Probe results (2026-09-14, all local, zero quota)

1. `GET /v1/models` → model listed, max_model_len 600000.
2. Basic completion (`enable_thinking=false`): content "OK",
   `usage: {prompt_tokens: 9, completion_tokens: 2,
   prompt_tokens_details: {cached_tokens: 0, ...}}` — REAL tokens incl.
   cache accounting. Telemetry captures verbatim via `raw["usage"]`.
3. Native tools: weather-tool probe returned a proper `tool_calls` array
   with stringified JSON arguments and `finish_reason: "tool_calls"` —
   exactly `parse_choice`'s expected shape.
4. Thinking on (default): `reasoning` separated from `content` by the
   parser; `reasoning_tokens` counted separately; content clean. No
   empty-content slip (n=1 — insufficient; watch in ladder telemetry).
5. Tool ROUND-TRIP (assistant tool_call + role:tool result → final turn):
   content "The weather in Paris is currently cloudy..." — the template/
   parser handles the full loop, not just the one-shot call.

## Decisions resolved by the probes

- Tool convention: NATIVE (probe-verified). Text `<tool_call>` fallback
  only if ladder telemetry shows discipline slips.
- `chat_template_kwargs.enable_thinking=false` is a ladder variant axis
  for batch annotation (thinking tokens cost latency).
- Official sampling for this checkpoint is temperature=1.0/top_p=0.95;
  our client default 0.4 — flag for variant tuning.

## Status of the kimi ladder goal

Still open; holdout/judge blocked on the Sep 19 weekly reset. The Deepseek
ladder arm can run immediately (local). Confound noted in the plan:
Deepseek holdout measures model+prompt, not prompt alone; kimi holdout
remains the clean prompt-only comparison if Matt wants it post-reset.
