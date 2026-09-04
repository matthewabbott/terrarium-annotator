# Research — critic, epistemics, salience, deferral prior art

*2026-09-03, background research agent. Feeds `critic-salience-epistemics.md`.*

## 1. Adversarial critic for grounded entries

**Doyle, "A Truth Maintenance System" (Artificial Intelligence 12, 1979)** — https://dspace.mit.edu/handle/1721.1/5733. Records *justifications* for every belief; beliefs stay IN only while justifications hold; dependency-directed backtracking retracts beliefs when support is withdrawn rather than chronologically undoing. Our data model: entries carry verbatim evidence quotes as justifications; canon drift = support withdrawal → retraction, not deletion.

**de Kleer, "An Assumption-based TMS" (AIJ 28, 1986)** — https://dekleer.org/Publications/An%20Assumption-Based%20TMS.pdf. ATMS maintains multiple assumption contexts simultaneously with a nogood database of contradictions. The glossary as "artifact of a moment in time": keep the in-character belief state at chapter N and the revised state side by side.

**Chain-of-Verification (Dhuliawala et al., 2023)** — https://arxiv.org/abs/2309.11495. Draft → plan verification questions → answer *independently of the draft* → revise. The Factored variant avoids self-agreement bias: critic re-derives support from quotes rather than asking "does quote support definition?" in one shot.

**FActScore (EMNLP 2023)** — https://arxiv.org/abs/2305.14251; **RAGAS faithfulness** — https://arxiv.org/abs/2309.15217. Groundedness as claim decomposition: split definitions into atomic claims, verify each against stored quotes, score = supported/total.

**SAFE (DeepMind, 2024)** — https://arxiv.org/abs/2403.18802. Agentic fact-checker; the "revise fact to be self-contained" step matters when claims reference context the standalone check lacks.

**Debate (Irving et al., 2018)** — https://arxiv.org/abs/1805.00899; **Self-Refine** — https://arxiv.org/abs/2303.17651 (note its documented self-bias failure — why the critic must use the author's recorded context + future story state, not re-judge with the same information).

*Pattern: TMS justification graph on quotes; critic = claim decomposition (RAGAS/FActScore) with CoVe-factored isolation; retraction on withdrawn justifications.*

## 2. Epistemic modes

**Wikidata ranks/qualifiers/references** — https://www.wikidata.org/wiki/Help:Ranking, Property:P248 "stated in", Property:P2241 "reason for deprecated rank". Production schema for retraction-without-deletion: deprecated statements retained with machine-readable reasons; per-claim provenance.

**FactBank (Saurí & Pustejovsky, LREC 2009)** — per-*source* epistemic modality {certain, probable, possible} × polarity; factuality annotated relative to nested sources (author says X believes Y reports Z) — precisely narrator vs. character vs. inference.

**AGM belief revision (1985)** — https://plato.stanford.edu/entries/logic-belief-revision/. Contraction keeps the rest coherent; epistemic entrenchment orders what falls first (rumor before narrated fact).

**Unreliable-narrator NLP** — TUNa / "Classifying Unreliable Narrators with LLMs" (2025) https://arxiv.org/abs/2506.10231; Wiebe (1994) point-of-view tracking for attributing claims to character sources.

*Pattern: Wikidata rank+qualifier+reference per claim; FactBank per-source modality; AGM entrenchment for retraction order; POV tracking for narrator-vs-character attribution.*

## 3. Salience-aware retrieval

**Generative Agents (Park et al., UIST 2023)** — https://arxiv.org/abs/2304.03442. `α_recency + α_importance + α_relevance`; exponential recency decay; importance static at write time (a known weakness — our recurrence-triggered re-salience fixes it).

**Mem0 Memory Decay** — https://mem0.ai/blog/introducing-memory-decay-in-mem0. Floored soft decay (0.3×–1.5× at search time): demote-not-delete; dormant terms resurface when strongest match.

**MemGPT** — https://arxiv.org/abs/2310.08560. Hot/cold tiered storage with explicit paging.

**Entity salience** — NYT-Salience (Dunietz & Gillick, EMNLP 2014): salience = centrality to document. **SWAT (ACL 2019)** — https://arxiv.org/pdf/1804.03580: PageRank centrality over entity co-occurrence graph + positional distribution + frequency. Fiction: "every-chapter mechanic vs one-off plant" falls out of graph position.

**SillyTavern World Info** — deployed reference for budget+priority deterministic injection.

**C-value/NC-value (2000)** + contrastive ranking vs. background corpus — https://aclanthology.org/L10-1379/: "distinctive to *this* story, not generic prose".

*Pattern: Generative-Agents triple for ranking; Mem0 floored decay for demote-not-delete; SWAT co-occurrence centrality for importance; C-value/contrastive scoring for story-distinctiveness.*

## 4. Overgeneric entries & deferred creation

**NIL prediction / out-of-KB linking** — "Learn to Not Link" (2023) https://arxiv.org/abs/2305.15725; NILK (CIKM 2022); TAC KBP NIL-clustering. The NIL decision — *no KB entry and shouldn't get one yet* — is deferred creation. Missing-Entity vs Non-Entity-Phrase separates "real thing, not yet notable" from "generic descriptor". NIL-clustering assigns co-referring unlinkable mentions the same cluster ID — accumulate until an entry is earned.

**Clustering-based inference for unseen entities (Angell et al., NAACL 2021)** — https://aclanthology.org/2021.naacl-main.205/: link as a cluster; one mention stays NIL, a second co-referring mention crosses the threshold. Exactly the brand-split rule.

**Coreference vs. named entity** — Lee et al. 2017 https://arxiv.org/abs/1707.07045: anaphoric/generic mentions vs. singletons; the overgeneric-entry bug is a mention-detection failure.

**TAC KBP Cold Start** — KB population from raw corpus, the evaluation lineage for first-read glossary building.

**CESI (WWW 2018)** — https://arxiv.org/abs/1902.00172: canonicalizing Open KBs via embeddings + side information — the retitle/merge operation.

**Wikidata notability / Wikipedia disambiguation** — the "structural need" criterion legitimates a sub-entry exactly when another statement needs it; Wikipedia naming conventions govern canonical titles and rename-on-reread.

*Pattern: NIL-with-clustering (promote on second co-referring occurrence); CESI canonicalization for retitle/merge; Wikipedia naming for titles; cold-start framing for first-read population.*
