# 2026-01-06 - Reader Mode: Scene-Based Glossary-as-Memory

## Objective

Implement a new processing mode that treats the glossary as the agent's persistent memory, using near-stateless scene-by-scene processing to avoid context explosion.

## Context

State of the codebase when I started:
- Current feature in progress: Post-F11 evaluation
- Last completed work: F11 thread-based processing produced only 22 entries from 12,721 posts
- Known issues: Thread mode overwhelmed agent, caused hallucinated IDs, sparse output

Analysis showed the mental model was wrong: agent should "read" the story and notice terms with special meanings, not batch-process entire threads.

## Work Done

- [x] Add migration 009 for `glossary_source` table (reference scene tracking)
- [x] Add `lookup_terms()` and `add_source_scene()` to GlossaryStore
- [x] Add `GLOSSARY_UPSERT_SCHEMA` and `GLOSSARY_LOOKUP_SCHEMA` to schemas.py
- [x] Add `upsert()` and `lookup()` methods to GlossaryTools
- [x] Add `glossary_upsert` and `glossary_lookup` handlers to dispatcher
- [x] Add `READER_MODE_PROMPT` to prompts.py
- [x] Add `build_reader_messages()` and `reset_for_new_scene()` to AnnotationContext
- [x] Add `_run_reader_mode()`, `_get_glossary_context_for_scene()`, `_run_reader_tool_loop()` to runner.py
- [x] Add `--reader-mode` CLI flag

### Key Design Decisions

1. **Near-stateless scenes**: Conversation history resets between scenes. The glossary IS the memory.
2. **Upsert tool**: Eliminates DUPLICATE errors and search-then-decide dance. Creates or updates with optional smart merge.
3. **Glossary context injection**: For each scene, lookup terms that appear in the text and inject their definitions.
4. **Source scene tracking**: New `glossary_source` table tracks which scenes contributed to each definition.

### Files Modified

```
src/terrarium_annotator/
  storage/migrations.py      # Migration 009: glossary_source table
  storage/glossary.py        # lookup_terms(), add_source_scene(), get_source_scenes()
  tools/schemas.py           # GLOSSARY_UPSERT_SCHEMA, GLOSSARY_LOOKUP_SCHEMA
  tools/glossary_tools.py    # upsert(), lookup() methods
  tools/dispatcher.py        # Handlers + set_merge_callback(), set_scene_index()
  tools/xml_formatter.py     # format_success() now accepts **kwargs
  context/prompts.py         # READER_MODE_PROMPT, updated get_system_prompt()
  context/annotation.py      # build_reader_messages(), reset_for_new_scene()
  runner.py                  # _run_reader_mode(), reader config option
  cli.py                     # --reader-mode flag
```

## Results

### Run v1 (Initial Prompt)

After ~23 hours (75/278 threads, ~27%):
- 2,775 glossary entries created
- 7,894 source references tracked
- Context usage: 3-4% (excellent stability)

**Quality Issues Identified:**

1. **Meta/quest mechanics leaking in (~150 entries)**
   - Dice rolls: `1d20`, `3d10`, `dice+3d10`
   - Stats: `([52]/26)`, `5/14 Vys`, `+10 bonus`
   - Platform: `4chan`, `4chan Time`, `QM`, `vote`, `OP`

2. **Mundane words that don't belong (~100+ entries)**
   - Short meaningless: `arm`, `egg`, `fog`, `gas`, `gun`, `ice`, `inn`
   - Over-elaborated: `immediately`, `love`, `blue tarp`, `walking boots`
   - Definitions for these were hallucinated (e.g., "immediately is a magical imperative")

3. **Over-fragmentation (same concept, many entries)**
   - Vys: 110 entries (`Vys`, `Vys energy`, `Vys pool`, `spend Vys`, etc.)
   - Rhynian: 49 entries (`Rhynian`, `Rhynian artifacts`, `Rhynian devices`, etc.)

### Prompt Tightening (v2)

Rewrote `READER_MODE_PROMPT` with:
- Explicit exclusions for quest mechanics, platform terms, mundane objects
- Consolidation guidance: one entry per concept, update don't duplicate
- Concrete examples of good vs bad entries
- Decision heuristic: "fewer high-quality entries beat many low-quality ones"

Run v2 started with fresh database to compare.

## Decisions Made

### Decision: Near-Stateless vs History Accumulation
**Options**: A) Accumulate history within thread, B) Reset after each scene
**Chose**: B - Reset after each scene
**Rationale**: The glossary IS the persistent memory. No need for conversation history when relevant definitions are injected per-scene.

### Decision: Upsert Merge Strategy
**Options**: A) Replace definition, B) Append, C) Smart LLM merge
**Chose**: A (Replace) with hook for C later
**Rationale**: Start simple. Added `merge_callback` parameter for future smart merge via LLM.

### Decision: Glossary Context Injection
**Options**: A) FTS search, B) Exact term lookup
**Chose**: B - Extract words from scene, lookup matching glossary entries
**Rationale**: More precise than FTS, injects only definitions for terms actually in the scene.

## Open Questions

- Should we add story summarization between scenes? Currently no cumulative summary.
- Is smart merge (LLM combining definitions) worth the extra inference cost?
- Should reader mode support codex entries too, or stay glossary-only?

## Next Steps

1. ~~Let v1 run complete and evaluate~~ → Paused after quality analysis
2. [In Progress] Run v2 with tighter prompt, compare entry quality
3. Consider adding cumulative story summary for narrative context
4. Evaluate whether smart merge is needed

---
*Agent: Claude Opus 4.5*
*Sessions: 2026-01-06 (implementation), 2026-01-07 (quality analysis + prompt v2)*
