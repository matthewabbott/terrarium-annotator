# Terrarium Annotator

An LLM harness that reads the Banished Quest corpus and builds a glossary/lorebook of setting-specific terms, characters, and places — every entry grounded in verifiable quotes from the source text. The long-term goal is wiki generation and semantic search over the story's lore.

## What works today (2026-09-04)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Annotate (via omp RPC on a Kimi subscription; --threads to scope)
terrarium-annotator run --corpus-db banished.db --annotator-db data/annotator.db \
    --threads 30265887,30305969 --pass-id my-pass --record data/recordings/run.jsonl

# Check every stored claim against the corpus
terrarium-annotator verify --corpus-db banished.db --annotator-db data/annotator.db

# Interrogate the glossary/story (read-only, write tools rejected)
terrarium-annotator chat --corpus-db banished.db --annotator-db data/annotator.db
```

Smoke-proven: threads 1–2 and 3–5 read end-to-end with kimi-k2.5 — 98 quote-grounded entries across the two runs (40 in the threads 1–2 DB, 58 in the threads 3–5 DB), `verify` clean on both.

## Current wiring

```
                    banished.db (read-only corpus)
                         │  story_post, OP-time order
                         ▼
        CorpusReader ──► batches of story posts
                         │
                         ▼
        Runner ───────────────────────────────────────────┐
          │  assembles: system + digest + cards + batch   │
          ▼                                               │
        ChatClient (OmpRpcClient: omp --mode rpc          │
                    --no-tools; OpenAI-compatible HTTP;   │
                    ScriptedModel in tests)               │
          │  <tool_call> text blocks                      │
          ▼                                               │
        ToolDispatcher (write tools quote-gated)          │
          │                    │                          │
          ▼                    ▼                          │
        GlossaryStore      StoryLog (gist per batch;      │
        (entries, revisions, merge tree settled at        │
         sources, modes,    thread close)                 │
         deferral log)                                    │
          └────────────────────┬──────────────────────────┘
                               ▼
                    data/annotator.db (one shared SQLite:
                    entries, story log/tree, transcripts,
                    run_state — checkpoint/resume per pass)
                               │
              ┌────────────────┼─────────────────┐
              ▼                ▼                 ▼
          verify (8        chat (read-only   recordings (L4
          invariants vs    archivist REPL    replay fixtures)
          corpus)          for Matt)
```

## Desired wiring (designed, not built)

```
 annotator (reads, writes)          researcher (top-down: reads glossary,
     │ advisor steering               investigates story, revises entries)
     │ (cheap, per-write)             │  owns: graveyard reclamation,
     ▼                                │        reread linking, retitles
 write-time gates                ┌───▼────┐
 (quote gate • epistemic        │ critic │ (fresh context; ONLY disproves;
  modes • shadow deferral)      │        │  never edits; debates rehydrated
                                └───┬────┘  author on contested entries)
                                    ▼
                            human queue (merge, disputes)
                                    ▼
         tier-2 wiki pages ──► steelbea.me export (thread + entry pages)
                                    ▼
              semantic search / embeddings over the finished lorebook
```

## Layout

| Path | Contents |
|------|----------|
| `banished.db` | Read-only corpus (276 threads, 9,813 story posts). Never modify. |
| `src/terrarium_annotator/` | `corpus/`, `memory/`, `glossary/`, `inject/`, `llm/`, `tools/`, `runner.py`, `chat.py`, `verify.py`, `state.py`, `cli.py`, `harvest_gold.py` |
| `tests/` | 155 tests mirroring modules (L0–L2 + L1 scripted end-to-end) |
| `SPEC.md` | v2 specification |
| `docs/design/` | v2 architecture (load-bearing), quality architecture, verification layers, research |
| `docs/plan/` | build plan + autonomous-run guardrails |
| `docs/worklog/` | session logs, smoke + calibration reports |
| `data/` | gitignored: annotator DBs, recordings, `exports/` (gold set, v1 anti-baseline) |

## Development

```bash
python -m pytest tests -q   # merge bar, with:
ruff check src tests && ruff format src tests
```

Session protocol and build plan: `AGENTS.md`, `docs/plan/v2-foundation.md`.
