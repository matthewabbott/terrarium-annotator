# Quality agents, Epistemics, Salience, Deferral — v2 quality architecture

*2026-09-03, from Matt's review of the L3 smoke report. Companion to `v2-architecture.md`; when implemented, §3/§6 there should point here.*

The smoke run proved the wiring and the quote gate. Matt's read of the 40 entries surfaced four quality problems. They interlock into one architecture: **deferral is the admission policy, epistemic modes are the data model, salience is the serving policy, and three quality agents — advisor, researcher, critic — are the enforcement arm.**

---

## 1. The quality agents (enforcement)

Three roles, in increasing cost and authority (Matt's refinement, from his critic-vs-researcher pattern in production):

### Advisor (annotator-time, cheap)

Lightweight steering *while the annotator reads*: deterministic guards (the quote gate already), plus prompt-level discipline (mark hearsay, prefer specific titles) and optionally a cheap model review of each proposed write. Explicitly **not** a debate: a context-less reviewer can't judge story-grounded claims, and per-write debate would throttle the annotator. This is steering, not adjudication.

### Researcher (top-down, medium)

An agent that starts from the *glossary*, not the story: picks an entry, investigates the corpus for evidence (fetch posts, recall story log), and **may revise entries** through the normal quote-gated write path. It doesn't need author rehydration — it researches sources directly, which is what makes it cheaper than debate.

The researcher owns the batch jobs: the **reclamation pass** (graveyard sweep — promote dormants that acquired a second mention, or fold them into canonical entries via the merge queue), **reread linking** (turn deferred descriptors into aliases of canonical entries), and retitle execution (`rename_entry`).

### Critic (adversary, expensive, rare)

A fresh-context agent whose only job is to **disprove** claims — find where a gloss outruns its evidence. It never revises entries. Its verdicts route to a researcher (fix it) or the human queue (genuine dispute). For high-value or contested entries, it can challenge the *rehydrated author* (transcript + reconstructed context, architecture §4) in a debate — the author defends with evidence or concedes and writes the revision itself.

**Critic × researcher at runtime** (Matt's production pattern, adapted): before researcher revisions become user-facing (wiki export / confirmed status), the critic reviews them. The annotator is exempt from this — advisor steering only, since a critic without research ability lacks the context to judge mid-read writes.

Verdict taxonomy:

| Verdict | Action |
|---------|--------|
| SUPPORTED | nothing |
| OVERCONFIDENT | researcher trims/rewrites gloss, or downgrades epistemic mode (§2) |
| UNSUPPORTED | retraction revision (§5) — never delete |
| DUPLICATE | human merge queue (never auto-merge — fiction has real name-sharing) |
| BAD TITLE | researcher retitles via `rename_entry` (`old fort` → `Old Silver Mint`), old title auto-aliased |
| TOO GENERIC | demote to deferred (§4) |

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
- **Reclamation pass**: a periodic researcher sweep of the graveyard — promote dormants that acquired a second mention, collate low-signal entries that turn out to share a referent (cluster-linking pattern) and fold them into canonical entries via the merge queue, or propose culls for the truly inconsequential. Cull proposals are quote-audited and **demote-first** (graveyard, reversible); deletion stays human-only. The graveyard is the researcher's inbox.
- **Search/RAG**: FTS rank × salience; later, if embeddings arrive for the audit side, salience weights those too.

## 4. Deferral (admission policy)

First-read vs reread asymmetry is the core insight: `strange language` is unlinkable on first read (the entry can never be found again), but on reread it's obviously a known entry. Rules:

**The admission criterion** (Matt, 2026-09-05, revised same day): an entry is warranted if it meets ANY of: (a) the referent is unresolvable or confusing from an LLM's prior + local context; (b) it would be a legitimate wiki page even if locally obvious (`vys`, the `poison` effect); (c) it is a colloquial English word whose in-setting meaning diverges — the `husk` class (a soul-layer pregnant with story meaning; plain English outside). Overcapture is fine — the graveyard/researcher path prunes reversibly later. The glossary is a databank that turns *broken references* into *sourced, understood references* — half "worth documenting in a wiki", half "resolvable referent". A term context-clue-obvious on first read earns an entry when it shows up again (researcher path handles late recurrence).

- **Specificity gate at propose time** (heuristic, researcher-audited): a term that is a bare common-noun descriptor with no proper/coined token and no existing alias goes to a **`deferred_candidate`** table (term + quote + post) instead of `entry`.
- **Promotion**: if a deferred surface form recurs in a later batch (cheap trigger scan), it promotes to a real entry — the second sighting is evidence of referential stability.
- **Reread linking**: a researcher pass over the corpus with the complete glossary turns known descriptors into *aliases* of canonical entries (entity-linking task), not new entries.
- **Sub-entry split rule** (Aghtaki brands): a nested term earns its own entry when it appears in a batch *without* its parent term, or recurs under a second referent (Scourge on a new character). Both are measurable from entry_source/thread data — no judgment call needed at write time.

**Shadow mode first** (review-driven caution): the gate launches non-blocking — it logs which proposals it *would* defer, and we compare those against the thread-page gold set (dev-verification L5) before any write is actually blocked. A rigid heuristic could suppress valid coined/common-noun terms (e.g. a real mechanic named plainly); calibrate precision/recall in shadow, then enforce.

## 5. History: diffs, reverts, and the git question

Settled with Matt: **the revision table is the spine; git semantics on top; no git-mirror needed.**

We already store full gloss text per revision, append-only, with provenance. That gives, with small additions:

- **Diffs**: computed on demand between revisions (difflib over gloss + metadata delta: mode changes, alias adds, retitles). `fetch_entry` gains a `diff` view.
- **Revert**: restoring an old version = *append a new revision* whose gloss is the old text, with a `reverts: <revision_id>` marker — git-revert semantics, append-only preserved, blame intact.
- **Review past versions**: revisions are already queryable per entry; the wiki page renders its history block from them.
- **When tier-2 page bodies land**, they must be revisioned the same way (today only the gloss is versioned — page bodies don't exist yet; don't build pages without revisioning them from day one).

What we should NOT do: use git as the storage engine. The verifier, salience scoring, and deferral scans all need relational queries over history; git can't answer them without a database in front anyway.

## Implementation order sketch

1. Prompt-side epistemic marking + `mode` column (small schema add; design doc update same commit)
2. Salience scoring in injection priority + search; graveyard status (`cold`) + resurfacing rule
3. `deferred_candidate` table + promote-on-recurrence
4. Revision diffs + revert-as-revision + `rename_entry`
5. Researcher pass (batch CLI: `annotator research --thread N` / `--graveyard`) — wiring testable via the L1 scripted harness
6. Critic verdict pass gating researcher output; author-rehydration debate only for contested/high-value entries

All but the researcher/critic are model-free and L0-testable.

## Prior art map (details: `research-critic-salience.md`)

- §1 quality agents: **TMS** (Doyle 1979, dspace.mit.edu/handle/1721.1/5733) / **ATMS** (de Kleer 1986, dekleer.org/Publications/An%20Assumption-Based%20TMS.pdf) — entries' evidence quotes *are* justifications; canon drift withdraws support → retraction with dependency tracking. **CoVe-factored verification** (arxiv.org/abs/2309.11495 — verify claims *without* the draft rationale, avoiding self-agreement). **FActScore** (arxiv.org/abs/2305.14251) / **RAGAS** (arxiv.org/abs/2309.15217) claim decomposition. **Debate** (arxiv.org/abs/1805.00899) is the critic↔author protocol's ancestor. Self-critique loops are known to reinforce their own draft's assumptions (discussed around Self-Refine, arxiv.org/abs/2303.17651, and the LLM-judge literature generally) — by analogy, our critic gets the author's transcript + future story state rather than re-judging with the same information.
- §2 epistemics: **Wikidata rank/qualifier/reference** (wikidata.org/wiki/Help:Ranking; P248 "stated in"; P2241 deprecation reason) — deprecated-not-deleted with machine-readable retraction reasons. **FactBank** (Saurí & Pustejovsky 2009, link.springer.com/article/10.1007/s10579-009-9089-9) annotates event factuality *relative to nested sources* (author says X believes Y reports Z) with modality × polarity — our narrator/character/inference modes are the same source-relative structure applied to fiction. **AGM** (plato.stanford.edu/entries/logic-belief-revision/) epistemic entrenchment — rumor retracts before narrated fact.
- §3 salience: **Generative Agents** (arxiv.org/abs/2304.03442) recency × importance × relevance. **Mem0 floored decay** (mem0.ai/blog/introducing-memory-decay-in-mem0 — 0.3× floor, demote-not-delete, dormant terms resurface). **SWAT** (arxiv.org/pdf/1804.03580) co-occurrence-graph centrality — Vys vs Well Flower falls out of graph position. **C-value/contrastive** scoring (aclanthology.org/L10-1379/) for story-distinctiveness.
- §4 deferral: **NIL-linking with clustering** ("Learn to Not Link", arxiv.org/abs/2305.15725; TAC KBP NIL-clustering) — "don't create an entry yet" is a studied problem with the exact Missing-Entity vs Non-Entity-Phrase distinction we need. **Angell et al. cluster-linking** (aclanthology.org/2021.naacl-main.205/) — second co-referring mention crosses the threshold (the brand-split rule). **CESI canonicalization** (arxiv.org/abs/1902.00172) for retitle/merge. **Wikidata "structural need"** (wikidata.org/wiki/Wikidata:Notability) for when a sub-entry is earned.
