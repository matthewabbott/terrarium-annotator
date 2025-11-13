# Terrarium Annotator

An always-on harness that walks the Banished Quest corpus, calls the local `terrarium-agent` server, and curates a codex of people, places, items, and mechanics. The agent keeps a rolling conversation history plus a JSON codex so later passages inherit the same terminology.

## How it Works

1. `CorpusReader` streams `story_post` rows from `banished.db` in chronological order.
2. `AnnotationContext` injects the current passages, any matching codex entries, and prior turns into an OpenAI-format payload.
3. `AgentClient` hits `http://localhost:8080/v1/chat/completions` (the terrarium-agent HTTP API).
4. The assistant must reply with `<codex_updates>[...]</codex_updates>` JSON plus optional `<analysis>` text.
5. `CodexStore` parses those updates, persists them to `data/codex.json`, and `ProgressTracker` records the last processed post so the next run resumes automatically.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Ensure terrarium-agent server is already running on localhost:8080
python -m terrarium_annotator.cli run --db banished.db
```

Useful flags:
- `--limit 50` – annotate only the next 50 posts.
- `--batch-size 3` – group multiple posts per LLM call.
- `--no-resume` – restart from the first post (ignores `data/state.json`).
- `--agent-url http://localhost:8081` – point at a different terrarium-agent endpoint.

All harness artifacts live under `data/`:
- `data/codex.json` – accumulated codex entries.
- `data/state.json` – last processed post id (safe to delete to restart from scratch).

## Current Status

- [x] Minimal agent harness with codex persistence.
- [ ] Term extraction heuristics beyond simple substring matching.
- [ ] Automatic conversation summarization/compaction.
- [ ] Tool calling for schema migrations or downstream exporters.
