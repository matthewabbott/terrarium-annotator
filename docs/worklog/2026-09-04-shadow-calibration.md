# 2026-09-04 — Shadow-gate calibration report (threads 3–5)

**Verdict up front: NO-GO for the specificity heuristic as written. The gate stays in shadow mode.**

## What ran

- Threads 30392208 / 30436630 / 30459080 (chronological 3–5), kimi-k2.5, 56 batches, ~2h (with two mid-run failures that hardened the client: empty-response and timeout, both now retried per-call and diagnosed in the transcript).
- `verify` exit 0 on the final DB. Quota: 7-day 28→33%, 5h reset mid-run (8→12%).
- 58 entries, 17 shadow-gate deferrals logged, 0 batch failures after hardening.
- Gold set harvested: 806 links, 218 unique entities, 38/38 pages (with anchor labels).

## Gate calibration

The heuristic (defer terms with no uppercase, digit, or non-ASCII):

- **Catches the real junk**: `strange language`, `old fort`, `sea silk` all flagged. ✓
- **Defers nearly every known-good lowercase term**: `vys`, `aghtaki`, `guar`, `kapros`, `oud`, `sikarida`, `sihirbaz`, `hayat`, `well flower` — all flagged. ✗✗

Banished Quest's important terms ARE lowercase foreign/coined words, so "lowercase = generic" is exactly wrong for this corpus. Precision on the 17 shadow-logged candidates: ~half are genuinely generic (`the armor`, `the executioner`, `bipedal lizard`, `magical experiments`), half are real entries (`guar`, `kapros`, `sikarida`, `sihirbaz`, `hayat`, `metamagic`, `elemental affinity`). **A lexical gate cannot work here.** Next candidate design: corpus-frequency-based (defer only terms that never recur) or critic-driven (defer verdict from the researcher, not a heuristic) — see `docs/design/critic-salience-epistemics.md` §4; the shadow log + deferred_candidate table stay in place for that.

## Recall vs gold set (threads 3–5 pages)

Union of smoke (t1–2) + shadow (t3–5) DBs: 98 entries vs 51 unique gold entities → **17/51 (33%) covered**.

Caveats on the miss list:
- **Naming mismatches** (we have the entity under a different surface): `centurion-armor` (ours: "Rhynian Centurion Armor"), `eshnuk-book-of-rituals` vs `eshnuk` (ours), several `magic:` subdomains (`earth`, `fire`, `water`, `metal`, `nature`) — likely covered as "Earth magic" style entries or not at all; alias/reread-linking work addresses this.
- **Real holes**: `mik` (the protagonist — no entry!), `insanity`, `runes`, `silver-tongued`, `ring-of-sanity`, `khmedi`, the library book titles (`blast-scroll`, `leaping-scroll`, …), `oud`.

Takeaways for the annotator prompt: the protagonist needs an entry; book/item titles are being skipped; single-word magic domains are being folded or missed.

## Smoke-run junk check (bonus)

The two junk classes from the L3 smoke report: `strange language`/`old fort` (gate catches them, but so would any rule) and the `Aleamond`/`Kingdom of Aleamond` duplicate pair (shadow run produced no duplicate pairs; merge/rename ops now exist for the critic queue).

## For Matt

- The wiring goal is complete; the admission-policy question is now evidence-backed: **lexical heuristics fail on this corpus; deferral must be recurrence- or critic-driven.**
- Entries to eyeball: `data/annotator-shadow.db` (58 entries, threads 3–5) via `terrarium-annotator chat --corpus-db banished.db --annotator-db data/annotator-shadow.db`.

## Post-goal addendum (advisory review)

- Harvest parser gained mocked unit tests (labels, sub-namespaces, anomaly preservation, partial failure) and a namespace-required filter; gold-set.json verified unpolluted (0 namespace-less rows) without re-harvest.
- Schema-versioning note: `data/annotator.db` (smoke, threads 1–2) predates the `entry_source.mode` column; `data/annotator-shadow.db` (threads 3–5) was created after it. No DB contains legacy rows under the mode default — writes supply mode explicitly, and old-schema DBs fail loudly (unknown column) rather than silently defaulting. If a future pass ever reuses an old DB, expect that loud failure; the fix is a fresh DB per schema epoch (current policy).
