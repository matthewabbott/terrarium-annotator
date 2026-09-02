# Terrarium Annotator

An LLM harness that reads the Banished Quest corpus and builds a glossary/lorebook of setting-specific terms, characters, and places — every entry grounded in verifiable quotes from the source text. The long-term goal is wiki generation and semantic search over the story's lore.

## Status

**v2 clean-slate phase.** The v1 harness (tool-loop runner, tiered compaction, snapshots, curator) was removed in September 2026 after two persistent failure modes: overzealous term extraction by local models, and context-management machinery heavier than the task. Git history preserves v1 in full; its glossary output survives as `data/exports/glossary-v2-full.json` (3,623 entries with provenance) as an evaluation baseline.

Current work: implementing the v2 design.

## Design in one paragraph

The agent reads the corpus scene by scene. Persistent state is exactly two things: a **two-tier glossary** (short standalone *cards* auto-injected on keyword trigger within a hard token budget; full wiki-style *pages* with backlinks pulled on demand) and a **story digest** (append-only log of scene gists compressed by a lazy binary merge tree, giving fixed-budget continuity). Every glossary write requires a verbatim, mechanically verified quote from the corpus. See `SPEC.md` and `docs/design/v2-architecture.md`.

## Layout

| Path | Contents |
|------|----------|
| `banished.db` | Read-only corpus (276 threads, ~13k story/QM posts). Never modify. |
| `SPEC.md` | v2 specification |
| `docs/design/` | v2 architecture, research findings, design worklog |
| `docs/worklog/` | Session logs |
| `data/` | Gitignored run artifacts; `data/exports/` holds the v1 glossary baseline |
| `src/terrarium_annotator/` | (to be built) |
| `tests/` | (to be built) |

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
python -m pytest tests -q
ruff check src tests
```
