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

## Wiring

_Pending._

## Metrics

_Pending._

## Smoke gate

_Pending._

## Parity arm

_Pending._

## Report

_Pending._
