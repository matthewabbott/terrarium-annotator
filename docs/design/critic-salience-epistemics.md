# Critic, Epistemics, Salience, Deferral — v2 quality architecture

*2026-09-03, from Matt's review of the L3 smoke report. Companion to `v2-architecture.md`; when implemented, §3/§6 there should point here.*

The smoke run proved the wiring and the quote gate. Matt's read of the 40 entries surfaced four quality problems. They interlock into one architecture: **deferral is the admission policy, epistemic modes are the data model, salience is the serving policy, the critic is the enforcement arm.**

---

## 1. The critic agent (enforcement)

The critic is a **fresh-context agent prompted to be the author's adversary**: its goal is to *disprove* an entry's claims — find where the gloss outruns its evidence quotes — and convince the author. It runs batched, after a thread (or N threads), entry-centric.

**Debate protocol** (Matt's refinement):

1. Critic fetches entry + revisions + evidence + cited posts, forms challenges (claim-by-claim, CoVe-factored: it derives support from the quotes itself, not from the author's rationale).
2. The **rehydrated author** (transcript + reconstructed context, architecture §4) answers each challenge: defends with evidence or concedes.
3. Outcome: author concedes → the *author* writes the corrected revision through the normal quote-gated path (attributed to the critique's `pass_id`). Critic remains unpersuaded, author holds → **human queue**. Neither agent free-edits outside the gated write path; the critic never writes entries directly (its drafts only enter via the author's conceded revision — still fully source-cited).

Verdict taxonomy applied per entry after the debate:

| Verdict | Action |
|---------|--------|
| SUPPORTED | nothing |
| OVERCONFIDENT | author trims/rewrites gloss, or downgrades epistemic mode (§2) |
| UNSUPPORTED | retraction revision (§5) — never delete |
| DUPLICATE | human merge queue (never auto-merge — fiction has real name-sharing) |
| BAD TITLE | retitle via `rename_entry` (`old fort` → `Old Silver Mint`), old title auto-aliased |
| TOO GENERIC | demote to deferred (§4) |

The critic's structural advantages over v1's curator: it runs **later than the author**, so it can check the claim against story state the author hadn't read (canon drift, retcons) via `recall_story`/post fetches — and it can interrogate the author's reasoning rather than re-judging blind (self-critique's known failure mode).

## 2. Epistemic modes (data model)

Fiction glossaries mix directly-narrated fact, character rumor, and reader inference — and the story *deliberately misleads*. The fix is not more rigor at write time; it's **tracking what kind of knowledge each claim is**, so later revelation can retract cleanly.

- Add `mode` to `entry_source` (and encourage per-claim marking in glosses): `narrated` (the text states it), `claimed` (a character/rumor says it), `inferred` (extrapolation).
- The wiki page renders the in-character-artifact property Matt wants: "previously believed X (thread 12), actually Y (thread 40)" — history intact, present state clean.
- Prompt-side: the annotator prompt instructs marking hearsay as hearsay. Cheap, do immediately.
- The live wiki already models this (Sadik's Abilities section separates demonstrated from speculated) — this formalizes a house convention into schema.

The Elaudian case is exactly this: "known for elaborate constructions" was an inference from one merchant anecdote; mode `inferred` + an adversarial challenge would have rewritten it by thread 5.

## 3. Salience and the graveyard (serving policy)

Terms differ by frequency and importance: Vys (every thread), El-Amin/Elaudian (recur later, matter then), Well Flower (one-off texture). Serving should respect that without *losing* anything:

- **Salience score** per entry, computed from data we already store: `log(1 + mention_count)` (from `entry_source`) × recency decay (last cited batch vs current) × tag prior (mechanic/character > item/texture).
- **Injection**: triggered-by-current-batch always injects (a dormant term reappearing must resurface its card — non-negotiable); under budget pressure, drop by lowest salience (replacing today's recency-then-shortest).
- **The graveyard** (Matt): cold storage is a visible place, not a void. `cold` (single-mention, never-updated, stale-N-threads) and retracted entries live there — excluded from recursion and search defaults, but still exact-trigger injectable and still in the wiki export.
- **Reclamation pass**: a periodic agent sweep of the graveyard that tries to *promote* dormant entries (found a second mention → warm again, linkable evidence upgraded) or fold them into canonical entries via the dedup/merge queue. Same tooling as the critic; the graveyard is its inbox.
- **Search/RAG**: FTS rank × salience; later, if embeddings arrive for the audit side, salience weights those too.

## 4. Deferral (admission policy)

First-read vs reread asymmetry is the core insight: `strange language` is unlinkable on first read (the entry can never be found again), but on reread it's obviously a known entry. Rules:

- **Specificity gate at propose time** (heuristic, critic-audited): a term that is a bare common-noun descriptor with no proper/coined token and no existing alias goes to a **`deferred_candidate`** table (term + quote + post) instead of `entry`.
- **Promotion**: if a deferred surface form recurs in a later batch (cheap trigger scan), it promotes to a real entry — the second sighting is evidence of referential stability.
- **Reread linking**: a second pass over the corpus with the complete glossary turns known descriptors into *aliases* of canonical entries (entity-linking task), not new entries.
- **Sub-entry split rule** (Aghtaki brands): a nested term earns its own entry when it appears in a batch *without* its parent term, or recurs under a second referent (Scourge on a new character). Both are measurable from entry_source/thread data — no judgment call needed at write time.

## 5. History: diffs, reverts, and the git question

Matt asked for proper git-like history: diffs over time, restore, review past versions. Position: **the revision table is the spine; git semantics on top; literal git as an export artifact.**

We already store full gloss text per revision, append-only, with provenance. That gives, with small additions:

- **Diffs**: computed on demand between revisions (difflib over gloss + metadata delta: mode changes, alias adds, retitles). `fetch_entry` gains a `diff` view.
- **Revert**: restoring an old version = *append a new revision* whose gloss is the old text, with a `reverts: <revision_id>` marker — git-revert semantics, append-only preserved, blame intact.
- **Review past versions**: revisions are already queryable per entry; the wiki page renders its history block from them.
- **When tier-2 page bodies land**, they must be revisioned the same way (today only the gloss is versioned — page bodies don't exist yet; don't build pages without revisioning them from day one).
- **Git-mirror export** (optional, later): materialize entries as markdown files in a real git repo for human log/blame UX. Read-only mirror; SQLite stays canonical (queryable, transactional, and the verifier's target).

What we should NOT do: use git as the storage engine. The verifier, salience scoring, and deferral scans all need relational queries over history; git can't answer them without a database in front anyway.

## Implementation order sketch

1. Prompt-side epistemic marking + `mode` column (small schema add; design doc update same commit)
2. Salience scoring in injection priority + search; graveyard status (`cold`) + resurfacing rule
3. `deferred_candidate` table + promote-on-recurrence
4. Revision diffs + revert-as-revision + `rename_entry`
5. Critic debate protocol (batch CLI: `annotator critique --thread N`) — wiring testable via the L1 scripted harness; reclamation pass reuses it

All but the critic/reclamation are model-free and L0-testable.

## Prior art map (details: `research-critic-salience.md`)

- §1 critic: **TMS** (Doyle 1979, dspace.mit.edu/handle/1721.1/5733) / **ATMS** (de Kleer 1986, dekleer.org/Publications/An%20Assumption-Based%20TMS.pdf) — entries' evidence quotes *are* justifications; canon drift withdraws support → retraction with dependency tracking. **CoVe-factored verification** (arxiv.org/abs/2309.11495 — critic answers "is this claim supported" *without* seeing the draft rationale, avoiding self-agreement). **FActScore** (arxiv.org/abs/2305.14251) / **RAGAS** (arxiv.org/abs/2309.15217) claim decomposition. **Debate** (arxiv.org/abs/1805.00899) is the critic↔author protocol's ancestor. Self-critique loops are known to reinforce their own draft's assumptions (discussed around Self-Refine, arxiv.org/abs/2303.17651, and the LLM-judge literature generally) — by analogy, our critic gets the author's transcript + future story state rather than re-judging with the same information.
- §2 epistemics: **Wikidata rank/qualifier/reference** (wikidata.org/wiki/Help:Ranking; P248 "stated in"; P2241 deprecation reason) — deprecated-not-deleted with machine-readable retraction reasons. **FactBank** (Saurí & Pustejovsky 2009, link.springer.com/article/10.1007/s10579-009-9089-9) annotates event factuality *relative to nested sources* (author says X believes Y reports Z) with modality × polarity — our narrator/character/inference modes are the same source-relative structure applied to fiction. **AGM** (plato.stanford.edu/entries/logic-belief-revision/) epistemic entrenchment — rumor retracts before narrated fact.
- §3 salience: **Generative Agents** (arxiv.org/abs/2304.03442) recency × importance × relevance. **Mem0 floored decay** (mem0.ai/blog/introducing-memory-decay-in-mem0 — 0.3× floor, demote-not-delete, dormant terms resurface). **SWAT** (arxiv.org/pdf/1804.03580) co-occurrence-graph centrality — Vys vs Well Flower falls out of graph position. **C-value/contrastive** scoring (aclanthology.org/L10-1379/) for story-distinctiveness.
- §4 deferral: **NIL-linking with clustering** ("Learn to Not Link", arxiv.org/abs/2305.15725; TAC KBP NIL-clustering) — "don't create an entry yet" is a studied problem with the exact Missing-Entity vs Non-Entity-Phrase distinction we need. **Angell et al. cluster-linking** (aclanthology.org/2021.naacl-main.205/) — second co-referring mention crosses the threshold (the brand-split rule). **CESI canonicalization** (arxiv.org/abs/1902.00172) for retitle/merge. **Wikidata "structural need"** (wikidata.org/wiki/Wikidata:Notability) for when a sub-entry is earned.
