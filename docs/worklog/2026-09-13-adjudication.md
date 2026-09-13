# 2026-09-13 — Quota circuit breaker + bounded adjudication pass

Goal (three gated phases): quota circuit breaker → adjudication mechanics →
bounded instrumented adjudication pass over ~40–60 of the 239 shadow-flagged
candidates in `data/annotator-full.db`, then a qualitative report comparing
findings to the t1–40 baseline insights. Spec: the /goal prompt (Matt,
2026-09-13); background: docs/worklog/2026-09-12-full-run-stocktake.md rec #1.

## Phase 1 — circuit breaker

## Phase 1 — circuit breaker (COMPLETE, commit d176dc6)

- `quota.py`: `probe_weekly_fraction()` (omp usage --json, 7d usedFraction;
  every failure mode raises QuotaProbeError — headroom never assumed) and
  `make_quota_breaker(threshold)` raising QuotaExceeded at/over threshold.
- Runner: check before each batch; halt leaves run_state at the next
  unprocessed batch → resume re-attempts it (tested). Researcher: check at
  session start + each round (tested: 0 calls on start-halt, exactly 1
  round on mid-round halt).
- CLI: `--quota-breaker` on run/research (default 0.50, <=0 disables);
  halt exits 3 with labeled message; `quota_check_factory` injectable so
  tests never touch the real probe.
- Researcher also gained `allowed`/`system_prompt`/`overview` params
  (defaults unchanged — Phase 2 needs them).
- 14 L0 tests; merge bar 202 passed, ruff clean.

## Phase 2 — adjudication mechanics (COMPLETE, commits efae62c, 98370a5)

- `demote_queue` table mirrors merge_queue (pending/accepted/rejected) —
  entry.status has no graveyard value (CHECK constraint) and migrating
  annotator-full.db was not authorized, so demotions are PROPOSED, human-
  applied. `GlossaryStore.propose_demotion` requires rationale + verbatim
  quote mentioning the term (same gate pattern as propose_merge).
- `ADJUDICATION_TOOLS`: fetch_entry/fetch_post/fetch_thread_range/
  recall_story/search_glossary/search_corpus + propose_demotion ONLY.
  Dispatcher rejection of every forbidden write (propose_entry,
  update_entry, add_alias, rename_entry, propose_merge, confirm_entry)
  is tested, plus positive dispatch of all allowed tools.
- Dedicated `adjudicate` subcommand hard-wires the allowlist; stock
  `research` keeps RESEARCHER_TOOLS and was not used. A byte-verified
  backup (`backup_db`, filecmp) is enforced before the writable DB opens;
  backup failure refuses the run (exit 2). Backup taken:
  `data/backups/annotator-full-20260913T002456.db`.
- Sampler: thread-first deterministic round-robin with unseen-class
  preference and middle-out fallback. NEEDED A FIX mid-phase: class-only
  allocation degenerated because the population's tags are freeform (65
  distinct "classes" over 239 rows — singleton strata starved thread
  coverage: 21/25 threads, first 8 picks all thread 30265887). Fixed
  version: 25/25 threads, 39 classes, no first-N degeneracy (tested).
- 17+2 L0 tests; merge bar 221 passed, ruff clean.

### Sampling frame (recorded per goal)

- Population: 239 deferred_candidate rows in annotator-full.db, all
  matched to live entries (0 unmatched).
- Population classes are noisy freeform tags (top: item 18, academy 16,
  Anthus 14, Licae ruins 14, anthus 12, character 11 — tag-vocabulary
  discipline is itself a finding, see Phase 4).
- Sample: 50 candidates, all 25 flagged threads, 39 tag classes,
  deterministic (re-runnable via load_flagged_candidates +
  stratified_sample at size 50). Persisted:
  `data/adjudication/adjudicate-20260913T005301/sample.json`.

## Phase 3 — the pass (COMPLETE; verify exit 0 after checker-bug fix)

- 5 chunk sessions (12/12/12/12/2), Kimi k2.5, ADJUDICATION_TOOLS,
  quota breaker active (0.50; usage moved 6% → 9% for the whole pass).
  Runtime 10m15s. Zero failed calls, zero retries, zero halts.
- **Writes proven clean**: table diff vs backup — entry/revision/
  entry_source/story_log/deferred_candidate/merge_queue IDENTICAL;
  only delta = 9 demote_queue inserts (all pending, human queue).
- **Verify gate: initially exit 1, RESOLVED as a checker bug (evidence
  below).** verify flagged 9 budget-compliance violations; the pre-pass
  backup reproduced them identically, proving they predated adjudication.
  Margin analysis then showed all 9 were over budget ONLY under verify's
  block-level accounting (len(block)//4, which adds separator newlines and
  loses per-card floor discounts: +27..+75 tokens) while UNDER or AT budget
  under the runner's enforced accounting (per-card sum of floored
  estimates, select_cards' actual contract: margins +0..-43). The run was
  compliant; the instrument mis-measured. Fix: verify now sums
  per-card `count_tokens(line)` — the exact accounting select_cards
  enforces — with a joined-rounding regression test. verify on
  annotator-full.db now exits 0. **Correction to the earlier claim in this
  log: the full run did NOT exceed its card budget; budget enforcement
  worked. The card-budget "restart blocker" is withdrawn.**
- Outcome: 50/50 audited, verdicts in chunk reports
  (data/adjudication/adjudicate-20260913T005301/chunk-*.md).

### Verdicts (from chunk reports + demote_queue)

TEXTURE (9 filed, queue ids 1–9): guar jerky, garum, fatback (ordinary
foodstuffs); grandson, librarian, school nurse (unnamed one-scene or
off-page extras); monkeys (one-scene fauna); chandelier (unnamed fixture);
magical contamination (hazard the narrator invents then negates on-page).

VALID (41): includes borderline-kept per the doubt rule: slavery (single
inference), dark elf (crowd phrasing), timid girl, oud. Confirmed-strong
classes: named districts (port, lower quarters), mechanics (magical item
slot — author-confirmed), cultural customs (smiling — two independent
scenes), unnamed-but-active characters (tanned human — drives a stealth
sequence), gear with mechanical effects (hide boots — footstep muffling).

## Phase 4 — report (COMPLETE)

**Shadow-flag precision: 9/50 = 18% texture.** The "~half of flagged are
valid" calibration does NOT hold on a stratified sample — 82% of flagged
candidates are valid referents with corpus-confirmed story weight. The
calibration was non-stratified (early-thread heavy); the stratified pass
revises it decisively downward for texture share. Caveat: the adjudicator
ran with an explicit keep-bias ("borderline = VALID"), which is the right
posture for precision measurement but means 18% is a floor estimate only
if one disputes borderline calls (4 noted).

**Over-admission concentrates in identifiable classes**: ordinary
foodstuffs named by real-world terms, unnamed one-scene/off-page extras,
one-beat background fauna/fixtures. Everything mechanical, institutional,
nominative, or plot-causal survived audit — including classes the t1–40
baseline missed entirely (books/documents: all VALID; taxonomy terms:
VALID).

**Tag vocabulary is undisciplined**: 65 freeform tag values over 239
flagged rows (academy/Anthus/Licae ruins/anthus as "classes"). The tag
priors mechanism assumes a controlled vocabulary; the relaxed prompt
apparently doesn't enforce one. Next prompt revision should pin the tag
list.

**Gloss style**: chunk reports show quote-faithful verdicts; the earlier
style concerns ((q.v.) cross-refs, ALL-CAPS bookkeeping) didn't impede
auditing — the (q.v.) style reads as genuinely wiki-useful. No new style
evidence either way from this pass.

**Cost (telemetry, run adjudicate-20260913T005301)**: 22 calls, 0 errors,
0 retries (attempts 22/22 success — no retry tax this pass), 93 tool
calls, 855k chars in / 21.5k chars out (est ~214k/~5.4k tokens).
Per audited candidate: ~17.1k chars in, ~1.9 tool calls, ~12.3s wall.
Weekly quota delta for the full 50-candidate pass: ~3 points (6% → 9%).
At this rate, auditing ALL 239 flagged candidates costs ~14 weekly points
(linear extrapolation: ~3 points per 50 candidates × 239/50 ≈ 14.3;
linear because per-chunk context is fixed-size — no glossary overview is
loaded). Provider usage fields: none emitted by omp (records all
provider_usage=0, consistent with the Q1 UNKNOWN verdict).

**Verdict on the open hypothesis**: the full run's permissiveness vs the
baseline is MOSTLY a distribution artifact, with a small real texture
tail. 53% flag rate ≠ 53% junk; the true texture share is ~18% of flagged
(≈10% of all 455 entries). Implications: (a) next prompt revision should
target the specific texture classes (ordinary food, unnamed extras,
one-beat scenery) rather than tightening admission broadly — the recall
wins are real and survived audit; (b) the demote_queue holds 9 proposals
for Matt; (c) budget enforcement is NOT a restart concern — the flagged
over-budget batches were a verify-side accounting artifact (see Phase 3),
fixed, with the run compliant under the enforced accounting.

## Evidence (merge bar)

- Phase 1: 202 passed, ruff clean — commit d176dc6.
- Phase 2 (+sampler fix): 221 passed, ruff clean — commits efae62c,
  98370a5.
- Phase 3: pass exit 0; verify exit 0 after fixing the checker's
  block-level accounting bug (regression test added); table diff shows
  only 9 demote_queue inserts.

## Open questions

- Does Matt accept the 9 demotion proposals? (demote_queue, all pending)
- ~~Card-budget violations~~ RESOLVED: checker accounting bug, fixed
  (verify now mirrors select_cards' per-card accounting); the full run was
  budget-compliant all along.
- Borderline-kept 4 (slavery, dark elf, timid girl, oud): Matt spot-check?
- Full-candidate audit (all 239) costs ~14 weekly points (linear from the
  measured 3-per-50) if Matt wants the full precision number — feasible
  under the 50% breaker but not negligible.

