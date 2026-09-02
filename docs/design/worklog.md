# Design Work — Worklog

## 2026-02-12

### Session: Context Improvements Design Doc

**Author**: spark-claude (OpenClaw agent, dispatched by Matt)

#### Files Read
- `SPEC.md` — full project specification
- `src/terrarium_annotator/context/compactor.py` — tiered compaction (0.5→5), ~500 lines
- `src/terrarium_annotator/context/annotation.py` — context building, message formatting, ~400 lines
- `src/terrarium_annotator/context/prompts.py` — all system prompts (tool-based, reader, curator, sweep modes), ~400 lines
- `src/terrarium_annotator/runner.py` — orchestration (first 100 lines, config + imports)
- OpenClaw docs: `concepts/memory.md`, `concepts/context.md`, `concepts/session-pruning.md`, `concepts/compaction.md`

#### Analysis Performed
- Compared OpenClaw's 3-layer memory architecture (workspace files + session compaction + session pruning) with terrarium-annotator's tiered compaction
- Identified 4 transferable patterns: semantic retrieval, pre-compaction flush, structured summaries, snapshot search
- Identified complexity reduction opportunities with stronger models

#### Decisions Made
1. **Semantic retrieval** is the highest-priority improvement — it's the foundation for reader mode working well
2. **Pre-compaction flush** is low-effort, high-value — one extra inference call prevents lost intent
3. **Structured summary** over blob summary — independently updateable sections prevent drift
4. **Eval framework before simplification** — can't remove complexity without measuring the impact
5. **Reader mode as target default** — it's architecturally simpler and aligns with "glossary is memory" philosophy

#### Artifacts Written
- `docs/design/context-improvements.md` — full design document with implementation plans
- `docs/design/worklog.md` — this file

#### What's Next
- Matt mentioned wanting to try GLM-4 Flash or Kimi K2.5 — needs terrarium-agent changes (blocked by running ML jobs)
- Eval framework should be built before model swap so we can compare old vs new
- Audit/refactor pass on the codebase (separate task)

#### Notes
- Attempted to dispatch via Codex CLI first — failed with 401 (no OpenAI API key configured on this machine)
- Claude Code CLI launched but spent 25+ minutes without producing output (likely stuck in read-loop or rate-limited) — killed and wrote doc directly
- The terrarium-annotator codebase is well-structured; the context/ package is cleanly separated from storage/ and tools/

## 2026-09-02

### Session: v2 cutover — research synthesis + new architecture

**Author**: omp agent session with Matt

#### What
- Commissioned web research on rolling memory, two-tier retrieval, provenance, and glossary evaluation → `research-memory-rag.md`
- Assessed OptMem by reading the `memo` source: adopt the algorithm (append-only log, lazy merge tree, budgeted age-decaying cover), reimplement in-process, align merges to thread boundaries, keep glossary cards outside the decaying tree
- Wrote `v2-architecture.md` — the load-bearing design doc for the rebuild
- v1 code/docs deleted; details in `docs/worklog/2026-09-02-v2-cutover.md`

#### Supersedes
`context-improvements.md` was written for the v1 codebase (its file references are gone). Its semantic-retrieval and eval-framework ideas are absorbed into `v2-architecture.md` §3/§6 with one change: embeddings move off the read path (audit-only for duplicate detection) because small models can't be trusted with self-managed retrieval.
