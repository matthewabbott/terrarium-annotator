# Aspirations — short/medium-term plans not yet scheduled

*The place for "note it down, don't build it yet." Ordered roughly near → far. See `docs/plan/v2-foundation.md` for the active build plan and `docs/design/critic-salience-epistemics.md` for the quality architecture these feed.*

## Evaluating the t1–40 run (when it completes)

- **Gold-coverage hard criterion** (Matt): the glossary should have an entry for *every* backlink in every published thread wiki page (218 unique entities across pages 3–40). Extra entries are fine — the bar is coverage, not exact match. Hardest class: books the protagonist reads — they look like texture but are inventory items with mechanical significance (reading grows the vys pool / teaches techniques). Consider a `book`/`document` tag prior boost during researcher passes.

  Two counts, not one: (i) coverage of *all* published-page links, and (ii) coverage of links *knowable by that thread's cutoff* — published pages contain spoiler/meta links a first-read pass cannot know, so (ii) is the fair bar and (i) is the stretch bar. Normalize published slugs through our alias table before scoring (their `centurion-armor` = our "Rhynian Centurion Armor").
- **Soft criterion**: how well generated entries/pages *match* the published wiki pages (fidelity of gloss vs published summary), beyond pure coverage.
- A/B entry quality: same threads, old prompt vs revised prompt, Matt judges the diffs.

## Prompt/skillset revision for the NEXT pass (not mid-run)

- Annotator prompt gains the revised admission criterion (OR-form: unresolvable/confusing OR wiki-worthy OR colloquial-with-divergent-in-setting-meaning, the `husk` class). See design §4.
- Consider adding explicit protagonist/main-party entries early (`mik` was a recall hole in the shadow run).

## Researcher / pruner / critic tier (design exists)

- Build the researcher pass (graveyard reclamation, reread linking, retitles) with the admission criterion and cull semantics in mind (design §3/§4).
- Critic: adversarial claim-disproof, debates rehydrated author on contested entries; gates researcher output before wiki-facing confirmation.

## Game-mechanics documentation (narrow-focus passes)

- `vys`-the-mechanic vs `vys`-the-concept may deserve separate pages; the wiki already has mechanics backlinks. Best done as a **glossary-enhanced readthrough**: an agent that already has the glossary reads with the narrow aim of documenting game mechanics and their evolution. Generalizes: narrow-focus glossary passes (factions, locations, techniques) with an existing glossary in context.

## Whole-thread reading

- Today the runner reads in 5-post batches (v1 legacy; local models couldn't hold a thread). kimi-k2.5's 262k context can hold most threads whole (~50–100 story posts ≈ 20–60k tokens). Worth an experiment: read the whole thread, then emit entries + gists — better "what matters" judgment per thread. Decouple from the merge tree (keep per-batch gists for the log regardless). Don't change mid-run; evaluate after t1–40.

## Far horizon

- Wiki export to steelbea.me (thread pages + entity pages; format pinned in `docs/design/wiki-format.md` + `thread-pages.md`).
- Semantic search / embeddings over the finished lorebook.
- IRC community-review bot: float contentious entries/revisions to the BQ community channel for human verdicts.
- Multi-corpus (building codes etc.).
