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

## Verdict

Not a disaster, and NOT a reason to revert: the prompt fixed its target classes and the shadow gate caught the over-admission in-flight (that's what it's for). The current pass is ~half-good capture, ~half texture to prune. **Pruning is now the priority workload**: 239 flagged candidates await the researcher prune pass; the gate's whole purpose is that this is reversible and evidence-backed.

## Recommended next steps (Matt's call)

1. **Pruning researcher pass** on `annotator-full.db` (239 flagged + scan of the unflagged for stragglers), cull-demote to graveyard.
2. **Restart the run after Sep 11 quota reset** with a style tweak to the prompt (discourage bookkeeping caps and pure-scenery entries) — resume from checkpoint 31411898:4.
3. Post-run: researcher alias pass (same as t1–40) then coverage analysis.
