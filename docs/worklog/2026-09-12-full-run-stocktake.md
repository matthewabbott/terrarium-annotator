# 2026-09-12 — Full-run stock-taking at quota stop (threads 1–25)

The full-quest run stopped at the 3-failure budget on 2026-09-07 05:17 (failure #3 = rate limit; 7-day quota hit 100%). Nothing is running. Checkpoint preserved: `data/annotator-full.db` at thread 31411898:4 (chronological thread 25/278).

## Position

- **277 batch gists, 455 entries, 239 shadow-flagged deferrals**, covering threads 1–24 complete + thread 25 in progress.
- Failure diagnostics: 1 (`EmptyResponseError`; supervisor resume worked as designed).

## Density: full-run vs t1–40 baseline (per-thread entries)

Full-run produces **2.5–5× more entries than the baseline in 15 of 25 threads** (e.g. thread 2: 48 vs 15; thread 5: 76 vs 15; thread 20: 13 vs 8 — roughly the exception). The shadow gate flags **53% (239/455)** of full-run entries as generic-suspect, vs **13% (23/177)** in the baseline.

## Quality reading (sampled entries)

**The relaxed prompt hit its design targets** — the exact classes the baseline missed:
- **Books are being captured now**: `book of rituals`, `priest's private journal`, `scroll`, `meditation instructions` (all thread 2 — the class the baseline whiffed entirely).
- **Taxonomy effects**: `Ranged` (with "ACQUISITION CORRECTED BY THE AUTHOR" note), `Leech`, `Manipulator`, `Weapon Affinity`, `Paralysis`, `Poison`.
- **Core setting**: Devanagari brands, `Aghtaki`, `Marsala Ve`, `Elaudian Steel`, `archeota` (Fulvia's housing), `gleaming automaton`, `Rhynian letters`, `Lord Spiros`, `Dragon Staff`.

**But it over-admits texture** — the junk tail is real and large: `cobblestone roads`, `hide boots`, `crowbar`, `cave`, `skin`, `patrols`, `strange dreams`, `a bundle of something`, `my friend`, `Moderately Wealthy`, `drinking animal's blood`, `the two men`, `tanned human`, `meditation instructions` (borderline — a book, but generic title).

**Gloss style notes**: the `(q.v.)` cross-reference style the model adopted is genuinely wiki-useful; the ALL-CAPS bookkeeping ("TWO REGIMES NOW ATTESTED: (1) FRAGMENTS", "ACQUISITION CORRECTED BY THE AUTHOR") is the full-restate rule producing ledger noise — probably wants a style instruction next revision.

## Verdict — corrected framing (post-review)

Not a disaster, and NOT a reason to revert: the prompt fixed its target classes. The 53% shadow-flag rate is a **distribution-shift signal, not a quality metric** — the shadow heuristic is a lexical would-defer detector, and our own calibration found ~half of flagged terms valid. What we can say from evidence:

- The relaxed prompt changed admission behavior materially (53% flagged vs 13% baseline; 2.5–5× density in 15/25 threads). That's a behavior fact, not a quality verdict.
- The flagged set is a **candidate review set, not a junk queue**: the stratified sample shows both (`inn keeper`, `a bundle of something`, `empty house`, `magical experiments` — texture) and (`gleaming automaton`, `secondary hearts`, `complementary elements`, `nature mage`, `blacksmith`, `port`, `tome` — clearly valid).
- The unflagged stratified sample is mostly solid (`+2 bonus`, `Battle at Eshnuk`, `Confuse`, `Manipulator`, `Shockwave Sword`, `Hazmat function`, `Research area`) with minor oddities (`PROLUO`, `Tiny little robots`).
- The earlier sample readings in this report were non-stratified (first-30 and random-20) — usable for illustration, not for rates.

**So: permissiveness vs the baseline is a hypothesis to adjudicate, not a verdict.** The correct workload is adjudication of the flagged candidates (researcher review pass, quote-audited), not bulk cull-demotion. Do NOT prune all 239 on the flag alone.

## Recommended next steps (Matt's call)
1. **Bounded, instrumented adjudication pass** — NOT a bulk cleanup, and NOT yet an economics verdict. Sequence: (a) add usage capture first (record provider `usage` fields per call — the observability aspiration's minimal slice); (b) the researcher quote-audits a stratified subset (~40–60 of the 239 flagged candidates) against source evidence, demoting only clear texture — this is quality adjudication first: shadow-flag precision per class, and the critic's first real workload; (c) the captured usage yields a *bounded baseline* cost measurement for adjudication. Whether post-hoc pruning is economically superior, and whether token economics is the binding constraint at all, are open questions for that data — not premises.
2. **Restart the run only on Matt's explicit go-ahead** (the Sep 11 quota reset has passed, but Matt paused the run Sep 7 — no restart without authorization). When restarted: style tweak (discourage bookkeeping caps and pure-scenery entries), resume from checkpoint 31411898:4.
3. Post-run: researcher alias pass (same as t1–40) then coverage analysis.


## Cost autopsy (2026-09-12): cost-increase mechanisms (per-batch, measured) — NOT a measured quota multiplier

**Measured per-batch structure** (transcript data, not billing): tool calls 2.7 → 8.5; prompt chars 34.5k → 99.5k; output chars 1.1k → 2.3k; entries 0.43 → 1.64 per batch. Total prompt volume 14.4M vs 27.6M chars for 2/3 the batches.

**Attribution caveats**: stored character volume is NOT provider-billed tokens (unknown tokenizer + possible cache pricing); the 7-day quota delta (33%→100%) also includes the researcher pass, chat probes, retries, and Matt's interactive usage — the ~10–25× quota multiplier is an UNVERIFIED proxy and must not be quoted as measured. The structural per-batch multipliers (~3× turns, ~3× prompt size, 2× output) ARE measured and suffice for the design lesson.

**Mechanisms (structural, confirmed)**: (1) liberal admission → more proposals → more tool calls → more follow-up turns; each turn re-sends the full context cold — `OmpRpcClient` spawns one fresh process per call, so no prefix caching (mechanism valid; its share of billed cost is hypothesis). (2) Growing glossary → bigger injected card blocks every subsequent batch. (3) Full-restate rule → longer outputs and bigger cards downstream. Fixes to test in the ladder: per-batch proposal cap, dedupe-before-propose, shorter glosses, tool-round budget.

**Correction (advisory)**: 8.5 tool calls/batch does NOT establish the 8-round turn cap was hit — turns were 3.5/batch, so most batches ran under it. Whether the cap was ever reached is a hypothesis to instrument, not a measured fact.

## 2026-09-12 — discussion: prompt laddering + model onboarding

Matt floated (pre-handover): A/B prompt variants on a 5-thread slice with criteria scoring; onboarding Deepseek v4.1 Flash via the same scorecard; model provenance in blame + rehydrate-with-generating-model; usage circuit-breaking (80% weekly or $x); prompts separated by agent and by metrics. Design note: docs/design/prompt-laddering.md. Key planning fact: full-quest run covered 25/278 threads on ONE weekly Kimi quota (~10–11 windows at current pace).

