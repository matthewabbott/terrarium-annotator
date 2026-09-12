# Aspirations — short/medium-term plans not yet scheduled

*The place for "note it down, don't build it yet." Ordered roughly near → far. See `docs/plan/v2-foundation.md` for the active build plan and `docs/design/critic-salience-epistemics.md` for the quality architecture these feed.*

## Token-usage observability (Matt, 2026-09-12)

The ladder gets a cost column from day one: not just "which prompt/model is best" but "which way of working costs what". Components:

- **Usage capture**: record provider `usage` (prompt/completion/cached tokens when reported) per call in transcript/run_meta; attribute per call-type (annotation vs merge-settle vs chat vs researcher) and per context component (cards vs digest vs scene vs system). Response `usage` fields are ground truth where provided; chars/4 heuristic otherwise. RPC `agent_end` may carry telemetry (check rpc.md fields).
- **Headline metric**: cost per covered gold entity per variant (quality and cost in one number). Secondary: entries/1k posts × cost; tool calls per batch; context growth slope over a run.
- **Efficiency variants to A/B** (first-class ladder candidates):
  1. **Prefix caching**: reuse ONE RPC process across a batch's tool rounds (provider-side prefix cache hits on unchanged system+cards); keep stateless across batches. Likely the biggest single win — cost structure today is turns × cold context.
  2. **Batch writes**: a `propose_entries` array tool (one call → N gated entries) to collapse N turns into 1.
  3. **Whole-thread reading** (already on this list): amortizes system+cards per thread instead of per 5 posts.
  4. **Bulk model for reading** (Deepseek Flash), strong model for critic/researcher.
  5. **Context trims**: gloss length caps, top-k cards by salience, digest budget tuning, stop-early on repeated gate rejections.
  6. Web research on RAG/prompt-caching efficiency strategies, then ladder-test the promising ones.


## NEXT: usage telemetry (gated task for next session)

**Priority #1 for the next session. Acceptance gate — either met, or the session stops and reports the blocker.**

- Provider-neutral `InstrumentedClient` wrapper recording per call: prompt/completion char counts, tool-call count, context-component sizes (cards/digest/scene/system — computed at assembly), and provider `usage` fields **only when reported** (null otherwise; never invented; Kimi RPC exposure of `usage` is UNVERIFIED until checked).
- Every run/researcher session logs metrics to a durable per-run file under `data/recordings/usage/`.
- L0 tests: wrapper over ScriptedModel asserts record contents and null-provider behavior; merge bar (`pytest`, `ruff`) green.
- A `summarize()` (or CLI) producing per-run and per-call-type aggregates (calls, chars in/out, tool calls).
- Zero behavior change: pure wrapper; annotator and researcher constructible with or without it.
- **Failed/retry calls are first-class records**: every attempt (success or exception) logged with attempt number, status, and error type — the retry tax is a major cost component, not an afterthought.
- **Stable identifiers**: each record carries a run ID and call-type label (annotation / merge-settle / chat / researcher) so per-call-type aggregation is actually possible. Context-component sizes are passed by the prompt assembler or marked `unknown` — no silent gaps.

## Evaluating the t1–40 run (when it completes)

- **Gold-coverage hard criterion** (Matt): the glossary should have an entry for *every* backlink in every published thread wiki page (218 unique entities across pages 3–40). Extra entries are fine — the bar is coverage, not exact match. Hardest class: books the protagonist reads — they look like texture but are inventory items with mechanical significance (reading grows the vys pool / teaches techniques). Consider a `book`/`document` tag prior boost during researcher passes.

  Two counts, not one: (i) coverage of *all* published-page links, and (ii) coverage of links *knowable by that thread's cutoff* — published pages contain spoiler/meta links a first-read pass cannot know, so (ii) is the fair bar and (i) is the stretch bar. Normalize published slugs through our alias table before scoring (their `centurion-armor` = our "Rhynian Centurion Armor").
- **Soft criterion**: how well generated entries/pages *match* the published wiki pages (fidelity of gloss vs published summary), beyond pure coverage.
- A/B entry quality: same threads, old prompt vs revised prompt, Matt judges the diffs.


## From Matt's t1–40 analysis review (2026-09-05)

- **The gold set is evidence, not canon**: `game:siege` was a human mistake on the wiki. Rule: published backlinks are evaluation *evidence*, never ground truth. Known-bad links are kept in the set but labeled as gold errors and excluded from hard recall targets (we do not silently treat the wiki as authority; wiki-side corrections land when we generate pages).
- **Best-fit naming is post-hoc by necessity**: characters introduced under aliases (Burnout → Skull Kid) or description-only names (papa-dracolich = "the dragon") can't be named right on first read. The researcher tier owns best-fit naming + alias consolidation after a pass.
- **Ledger pages**: consolidate one-off-but-worth-recording details into pages like "Ledger of notable spirits" / "minor characters" / "minor locations" (Diovis, Hodei, Maximilien-class entries) — wiki structure decision, resolved at export time. Shahzada ≠ Shahzadi (prince vs princess titles — explicitly NOT a merge).
- **Q&A content**: our mechanics entries from author Q&A posts are good adds; the *wiki's* deficiency (Q&A content never made it into magic/trivia sections). Thread/entity pages should have a Q&A section at export.
- **Unreliable narration**: Mikhael is prejudiced; mechanics knowledge is classical-physics-then-relativity. Entries should document conflicting accounts with in-story provenance of each (epistemic modes + per-source attribution, §2) rather than flattening to one story.
- **Specialist annotators**: mechanics-specialist pass (attributes/skills like silver-tongued, inventory, companions, abilities, books), magic-taxonomy pass, objects pass — narrow-focus glossary-enhanced readthroughs after the main pass.

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
