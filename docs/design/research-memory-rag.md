# Context-Management & Retrieval Research for terrarium-annotator v2

*Research compiled 2026-09-02 (background research agent). Sources linked inline.*

Scope: a sequential fiction-corpus reader (~276 threads, ~13k QM/story posts) that builds a glossary/wiki. v1 failed on (a) overzealous term extraction by local models and (b) poor context management. Each section ends with a recommended fit.

---

## 1. Rolling / hierarchical memory for sequential long-document reading

### OptMem (the candidate under evaluation)
Victor Taelin's OptMem is a 426-token prompt block + one dependency-free Python file. The agent writes one-line memories to an append-only `LOG.txt`; pairs of memories are compressed on-the-spot into one-line summaries (`#0-1`, `#2-3`), pairs of those into `#0-3`, forming a lazy binary tree. At "wake" the agent gets a constant-size slice of the tree: recent memories verbatim, older ones as progressively coarser summaries (96 lines ≈ 8k tokens; `WAKE_LINES` is a *reading* budget, resizeable freely since nothing is recomputed). Nothing is ever deleted; retrieval into old detail is regex/search over the log, not embeddings. At 1M memories wake takes 0.03s because records are fixed-width. — https://github.com/VictorTaelin/OptMem, https://moclaw.ai/blog/what-is-optmem

Fit for sequential reading: **good structural fit**. Unlike chatbot memory, the corpus stream is itself ordered, so time-decay ≈ narrative-distance decay, which is exactly what a reader needs (chapter 200 should remember chapter 199 in detail, chapter 12 as a gist). Two caveats: (a) regex-only recall means the agent must remember *that* a fact exists to grep for it — fine if glossary cards carry the pointers (§3); (b) the tree summary nodes are written by the same model that reads, so with a small local model, summary quality is the bottleneck, not the data structure. The binary pairing is content-agnostic; pairing at *post/thread boundaries* instead of raw adjacency would respect discourse structure (see BOOOOKSCORE below).

### MemGPT / Letta
OS-inspired virtual context management: main context ("RAM": system prompt + core memory blocks) is paged against recall storage (full message history) and archival storage (vector-indexed). The LLM itself calls functions (`core_memory_append`, `archival_memory_search`) to move data between tiers; a heartbeat interrupt lets it chain memory ops before responding. Beat GPT-4 baseline on document analysis (89% vs 63%). Evolved into Letta (Postgres-backed, 74.0% on LoCoMo with GPT-4o-mini). Known weakness: *orchestration* — paging the wrong things wastes tokens; archiving too aggressively causes "memory blindness" where the agent doesn't know a fact exists. — https://arxiv.org/abs/2310.08560, https://letta.com

Fit: **partial**. The self-paging idea is right, but it presumes a model strong enough to manage its own memory — a bad bet on small local models (see §2 on tool-call mismatch). Borrow the *tiering*, not the *agent-managed paging*; make paging deterministic.

### RAPTOR
Recursively embeds chunks (SBERT), clusters with a Gaussian mixture model, summarizes each cluster, and repeats bottom-up into a tree of abstractions; retrieval uses either tree traversal or a "collapsed tree" (all nodes flat in one index). +20% on QuALITY with GPT-4 over vanilla RAG. — https://arxiv.org/abs/2401.18059, https://github.com/parthsarthi03/raptor

Fit: **conceptually related but mismatched**. RAPTOR's tree is built by *semantic clustering* (non-sequential, batch, offline), OptMem's by *temporal adjacency* (incremental, online). For a corpus read in order, temporal adjacency is the correct structure; RAPTOR's clustering would mix threads. Notable: RAPTOR needs the whole corpus up front — a batch RAPTOR pass over already-read threads could complement the rolling log as a "second brain" for cross-thread themes, but that's optional scope.

### ReadAgent
Human-inspired: (1) *episode pagination* — the LLM picks natural pause points between paragraphs; (2) *memory gisting* — each episode compressed to a short gist; (3) *interactive lookup* — the agent re-reads original episodes when a task needs detail. 3.5–20× effective context extension; on NarrativeQA beat neural/BM25 retrieval by +12.97% (strict LLM rating) on full books. Cost caveat: hundreds of API calls per long text; efficiency depends on cheap low-latency inference — actually an argument *for* local models here. — https://arxiv.org/abs/2402.09727

Fit: **strongest conceptual match**. Sequential reading with continuity is literally its design case (books). The pagination-by-LLM step is skippable (forum threads have natural post boundaries), but "gists + on-demand re-read of source posts" should be the spine of v2: the digest gives continuity, the source posts are the ground truth the agent can pull back.

### A-MEM (Agentic Memory)
Zettelkasten-style: each new memory becomes a structured note (LLM-generated keywords, tags, context description); the system links it to related historical notes and *memory evolution* retroactively updates older notes' attributes when new memories arrive. — https://arxiv.org/abs/2502.12110, https://github.com/agiresearch/a-mem

Fit: **borrow selectively**. The retroactive-update mechanism is exactly what a glossary needs (a term's definition *changes* as the story reveals more — "the Sovereign" turns out to be three people). But A-MEM's LLM-generated linking at every write is expensive and noisy on small models. Keep: entries are *living documents* with revision history. Skip: free-form dynamic link generation.

### Mem0
Production memory: extracts atomic facts from conversation turns, reconciles against existing store (add/update/delete), vector+graph retrieval. April 2026 update: single-pass hierarchical extraction (one LLM call, add-only — history survives), multi-signal retrieval with recency boost/idle decay; 92.5 LoCoMo / 94.4 LongMemEval at <7k tokens per retrieval call. — https://arxiv.org/abs/2504.19413, https://mem0.ai/research

Fit: **reference architecture, wrong workload**. Mem0 is chatbot memory (facts about a *user*). The useful imports: add-only extraction (no destructive overwrite — conflicts become *revisions*, crucial for fiction where "facts" get retconned in-story), and recency-boosted ranking.

### Hierarchical vs. incremental summarization (BOOOOKSCORE / BookSum)
BookSum established that long narrative summarization has three granularities (paragraph/chapter/book). BOOOOKSCORE (ICLR 2024) compared *hierarchical merging* (summarize chunks, recursively merge — three distinct prompts) vs. *incremental updating* (running summary updated per chunk). Hierarchical merging scores higher on coherence (incremental requires harder instruction-following, error-prone on weaker models); incremental keeps more detail, which humans sometimes preferred. They also catalog 8 coherence error types in book summaries. — https://arxiv.org/abs/2105.08209, https://arxiv.org/abs/2310.00785

Fit: **directly decision-relevant**. OptMem's tree is hierarchical merging, done incrementally — the best of both, and empirically the right choice when the summarizer is a small model (each step is a simple "compress these two lines" task, not "update this complex running state"). For the reader's *current-chapter* context, use a small running summary; for the archive, use the tree.

### Recommended fit (§1)
Adopt OptMem's spine (append-only log + lazy binary summary tree + constant-size wake digest) with three modifications: (1) pair nodes on post/thread boundaries, not raw line adjacency; (2) put glossary *cards* (§2 tier 1) in the always-present core context, outside the decaying tree, since entities must not fade; (3) retrieval = deterministic grep/keyword + the glossary backlink index, not embeddings, matching OptMem's "position is identity" simplicity. ReadAgent's gist-then-lookup loop is the behavioral pattern; A-MEM's retroactive revision is the glossary update pattern.

---

## 2. Two-tier retrieval for a growing glossary/lorebook

### SillyTavern World Info (the mature reference implementation)
Lorebook entries are keyword-triggered: when a key appears in the scanned chat window, the entry's content is injected. Key mechanics, all tunable: **scan depth** (how many past messages to scan), **token budget** (cap on injected lore; 0 = auto 20% of context window; overflow drops lowest-priority entries), **priority/order**, **recursive scanning** (entry content can trigger other entries, with `max_recursion_depth` to bound the cascade), **constant/always-on entries**, **minimum activations** (keep scanning backward until N entries triggered), and **injection depth** (where in the prompt the content lands). Community guidance: entries must be standalone (only the Content field is injected — titles/keys aren't), scan depth ~15, budget ~1800 tokens for adventure mode. — https://docs.sillytavern.app/usage/core-concepts/worldinfo/, https://deepwiki.com/SillyTavern/SillyTavern/6.1-world-info-system

Lesson for v2: this ecosystem converged over years on exactly the tier-1 design — short standalone cards, keyword trigger, hard token budget, bounded recursion. Proven with small local models (SillyTavern's core user base runs 7–70B local). The known failure mode is *trigger spam*: over-broad keys flood the budget and crowd out chat — which is v1's overzealous-glossary failure in another guise. Mitigations from the ecosystem: per-entry budget share, priority tiers, and requiring *specific* (multi-word, case-sensitive) keys.

### RAG for long-form story generation (the academic mirror)
- **SCORE** (story coherence + retrieval): dynamic state tracking of objects/characters via symbolic logic, hierarchical episode summaries, hybrid TF-IDF + embedding retrieval; 41.8% fewer hallucinations than GPT baseline. — https://arxiv.org/abs/2503.23512
- **FictionRAG**: *stateful* retrieval for long-narrative roleplay — argues static stateless RAG can't track evolving plot, causing character hallucination. — https://doi.org/10.3390/a19050383
- **STORYTELLER** / entity-event KG RAG / "Guiding Generative Storytelling with KGs": track entities as SVO triplets or KG nodes with temporal state; retrieval respects *when* a fact was true. — https://arxiv.org/abs/2506.02347, https://arxiv.org/abs/2505.24803
- **Lost in Stories / ConStory-Bench**: LLM-judge taxonomy of consistency bugs (plot, character, timeline) — useful as the downstream failure taxonomy the glossary is meant to prevent. — https://arxiv.org/abs/2603.05890

Lesson: fiction retrieval must be **temporal/stateful** — a glossary entry needs versioned definitions ("as of thread 87") or at least last-updated provenance, because canon drifts.

### Progressive disclosure / contextual retrieval / agentic pull
- **Anthropic Contextual Retrieval**: prepend LLM-generated chunk-specific context to each chunk before embedding + Contextual BM25 hybrid; 49% fewer failed retrievals (67% with reranking). — https://anthropic.com/engineering/contextual-retrieval. For the glossary this maps to: index each entry with a *situated* description ("X, the smuggler introduced in thread 3, sister of Y"), not a bare term.
- **Agentic RAG tradeoffs**: tool-based pull improves relevance on complex multi-facet queries but multiplies latency, token cost, and complexity; naïve unconditional injection creates a paradox where injected memory consumes the very window it was meant to extend, and "lost in the middle" degrades reasoning on stuffed context. — https://arxiv.org/abs/2501.09136, https://mastra.ai/articles/agentic-rag, https://arxiv.org/pdf/2605.20724 (CALMem, token-budget-adaptive injection)

### Auto-inject vs. tool-based pull, by model size
The deciding evidence is small-model tool-use reliability:
- Across four 3–8B open models, the mismatch between *needed* and *actual* tool calls is 26.5–54.0% on arithmetic and 30.8–41.8% on factual QA — small models frequently fail to call a tool they need, or call it wrongly. — https://arxiv.org/abs/2510.03847 (SLM agentic survey), medium.com/@michael.hannecke analysis
- Small models (1.5–3B) also show an 8.2% average accuracy *drop* under standard RAG frameworks and readily abandon correct answers when handed retrieved content; filtering retrieved content recovers it. — https://arxiv.org/pdf/2602.00887 (EffGen)
- Reliable local tool-calling exists (Qwen3 32B, Llama 3.3 70B ~97% well-formed call rate) but at 24–48GB+ VRAM, not "small". — promptquorum / docker.com local tool-calling evals

Implication: **tier 1 (cards) should be auto-injected, tier 2 (wiki pages) tool-pulled — but never *depend* on the pull.** A small model that forgets to call `fetch_wiki(X)` degrades gracefully if the card was already injected; a system that *requires* the call fails 30–50% of the time. For frontier API models the balance can shift toward on-demand pull (they call tools reliably and pay real per-token cost); for local models, inference is free-ish and context is cheap, so bias toward auto-inject with a strict budget. In both cases, keep tier-2 pages out of the default context: full pages + backlinks are big, and FictionRAG/SCORE-style state only needs the card for continuity.

### Recommended fit (§2)
SillyTavern-style deterministic auto-injection for tier 1: card = term + 1–2 sentence standalone definition + trigger keys (exact + alias), injected on keyword match against the *current post* only (scan depth 1–2 — unlike chat, the reader always processes fresh text, so deep scans are needless), hard token budget (~15–20% of context), priority = entry size/recency, recursion depth ≤1. Tier 2 = wiki page with backlinks, fetched via a `fetch_entry(term)` tool *and* offered via a one-line hint in the card ("full page available"), so tool-pull failure degrades to card-only rather than nothing. Version each entry's definition; never silently overwrite canon.

---

## 3. Provenance / backlinks

### Citation-grounded RAG patterns
- **ALCE** (Gao et al., EMNLP 2023): first benchmark for automatic citation evaluation; NLI-based entailment checking of generated statements against cited passages. Citation *recall* = the union of cited docs entails the claim; citation *precision* = dropping any one cited doc breaks entailment (i.e., every citation is load-bearing). This precision definition is directly reusable for glossary backlinks: a backlink that isn't load-bearing is noise. — https://arxiv.org/abs/2305.14627, https://github.com/princeton-nlp/ALCE
- **Quote-then-verify**: require the generator to emit verbatim supporting quotes with source IDs, then verify substring/entailment mechanically *before* committing the entry. Catches the classic failure where answers "appear grounded" (have citation markers) but the claim is unsupported, distorted, or stitched from irrelevant evidence. — medium.com/@Nexumo_ RAG grounding tests; CiteEval (principle-driven citation eval) — https://arxiv.org/pdf/2506.01829
- **"Correctness is not Faithfulness in RAG Attributions"**: a correct claim can still be mis-attributed; provenance must be checked separately from factuality. — https://arxiv.org/pdf/2412.18004

### Blame tracking / revision history
No established "git blame for RAG" standard exists; the practical pattern is structural: store provenance as data, not prose. Each glossary/wiki entry carries a list of `{source_id, span, quote, extraction_pass_id, timestamp}` tuples; definition *edits* append a new revision with its own evidence list rather than mutating (mirrors Mem0's 2026 add-only turn and A-MEM's memory evolution). This gives: (a) blame — which reading pass introduced an error; (b) retraction — if a thread is found to be non-canon or the extraction prompt was buggy, delete-by-pass-id; (c) verifiability — quotes can be re-checked against the immutable corpus offline.

Graph-side precedent: LightRAG attaches source-paragraph references to extracted entities/relations and dedups across paragraphs; MS GraphRAG keeps source chunk IDs on every extracted node (though its own entity resolution is famously unimplemented — a warning that dedup + provenance must be designed together, since a merge must union the evidence lists, not discard them). — https://github.com/HKUDS/LightRAG, https://github.com/microsoft/graphrag/discussions/778

### Recommended fit (§3)
Backlinks are first-class structured data: every entry and every definition revision stores `[(thread_id, post_id, quote)]`. Enforce ALCE-precision discipline at write time: when adding a backlink, the quote must contain the term (cheap substring check) and an NLI/LLM check that the quote supports the claimed definition (quote-then-verify before commit). Merges union evidence; corrections append revisions; never edit history. This also feeds §4: provenance coverage (% of entries with ≥1 verified quote) is a hard metric.

---

## 4. Evaluation criteria for glossary quality

### Existing frameworks to reuse
- **RAGAS**: decomposes answers into atomic claims and verifies each against retrieved context (faithfulness); context precision/recall for the retrieval side. LLM-as-judge based. Faithfulness maps 1:1 to "definition accuracy against source quotes". — https://docs.ragas.io, https://arxiv.org/abs/2309.15217
- **ARES**: fine-tuned small LM judges with synthetic training data + prediction-powered inference (PPI) to calibrate against a few hundred human labels; beats RAGAS by up to 59.3pp on context relevance. Important because PPI gives *statistical confidence* on LLM-judged scores with small human-label budgets — realistic for a solo project. — https://arxiv.org/abs/2311.09476
- **ALCE citation recall/precision** (§3) for backlink quality.
- **HHEM** (Vectara) — a classifier (not LLM-judge) hallucination detector; cheaper and more stable for batch re-verification of the whole glossary. — vectara.com/blog/evaluating-rag
- **HalluLens / LongWiki**: hallucination taxonomy for long-form generation (entity-error, relation-error, incompleteness, outdatedness, overclaim, unverifiability) — reuse as the failure-category labels for glossary auditing. — https://arxiv.org/abs/2504.17550
- **BOOOOKSCORE's 8 coherence error types** for narrative summaries — relevant if thread summaries are also evaluated. — https://arxiv.org/abs/2310.00785

### Automatic Term Extraction (ATE) — term-selection precision/recall
Classic ATE evaluates precision/recall/F1 at *type level* (unique terms) and *micro level* (per-occurrence); hybrid linguistic-filter + statistical-filter pipelines beat either alone ("Automatic glossary term extraction from large-scale requirements specifications" — chunking + embedding-based semantic filtering to prune candidates). LLMs beat traditional statistical ATE but are over-generous — exactly v1's failure. — https://aclanthology.org/2025.findings-acl.516/, https://dl.acm.org/doi/10.1145/3787584 (ACM CSUR ATE survey), https://ieeexplore.ieee.org/document/8491159

Practical hard metrics for "was this term worth an entry":
1. **Frequency × spread**: mentions in ≥k distinct threads (single-thread jargon rarely deserves a card); TF-IDF/c-value style domain-relevance against a generic-fiction background corpus.
2. **Trigger utility** (the decisive one): simulate reading — for each entry, count posts where (a) the term appears, (b) the definition would change interpretation, vs. posts where injection is pure noise. This is a *downstream* precision metric, like ALCE citation precision but for injection.
3. **Injected-token share**: % of total context spent on glossary; v1's overzealousness shows up here before anywhere else.
4. **Human/LLM-judge gold set**: hand-label ~100 candidate terms (worth/not-worth); report type-level P/R/F1. ARES-style PPI to scale the judge.

### Recall (missed terms)
Recall is the hard direction — you can't count what you never extracted. Options: (a) **adversarial second pass** — a different prompt/model scans posts for "terms a reader unfamiliar with the setting couldn't understand" and diffs against the glossary; misses = recall holes. (b) Gold-set recall against the hand-labeled 100. (c) Proxy: **context recall** (RAGAS-style) on reading comprehension QA — build 50–100 questions requiring setting knowledge; if the glossary+wake digest can't answer, identify the missing entity.

### Definition accuracy / hallucination
Decompose each definition into atomic claims (RAGAS faithfulness pattern), verify each against the entry's stored quotes: substring → NLI/LLM entailment → human spot-check. Score = supported claims / total claims. Target ≥0.8 (standard production) / ≥0.9 if entries auto-inject into every downstream read. Outdatedness is a fiction-specific failure: a definition that *was* true but canon moved — check `last_verified_thread` against current read position; entries untouched for >N threads get re-verification queued.

### Duplicate / fragment detection
State of the art in GraphRAG-land: string matching misses case/abbreviation/synonym/typo variants ("Less is More: Denoising KGs for RAG"); KGGEN uses iterative LLM-guided clustering to merge semantically equivalent entities; entity resolution pipeline = blocking (embedding similarity) → matching (LLM pair judge) → merging (union evidence, alias table). — https://arxiv.org/pdf/2510.14271, https://arxiv.org/pdf/2510.20345 (LLM-KG-construction survey). Metrics: (a) gold alias set P/R ("the Sovereign" = "Sovereign" = "Sov"); (b) **false-merge rate** — worse than duplicates in fiction (two different characters who share a name/title is a real trope); every proposed merge should require an LLM pair-judgment citing *both* entries' evidence quotes; (c) alias-table coverage: every surface form that triggered injection resolves to a canonical entry.

### Cross-reference validity
Graph-hygiene checks, all automatable: every `[[link]]` resolves to an existing canonical entry (dangling-link count); backlink symmetry (A links B ⇒ B's source list or related-entries mentions A); backlinks point to post IDs that exist and contain the term; no cycles of entries whose entire definition is circular ("X: see Y" / "Y: see X").

### Recommended fit (§4)
Automatable dashboard (run after every reading pass): provenance coverage ≥95% of entries with verified quote; faithfulness (claims entailed by quotes) ≥0.85; dangling-link count = 0; injected-token share ≤ budget; duplicate-cluster count from embedding blocking (human-reviewed merge queue, never auto-merge). Soft metrics on a fixed 100-term gold set + 50–100 comprehension QA: type-level P/R/F1 for term selection (judge calibrated à la ARES PPI); definition helpfulness rated by LLM-judge against source quotes. The two v1-specific tripwires: injected-token share (overzealousness) and simulated trigger-utility (precision that matters downstream).
