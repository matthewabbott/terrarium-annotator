# Repository Guidelines

## Project Structure & Module Organization
The repository currently contains the reference corpus `banished.db` and usage notes in `how to use banished.db.txt`; keep these untouched so runs remain reproducible. Place all runnable code inside `src/terrarium_annotator/` using clear module boundaries (e.g., `codex/`, `ingest/`, `cli.py`) so imports stay stable. Mirror every module with a test file under `tests/` (for instance, `tests/test_codex.py`). Long-lived assets such as prompt templates or schema definitions belong in `assets/` or `config/` subfolders, while notebooks and scratch work should go in `research/` to keep the main tree clean.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` — isolate dependencies before hacking on the annotator.
- `pip install -r requirements.txt` — install the pinned toolchain once the dependency list is updated.
- `python -m terrarium_annotator.cli run --db banished.db` — execute the harness against the bundled story database for manual verification.
- `python -m pytest tests -q` — run the test suite locally; add `-k name` to target a specific component.
- `ruff check src tests` — lint and format in a single pass; run `ruff format` when touching large files.

## Coding Style & Naming Conventions
Follow PEP 8 with 4-space indentation, 88-character lines, and type hints on all public functions. Prefer descriptive module names (`codex_store.py`, `story_buffer.py`) and snake_case for variables/functions; reserve PascalCase for classes and TypedDicts. Keep docstrings short but precise, documenting parameters that influence LLM context size. Run `ruff` (lint) and `ruff format` (format) before every push; do not mix unrelated stylistic and behavioral changes in one PR.

## Testing Guidelines
Use `pytest` with fixtures that fabricate minimal SQLite snapshots instead of mutating `banished.db`. Name tests after the behavior under test (`test_codex_lookup_rehydrates_cached_definition`). When adding orchestration logic, include regression tests that assert both the codex output and the streamed prompts. Target high-value areas—LLM prompt construction, database migrations, and CLI argument parsing should all have coverage before feature work is marked complete.

## Commit & Pull Request Guidelines
Write imperative, concise commit subjects similar to the existing history (`Add codex ingestion pipeline`). Each PR should include: a one-paragraph summary, bullet list of major changes, test evidence (`pytest` output or screenshots for UI clients), and any follow-up TODOs. Link to tracking issues or design docs when adding new pipelines, and request review from both an LLM-harness owner and a data owner when the schema changes.

## Security & Configuration Tips
Treat `banished.db` as read-only; create derived files under `data/tmp/` and add them to `.gitignore`. Do not commit API keys—load provider credentials from environment variables or `.env` files ignored by git. When introducing new configuration knobs, document the default in `README.md` and supply a safe fallback so the CLI can start without secrets.
