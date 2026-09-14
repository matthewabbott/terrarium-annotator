# 2026-09-14 — Prompt laddering: infrastructure + first bounded A/B

Goal: prompts-as-data + provenance, ladder harness with scorecard, then a
train/test experiment — tune on threads 1–5, validate on the Anthus
academy arc as holdout. Anti-Goodhart: prompts stay short/contextual;
the winner must hold on the holdout or the ladder says so. Multi-pass
production shapes are HYPOTHESES with defined evidence, not builds.
Circuit breakers at 0.60 weekly for this goal's live runs (adjustable
via --quota-breaker).

## Phase 0 — framing (COMPLETE, commit dfcdb17)

prompt-laddering.md gained: train/test split (tune 1–5, Anthus holdout,
no fiat picks), multi-pass hypothesis with measured-only economics (10–25×
quarantined as unattributed proxy), NG+ epistemics requirement, and the
gold-coverage scoping contract (denominator = processed threads' pages).

## Phase 1 — prompts as data (COMPLETE, commit dfcdb17)

- prompts/reader-v2.md = current relaxed prompt (byte-verified vs the
  in-code constant). prompts/reader-v1.md = t1–40 baseline recovered from
  commit 01e19d0^ (content-verified). prompts/reader-v3.md = v2 with two
  changes only: admission audit test replacing "when in doubt, add it",
  and a pinned 8-tag vocabulary (mechanic/character/faction/location/
  item/creature/document/concept).
- Runner loads prompts from files (default reader-v2); run_meta records
  prompt vintage + model; CLI --prompt. 6 L0 tests; 228 passed.

## Phase 2 — harness (COMPLETE, commit 1cba2bd)

- ladder.py scorecard: slice-scoped gold coverage (page N = Nth
  chronological thread — verified against corpus titles + label presence
  on pages 3/4/5 BEFORE any live run), flag rate, token-subset pair
  candidates (heuristic proxy),
  telemetry cost, est tokens per covered entity (N/A on zero coverage).
- CLI ladder-score. 9 L0 tests; 237 passed.

## Phase 3 — ladder runs

### Budget policy (recorded before any live run; advisory-driven)

The 0.60 breaker is an absolute shared-window ceiling, not a per-variant
allowance. At 17% weekly used, ~43 points fund: 3 train arms + holdout +
judge. Policy:
- Run arms cost-ascending (v1 conservative first, then v3, then v2) to
  maximize completed arms if the ceiling approaches.
- Pre-launch gates: abort ladder if 7d usage ≥ 0.40 before v3; ≥ 0.50
  before holdout; ≥ 0.55 before judge. An arm stopped by quota is INVALID,
  reported as such — never partially scored.
- Every arm runs with --quota-breaker 0.60 as the hard backstop.
- Pre-v2 launch (at 45%): no staged gate was declared for v2 — decision
  was backstop-only (0.60), on the evidence that v2-conditions on
  threads 1–5 cost ~37k chars/batch early (full-run transcript data) and
  the arm was goal-required for winner selection. Recorded post-hoc for
  policy completeness; in the event the breaker fired and the arm was
  classified invalid.


All arms: threads 1–5 (30265887…30459080), kimi-k2.5, --quota-breaker
0.60, telemetry on, one DB per variant under data/exp/.

| arm | wall | batches | entries | quota delta | status |
|---|---|---|---|---|---|
| reader-v1 | 3h22m | 63 | 75 | +9 pts (17→26%) | VALID, verify exit 0 |
| reader-v3 | 5h15m | 63 | 166 | +19 pts (26→45%) | VALID, verify exit 0 |
| reader-v2 | 4h10m | 49/63 | 111 | +15 pts (45→60%) | **INVALID — breaker halt exit 3 at 60% weekly, thread 5 batch 3 checkpoint preserved.** Partial diagnostics only. |

Holdout (Anthus arc) ABORTED: 7d usage hit the predeclared 0.50 gate.
Judge sample ABORTED: 0.55 gate. Both are post-reset work (Sep 19).
Anthus holdout slice identified for then: threads 17–22 chronological
(30936089, 31184164, 31223331, 31247134, 31260696, 31283673) — the
academy arc by Anthus/academy mention density (13 and 13 mentions in
threads 17/22; gold pages 17–22 exist).

## Phase 4 — report

### Train table (threads 1–5; gold in-scope = pages 3–5, 52 pairs)

| metric | reader-v1 (conservative) | reader-v3 (audit+tags) | reader-v2 (relaxed, PARTIAL/INVALID) |
|---|---|---|---|
| entries | 75 | 166 | 111 (79% of slice) |
| entries/1k posts | 243.5 | 539.0 | 360.4 (partial) |
| flag rate (distribution signal) | 26.7% | 42.2% | 51.4% (partial) |
| gold exact-surface | 20/52 (38.5%) | 23/52 (44.2%) | 24/52 (46.2%, partial) |
| est tokens in | 1.77M | 4.40M | 2.77M (partial) |
| tokens/covered entity | **88k** | 191k | 115k (partial) |
| attempts err/success | 5/336 | 6/387 | 2/276 |
| token-subset pair candidates (heuristic proxy, not a duplicate count) | 5 | 17+ | 7 (partial) |

### The alias-gap diagnostic (NOT a scored tier — hypothesis only)

Exact-surface coverage says all three arms missed all 11 books. MANUAL
alias-aware check shows the opposite for v3: it holds "Beginner's Guide to
the Elements: Earth/Metal Edition", both "Metamagic Instruction" scrolls,
"priest's journal", "courier's missive", "a treatise on advanced water
magic", "five effects every vatis should know", "Grandmaster Rostam('s
journal)" — ≈9–10/11 book entities under different surfaces. v1 holds 5
book-ish entries despite no book callout (incl. "book of rituals" — which
CONTRADICTS the stocktake's "baseline whiffed books entirely"; reconcile:
the t1–40 baseline may have lost them to later-pass revision, or the
whiff was thread-2-specific partial). Exact-surface coverage is a FLOOR;
the scorer needs an alias-aware tier before coverage numbers guide
decisions. (mik/suresh class: the known alias-table hole, researcher
territory.)

### Findings

1. **v3's audit test did NOT restrain admission** — 166 entries vs v1's
   75; 539/1k posts. The pinned tag vocabulary also failed silently:
   v3 produced duplicate fragment pairs ("magic"/"magic tomes",
   "metamagic"/"metamagic instruction: blast" ×3). Prose self-checks do
   not bind at write time.
2. **v1 is the cost-efficiency leader on exact-surface scoring** (88k
   tokens/covered vs 191k) with near-equal exact-surface coverage and half
   v3's flag rate. What v1/v3's flag rates mean for TEXTURE share is
   UNKNOWN: the adjudication 18% figure was measured on the full-run (v2)
   flag population and must not be multiplied onto different populations.
   A v1/v3 adjudication pass is the pending audit.
3. **v2-partial** tracked v3's shape (density between v1 and v3, flag
   rate 51% consistent with the full run's 53%) — consistent, no surprise.
4. Retry tax was small but real on all arms (5/6/2 error attempts);
   telemetry captured it per-arm.

### Verdicts
- **Train winner: none declared.** Exact-surface scoring (the only scored
  comparison): v1 leads cost/covered ~2×, coverage within 3–4 pairs of v3
  — with v2 invalid and the holdout unrun, that is ambiguity. Per the
  goal's clause, the incumbent (v2) goes to the holdout post-reset. NO
  restart recommendation on this data alone.
- **Multi-pass hypothesis status**: v1's cleanliness and cost advantage
  are measured facts on this slice. Whether its coverage deficit vs
  liberal prompts is real is UNRESOLVED: exact-surface says 3 pairs; the
  manual alias diagnostic (below) suggests it may partly vanish, but that
  diagnostic is not a scored tier. Confirm/disconfirm needs (a) the
  alias-aware scorer tier and (b) the holdout runs — both pending.
- **restart recommendation**: after Sep 19 reset, run reader-v2 on the
  Anthus holdout (threads 17–22) + reader-v1 holdout for comparison, then
  decide. If v1-class conservative holds coverage on the texture-heavy
  arc, restart with v1 and schedule specialist passes; else restart v2.

### Evidence

- Phase 0/1: commit dfcdb17, 228 passed. Phase 2: commit 1cba2bd, 237
  passed. Phase 3: verify exit 0 on all three variant DBs (v2 partial
  included); quota ledger 17→26→45→60%; breaker halt observed working
  (exit 3, checkpoint preserved).

### Open questions

- Alias-aware coverage tier in the scorer (labels + token-subset
  diagnostics, clearly separated from exact-surface) — the current
  exact-only floor misread book coverage by ~20 points absolute.
- v1-caught-books vs stocktake's "baseline whiffed books": reconcile
  against annotator-t1-40.db history (read-only) — was it thread-2
  partial, or post-pass loss?
- Do v3's fragment duplicates ("magic"/"magic tomes") need a write-time
  dedupe-before-propose check rather than prompt prose?
- Holdout + judge runs post-reset (Sep 19), per the plan above.
