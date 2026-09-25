# 2026-09-25 — DeepSeek parity cutover (goal)

Goal: measured coverage parity between the local DeepSeek path
(127.0.0.1:8888/v1, DeepSeek-v4.1-Flash-EXL3) and the kimi annotator path,
ending in a parity verdict. Spec: docs/plan/deepseek-cutover.md;
baselines: docs/worklog/2026-09-14-prompt-ladder.md.

Sequencing note: the objective lists smoke gate before wiring, but the
smoke gate needs the provider flags to drive the annotator at the server —
so wiring's flag/client portion runs first (dependency, not scope change).

## Housekeeping

Ladder/holdout DBs moved to `data/exp/kimi-k2.5/` (provenance label per
Matt; content untouched, moves only):

| new path | was |
|---|---|
| data/exp/kimi-k2.5/ladder-reader-v1.db | data/exp/ladder-reader-v1.db |
| data/exp/kimi-k2.5/ladder-reader-v2.db | data/exp/ladder-reader-v2.db (PARTIAL/INVALID arm — quota-stopped) |
| data/exp/kimi-k2.5/ladder-reader-v3.db | data/exp/ladder-reader-v3.db |
| data/exp/kimi-k2.5/holdout-reader-v1.db | data/exp/holdout-reader-v1.db |
| data/exp/kimi-k2.5/holdout-reader-v2.db | data/exp/holdout-reader-v2.db |

Canonical DBs (data/annotator-full.db, data/annotator-t1-40.db) stay in
place — referenced across docs and worklogs; their model provenance lives
in run_meta/pass_id.

## Wiring (COMPLETE, commit 0cf6af2)

`--provider kimi|local` + `--base-url` + `--no-thinking` + `--top-p` on
all four model commands; `--context-tokens` on run; OpenAICompatibleClient
gains top_p/chat_template_kwargs passthrough; run_meta records sampling
provenance. 9 L0 tests. 247 passed.

## Metrics (COMPLETE, commit a253df8)

usage_tokens normalizer (nested/flat, missing=absent); usage-summary
provider token sums + e2e tok/s + cache-hit + reasoning share; scorecard
provider-preferred headline with cost_source label. 7 L0 tests. 254
passed.

## Smoke gate (PASSED first attempt, 2026-09-25)

Run: `run --provider local --model DeepSeek-v4.1-Flash-EXL3
--no-thinking` on a fabricated 2-thread corpus (data/exp/smoke-corpus.db,
the "Zephra stone" story), DB data/exp/smoke-deepseek.db.

- Multi-round session worked: 17 calls, 25 tool calls, 0 errors/retries,
  2m01s for 2 batches + 1 merge settle.
- `verify` exit 0 — all quotes verbatim (mechanically checked).
- Merge-tree settle produced a clean single-line gist (OptMem format
  compatible with the deepseek_v41 template).
- Gloss fidelity strong on n=4 entries: "Its nature, origin, and purpose
  are not yet explained" — proper epistemic restraint, no overreach.
- First REAL token telemetry: 59k prompt / 4.2k completion tokens;
  **34.8 tok/s decode e2e** (par with the playbook's 31.6 measured
  single-stream); **cache_hit_rate 0.811** — vLLM prefix cache hits
  across tool rounds, which dissolves much of the kimi cold-context cost
  structure locally; reasoning_share 0 (thinking-off honored).


## Parity arm

_Pending._

## Report

_Pending._
