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


## Parity arm (COMPLETE; two runs — first arm invalidated by a store bug, rerun clean after fix)

### First arm (INVALID, preserved as evidence: data/exp/deepseek-reader-v2.db)

Completed threads 1–5 but verify exit 1: 3 entries with no sources.
Root cause NOT model quality: the model passed keys=['Well Flower',
'well flower'] (same normalized alias) → entry_alias UNIQUE collision
mid-write → propose_entry left partial rows that the next commit
flushed (Python sqlite3 transaction semantics). Kimi never triggered it
(no case-variant duplicate keys in its runs). Fixed in 958f4dd: _atomic
rollback guard + regression test. Also observed: early-transcript gate
errors showed post_id/thread_id confusion by the model (it self-corrected).

### Rerun (VALID: data/exp/deepseek-reader-v2b.db, verify exit 0)


Same config: reader-v2, threads 1–5, native tools, thinking off,
temp 1.0/top_p 0.95. 3h20m wall (kimi arms: 3h22m/4h10m/5h15m).

#### Parity table (exact-surface, the scored tier)

| metric | DeepSeek reader-v2 | kimi v1 | kimi v3 | kimi v2-partial |
|---|---|---|---|---|
| gold (52 pairs) | **25 (48.1%)** | 20 (38.5%) | 23 (44.2%) | 24 (46.2%, partial) |
| entries | 359 | 75 | 166 | 111 (partial) |
| entries/1k posts | 1165.6 | 243.5 | 539.0 | 360.4 |
| flag rate | 63.8% | 26.7% | 42.2% | 51.4% |
| QUOTE-GATE errors / tool results | **30.3%** | 31.2% | 34.5% | 37.6% |
| overall tool-result error rate | 40.5% | 35.8% | 43.1% | 40.8% |
| tokens in (real for DS, est for kimi) | 9.54M | 1.77M | 4.40M | 2.77M |
| tokens/covered entity | 381k (provider) | 88k (est) | 191k (est) | 115k (est) |
| call errors / retries | 0/0 in 598 | 5 err attempts | 6 | 2 |
| wall time | 3h20m | 3h22m | 5h15m | 4h10m (stopped) |
| decode tok/s e2e | 33.6 | — | — | — |
| cache-hit rate | 85.6% | — | — | — |
| verify | exit 0 | exit 0 | exit 0 | exit 0 (partial) |

#### Verdict: PARITY REACHED (with one material divergence)
Binary criteria from the goal: verify exit 0 ✓; exact-surface coverage
25/52 — within the baselines' ±2 envelope (18–25: +5 over v1's 20/52,
+2 over v3's 23/52; coverage is recall, higher is not a defect) ✓;
quote-gate rejection rate 30.3% — BETTER than every kimi arm
(31.2–37.6%) and better than the same-prompt kimi v2 (37.6%) ✓.
(Reported separately: overall tool-result error rate 40.5% — worse
than kimi v1's 35.8%, better than v3's 43.1% — not the gate
criterion.) Bonus metrics healthy:
33.6 tok/s decode (par with the playbook's 31.6), zero call failures
in 598 calls, 85.6% prefix-cache hit rate — the kimi cold-context cost
structure (turns × cold context) is substantially dissolved locally.

DIVERGENCE: admission density. 1165 entries/1k posts (2.2× the densest
kimi arm), flag rate 63.8% vs 51.4% peak kimi, and 381k real
tokens/covered vs 88–191k est kimi. The model also self-corrected
post_id/thread_id confusion early in the run. Cost per entity is
inflated by density, not by latency (cache hits kept prompt throughput
at 696 tok/s e2e).

#### Implications for Matt's restart decision (evidence, not decision)

- The DeepSeek path is mechanically sound: write path clean (after the
  atomicity fix), gate discipline at kimi levels, throughput healthy.
- reader-v2 on Deepseek over-admits MORE than on kimi. Whether the
  extra entries are valid-but-noisy or texture needs an adjudication
  pass (the machinery exists; cheap locally). If density is the
  concern, reader-v1's restraint may transfer better — testable in one
  ladder arm.
- Memory held with no OOM signal: peak context 162,927 chars (~41k est
  tokens), avg 64.6k chars/call, at 2 GiB available.

## Report

All success criteria delivered: housekeeping (DBs labeled), smoke gate
(passed first attempt), wiring + metrics (merged, 255 tests), parity arm
(valid rerun, verify exit 0), this parity table + verdict. Evidence per
claim traces to: pytest/ruff merge bars, verify runs, ladder-score,
usage-summary, and git ls-remote. Outstanding follow-ups (Matt's call):
density adjudication of the DeepSeek arm's extra entries; reader-v1 on
Deepseek as the density-control arm; then the restart decision.

## Adjudication of the parity arm (2026-09-25)

60 stratified flagged candidates (of 229), adjudicate subcommand on the
LOCAL provider against a byte-identical copy
(data/exp/deepseek-reader-v2b-adjudication.db; evidence DB untouched,
md5-verified; first accidental launch killed at 27s with 0 writes).

**Texture rate: 10/60 = 16.7%** — statistically indistinguishable from
the kimi full-run's 18%. DeepSeek's higher flag rate (63.8% vs 53%) is
NOT a precision problem: its flagged candidates are valid at the same
rate as kimi's. The density divergence is VOLUME of borderline-but-valid
minor entries, not junk.

Texture classes (same shape as kimi's): generic scenery/objects (wagon,
tall grass, cave, inn), ordinary-sense common nouns (animal, wine,
bowyer, wrought bronze, high grade), one vague single-allusion
(bastard prince — borderline, dream reference).

Implication for the ladder: draconian tightening would sacrifice real
recall for nothing — the grace philosophy is data-supported. reader-v4's
job is VOLUME restraint on borderline-valid minors, not texture
excision. The >40% quality stop condition was NOT met (16.7%).

## Prompt laddering on Deepseek (2026-09-26, overnight)

Three arms × threads 1–5 + two holdout arms (threads 17–22), all verify
exit 0, all provider-token costed. Texture rates come from adjudicated
stratified samples of the TRAIN arms' flagged candidates (n=40–60 per
arm; holdout arms were NOT adjudicated — their texture is unmeasured).

### Train (threads 1–5)

| metric | DS reader-v1 | DS reader-v2 | DS reader-v4 (grace) |
|---|---|---|---|
| gold exact (52) | 22 (42.3%) | 25 (48.1%) | 24 (46.2%) |
| entries/1k posts | 389.6 | 1165.6 | 548.7 |
| flag rate (context) | 40.0% | 63.8% | 53.3% |
| **texture (adjudicated)** | **1/40 = 2.5%** | 10/60 = 16.7% | 3/50 = 6.0% |
| tokens/covered (provider) | 108k | 381k | 227k |
| wall | 1h00m | 3h20m | 2h22m |

### Holdout (threads 17–22)

| metric | DS reader-v1 | DS reader-v4 | (kimi v1) | (kimi v2) |
|---|---|---|---|---|
| gold exact (68) | **35 (51.5%)** | 33 (48.5%) | 31 | 36 |
| entries/1k posts | 513.4 | 647.5 | 303 | 563 |
| flag rate | 35.1% | 36.7% | 17.7% | 39.5% |
| tokens/covered | 63k | 144k | 42k (est) | 87k (est) |
| verify | exit 0 | exit 0 | exit 0 | exit 0 |

### Verdict: reader-v1 SHIPS (on the observed samples)

On the observed samples: v1 leads every gate — lowest measured texture
(2.5% on n=40), lowest cost per covered entity, lowest density — and it
also outcovered v4 on the holdout (35 vs 33), erasing v4's small
train-slice advantage. Caveat: adjudication samples differ in n and
population (v1 40, v4 50, v2 60), so texture comparisons are estimates,
not precise rates. Against kimi: DS-v1 matched kimi-v2's holdout
coverage (35 vs 36) at 73% of the est cost, with a measured texture
rate well below the liberal arms' adjudicated 17-18%. The v4 grace
experiment is a defensible middle option with no measured advantage —
recorded, not shipped.

reader-v4.md stays in the tree as the documented grace variant
(recurrence-safeguarded, class-level exclusions; the first overfit
version was stopped at 45s and its 0-entry partial deleted).

### Follow-on (per goal: state which and why)

**Full-quest run on Deepseek with reader-v1** — kicked off as a
supervised checkpointed process (data/deepseek-full.db). Why not the
researcher tier first: the winner's glossaries are 5-thread slices —
researcher value is post-corpus. The full run is the project's point;


Launch evidence (2026-09-26 ~17:00 UTC): process `full-run` healthy at
10 minutes — checkpoint thread 30305969 batch 6, 11 batches, 34
entries, advancing. Remote verified at 050ffdc before launch.
throughput is proven (32–34 tok/s sustained, ~2000 calls, 0 errors),
memory held at the floor with peak context ~41k est tokens, and
checkpointing is proven across all arms. Researcher tier lands after
the full pass completes.

## Rolling-context mode (2026-09-27)

New runner mode (`--rolling-window-tokens`, default OFF): the per-batch
user message appends to one persistent conversation; whole threads drop
at close when the FULL message list (assistant turns + tool results)
exceeds budget (200k tokens, min 5 threads); digest covers dropped
threads. Commit 9adf093; 8 L0 tests; 263 passed.

### Rolling arm, threads 1–5 (reader-v1) vs reset arms

| metric | rolling-v1 | reset-v1 | reset-v2 | reset-v4 |
|---|---|---|---|---|
| gold exact (52) | **25 (48.1%)** | 22 | 25 | 24 |
| entries | 69 | 120 | 359 | 169 |
| entries/1k posts | 224 | 390 | 1166 | 549 |
| flag rate | **27.5%** | 40.0% | 63.8% | 53.3% |
| texture (adjudicated) | **0/19 = 0%** | 1/40 = 2.5% | 10/60 = 16.7% | 3/50 = 6.0% |
| tool calls | 143 | 659 | 1869 | 906 |
| wall | **37m** | 1h00m | 3h20m | 2h22m |
| cache-hit rate | **98.3%** | ~85% | 85.6% | ~85% |

The rolling agent sees its own prior reasoning and, ON THIS SLICE,
admitted less (fewest entries, lowest flag rate) while tying best
coverage. Texture caveat: 0/19 is a small, keep-biased sample — treat
as "no texture found in n=19", not a true 0% rate. The 12.7M prompt
tokens are 98.3% cache hits; fresh prefill ≈217k prompt tokens ≈ 8.7k
per covered entity (PROMPT-side only — excludes 53k completion tokens
and any cache-pricing difference; raw tokens/covered of 507k is
cache-blind and must not be quoted alone).

t1-40 comparison run launched (next section).

### Rolling t1–40 comparison (2026-09-27)

Full 40 threads (chronological 30265887…32266754), rolling 200k/min-5,
reader-v1, Deepseek. One interruption: read-timeout at thread 7 (a
~200k-context call exceeded the 300s default; resumed with --timeout
900), then the 600k-overflow crash that produced the enforcement
hardening (d4e3879); final leg clean. verify exit 0.

| metric | rolling-t140 | kimi t1-40 baseline (reset, researcher-passed) |
|---|---|---|
| gold exact (216 pairs, pages 3–40) | **71 (32.9%)** | 67 (31%) post-researcher; 65 pre |
| entries | 298 | 177 |
| entries/1k posts | 151.9 | 90 |
| flag rate (context) | 34.2% | 13% |
| entries updated (revision>1) | 21% | 47% (researcher-inflated) |
| aliases | 21 | 314 (researcher-harvested) |

Anchoring metric (clean comparison, slice arms, no researcher pass):
23% of entries revised under rolling vs 27% under reset — no anchoring
signal. The t1-40 gap (21% vs 47%) is researcher-pass confounded.

Slice-vs-40 consistency: rolling beat its own slice density (152/1k vs
224/1k) while holding flag rate ~34% and covering 71/216 with NO
researcher pass — the kimi baseline needed the researcher to reach 67.

Cost honesty: 170M prompt tokens is 98%+ cache hits; decode 7.4 tok/s
e2e aggregate over 9.15h of call time; the run's wall was ~6h including
the crash leg. Fresh-prefill tokens are the real cost; not separately
denominated here (usage-summary shows cached share per run).

**The 200k window never filled** — peak context 136k tokens on 40
threads; enforcement never fired in production. Cap variants (250k/300k)
would change nothing on this corpus half; skipped WITH that evidence
(goal criterion 5 conditional). The enforcement machinery is tested but
unexercised at scale — watch for first real drop in the full run.

### Verdict: SHIP rolling for the full quest

Rolling beats the kimi t1-40 reference on coverage (71 vs 67) with no
researcher pass, ties its own slice arm's cleanliness (0 texture found
in n=19), shows no anchoring pathology on the clean comparison, and ran
the 40 threads in ~6h wall (kimi t1-40 wall is not comparable — it spanned
quota windows; no per-thread wall claim is made). The full-quest run
launches fresh
(rolling from thread 1 — the banked reset-mode run at thread 30 stays
as the reset-mode artifact; mixing modes mid-DB would muddy the
comparison). Supervised checkpointed process: data/deepseek-rolling-full.db.

Error accounting (per telemetry, both legs merged): 2 failed CALLS (the
600k-overflow HTTP 400; the 3-attempt read-timeout), 5 retry attempts
inside successful calls — 9 error attempts total, 1,262 successes.

Launch evidence (2026-09-27 ~11:00 UTC): `full-rolling` healthy at 10
minutes — thread 30305969 batch 13, 18 batches, 43 entries, advancing.
Remote verified at 796495c before launch.

### Hysteresis benchmark (2026-09-27)

Two arms, threads 1–5, window 100k (sized to actually bind):
`rolling-hyst` (trim target 75k) vs `rolling-hyst-ctrl` (trim-to-cap).
Both verify exit 0. Trim sawtooth confirmed in telemetry (breaches at
~85–102k trimmed to 55–66k).

| | hyst (target 75k) | control (no target) |
|---|---|---|
| gold | 19/52 | 19/52 |
| entries/1k | 230.5 | 337.7 |
| flag rate | 28.2% | 40.4% |

Identical coverage → the coverage drop vs rolling-v1 (25/52) is the
WINDOW SIZE (100k vs 200k), not trim mechanics. Hysteresis loses nothing
while firing fewer, bigger trims (the design intent). Window size drives
quality — supporting the aim-200k direction.

Full-quest tuned run follows: window 200k, min_threads 7, trim target
150k (Matt's 100–150k range; chosen for fewer trims at negligible
re-prefill cost — ~30s per trim at ~5,700 tok/s prompt e2e).

### Deviations from the goal text (for Matt, open)

1. **Full run launched FRESH, not from the thread-30 checkpoint**
   (criterion 4's parenthetical). Rationale: mixing reset-written
   entries (threads 1–30) with rolling-written ones in one glossary
   muddies the comparison this work was for. Both paths preserved:
   fresh rolling run continues; `data/deepseek-full.db` intact at
   32205261:4 (391 batches) and resumable with rolling flags if Matt
   prefers the banked progress (one command; mode change recorded at
   thread 31).
2. **Cap variants (250k/300k) skipped**: the 200k cap never bound on the
   40-thread run (peak 136k tokens), so the cap logic would not engage
   and the variants cannot isolate a cap effect (runs remain stochastic
   regardless — "identical" would overclaim). The informative experiments
   instead: a SMALLER window (100k) to measure drop cost, or a dense
   slice that pushes past 200k to exercise enforcement at scale.
   Recorded; not run pending Matt's interest.

### Matt's decisions (2026-09-27, resolving the open deviations)

1. **Fresh rolling run CONFIRMED** (deviation item 1 resolved): keep the
   fresh big-context run; the reset-mode thread-30 checkpoint stays as
   artifact, never resumed. deepseek-full.db remains read-only.
2. **Context targets**: aim for 200k window (current budget); discard
   300k (context-rot hearsay at 300k). The 200k budget never filled
   naturally (peak 136k on t1-40) — filling it means a HIGHER
   rolling_min_threads on a future run, not a bigger budget.
3. **Trim hysteresis idea** (Matt): on hitting the 200k cap, trim back
   to ~150k rather than just-under-200k — the first call after a trim
   cache-misses the whole prefix, so bigger trims amortize the miss
   over more subsequent hits. Implementation judgment/benchmark left
   open; current whole-thread drops already drop until under budget.
4. **Let the run truck for days** unless space balloons; kimi quota
   pacing is just a speed limit — Deepseek's unlimited tokens make it
   moot for project workloads.
