# Critic, Epistemics, Salience, Deferral — v2 quality architecture

*2026-09-03, from Matt's review of the L3 smoke report. Companion to `v2-architecture.md`; when implemented, §3/§6 there should point here.*

The smoke run proved the wiring and the quote gate. Matt's read of the 40 entries surfaced four quality problems. They interlock into one architecture: **deferral is the admission policy, epistemic modes are the data model, salience is the serving policy, the critic is the enforcement arm.**

---

## 1. The critic agent (enforcement)

A batch agent that runs after a thread (or N threads), entry-centric: for each entry touched, fetch entry + revisions + evidence + cited posts, then verdict:

| Verdict | Action |
|---------|--------|
| SUPPORTED | nothing |
| OVERCONFIDENT | trim/rewrite gloss, or downgrade epistemic mode (§2) |
| UNSUPPORTED | retract (append a retraction revision — never delete) |
| DUPLICATE | human merge queue (never auto-merge — fiction has real name-sharing) |
| BAD TITLE | retitle queue (`old fort` → `Old Silver Mint`) |
| TOO GENERIC | demote to deferred (§4) or drop |

Two capabilities beyond v1's curator:

- **Author interrogation.** Per-batch transcripts + append-only stores already make the author's context reconstructible (architecture §4 rehydration). The critic can replay the authoring context and *ask* "why did you write this gloss?" — cheap now, built for exactly this.
- **Future knowledge.** The critic runs later than the author and can `recall_story` / fetch posts from threads the author hadn't read. Canon drift checks ("did this get contradicted or retconned later?") become possible.

On literal git for entries: our `revision` table + `pass_id` already *is* the VCS (append-only, blame, per-edit provenance). A markdown-per-entry git mirror is a fine export artifact for humans but adds nothing structurally. Not needed.

## 2. Epistemic modes (data model)

Fiction glossaries mix directly-narrated fact, character rumor, and reader inference — and the story *deliberately misleads*. The fix is not more rigor at write time; it's **tracking what kind of knowledge each claim is**, so later revelation can retract cleanly.

- Add `mode` to `entry_source` (and encourage per-claim marking in glosses): `narrated` (the text states it), `claimed` (a character/rumor says it), `inferred` (extrapolation).
- Retraction is a first-class revision type: `superseded` with a pointer to the correcting revision + evidence. The wiki page can then render "previously believed X (thread 12), actually Y (thread 40)" — the in-character-artifact property Matt wants, with history intact.
- Prompt-side: the annotator prompt instructs marking hearsay as hearsay. Cheap, do immediately.
- The live wiki already models this (Sadik's Abilities section separates demonstrated from speculated) — this formalizes a house convention into schema.

The Elaudian case is exactly this: "known for elaborate constructions" was an inference from one merchant anecdote; mode `inferred` + critic verdict OVERCONFIDENT would have rewritten it at thread 5.

## 3. Salience (serving policy)

Terms differ by frequency and importance: Vys (every thread), El-Amin/Elaudian (recur later, matter then), Well Flower (one-off texture). Serving should respect that without *losing* anything:

- **Salience score** per entry, computed from data we already store: `log(1 + mention_count)` (from `entry_source`) × recency decay (last cited batch vs current) × tag prior (mechanic/character > item/texture).
- **Injection**: triggered-by-current-batch always injects (a dormant term reappearing must resurface its card — this is the non-negotiable rule); under budget pressure, drop by lowest salience (replacing today's recency-then-shortest).
- **Cold storage ≠ deletion**: single-mention, never-updated, stale-N-threads entries become `cold` — excluded from recursion and from search defaults, but still exact-trigger injectable and still in the wiki export.
- **Search/RAG**: FTS rank × salience; later, if embeddings arrive for the audit side, salience weights those too.

## 4. Deferral (admission policy)

First-read vs reread asymmetry is the core insight: `strange language` is unlinkable on first read (the entry can never be found again), but on reread it's obviously a known entry. Rules:

- **Specificity gate at propose time** (heuristic, critic-audited): a term that is a bare common-noun descriptor with no proper/coined token and no existing alias goes to a **`deferred_candidate`** table (term + quote + post) instead of `entry`.
- **Promotion**: if a deferred surface form recurs in a later batch (cheap trigger scan), it promotes to a real entry — the second sighting is evidence of referential stability.
- **Reread linking**: a second pass over the corpus with the complete glossary turns known descriptors into *aliases* of canonical entries (entity-linking task), not new entries.
- **Retitle**: critic-driven; needs a `rename_entry` op (update term, auto-register the old title as alias, append revision noting the rename).
- **Sub-entry split rule** (Aghtaki brands): a nested term earns its own entry when it appears in a batch *without* its parent term, or recurs under a second referent (Scourge on a new character). Both are measurable from entry_source/thread data — no judgment call needed at write time.

## Implementation order sketch

1. Prompt-side epistemic marking + `mode` column (small schema add; design doc update same commit)
2. Salience scoring in injection priority + search
3. `deferred_candidate` table + promote-on-recurrence
4. Critic agent (batch CLI: `annotator critique --thread N`) with verdict taxonomy; rehydrate-interrogate as v2 of the critic
5. `rename_entry`, retraction revision type

All but the critic are model-free and L0-testable; the critic is the only LLM part and can reuse the L1 scripted harness for wiring tests.

## Prior art map (details: `research-critic-salience.md`)
- §1 critic: **TMS** (Doyle 1979, dspace.mit.edu/handle/1721.1/5733) / **ATMS** (de Kleer 1986, dekleer.org/Publications/An%20Assumption-Based%20TMS.pdf) — entries' evidence quotes *are* justifications; canon drift withdraws support → retraction with dependency tracking. **CoVe-factored verification** (arxiv.org/abs/2309.11495 — critic answers "is this claim supported" *without* seeing the draft rationale, avoiding self-agreement). **FActScore** (arxiv.org/abs/2305.14251) / **RAGAS** (arxiv.org/abs/2309.15217) claim decomposition. **Self-Refine's documented self-bias** (arxiv.org/abs/2303.17651) is why the critic gets the author's transcript + future story state rather than re-judging blind.
- §2 epistemics: **Wikidata rank/qualifier/reference** (wikidata.org/wiki/Help:Ranking; P248 "stated in"; P2241 deprecation reason) — deprecated-not-deleted with machine-readable retraction reasons. **FactBank** (Saurí & Pustejovsky 2009, link.springer.com/article/10.1007/s10579-009-9089-9) per-source factuality — narrator vs. character vs. inference is literally its annotation scheme. **AGM** (plato.stanford.edu/entries/logic-belief-revision/) epistemic entrenchment — rumor retracts before narrated fact.
- §3 salience: **Generative Agents** (arxiv.org/abs/2304.03442) recency × importance × relevance. **Mem0 floored decay** (mem0.ai/blog/introducing-memory-decay-in-mem0 — 0.3× floor, demote-not-delete, dormant terms resurface). **SWAT** (arxiv.org/pdf/1804.03580) co-occurrence-graph centrality — Vys vs Well Flower falls out of graph position. **C-value/contrastive** scoring (aclanthology.org/L10-1379/) for story-distinctiveness.
- §4 deferral: **NIL-linking with clustering** ("Learn to Not Link", arxiv.org/abs/2305.15725; TAC KBP NIL-clustering) — "don't create an entry yet" is a studied problem with the exact Missing-Entity vs Non-Entity-Phrase distinction we need. **Angell et al. cluster-linking** (aclanthology.org/2021.naacl-main.205/) — second co-referring mention crosses the threshold (the brand-split rule). **CESI canonicalization** (arxiv.org/abs/1902.00172) for retitle/merge. **Wikidata "structural need"** (wikidata.org/wiki/Wikidata:Notability) for when a sub-entry is earned.
