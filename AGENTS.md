# Repository Guidelines

## Project Structure & Module Organization
The repository contains the read-only reference corpus `banished.db` and usage notes in `how to use banished.db.txt`; keep these untouched so runs remain reproducible. Place all runnable code inside `src/terrarium_annotator/` with clear module boundaries (e.g., `glossary/`, `memory/`, `corpus/`, `cli.py`) so imports stay stable. Mirror every module with a test file under `tests/` (for instance, `tests/test_glossary.py`). Run artifacts live in `data/` (gitignored); small, durable exports belong in `data/exports/`.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` — isolate dependencies.
- `pip install -e '.[dev]'` — install the package with test/lint tooling.
- `python -m pytest tests -q` — run the test suite; add `-k name` to target a component.
- `ruff check src tests` and `ruff format src tests` — lint and format.

## Coding Style & Naming Conventions
Follow PEP 8 with 4-space indentation, 88-character lines, and type hints on all public functions. Prefer descriptive module names (`glossary_store.py`, `story_log.py`) and snake_case for variables/functions; reserve PascalCase for classes and TypedDicts. Keep docstrings short but precise, documenting parameters that influence LLM context size. Run `ruff` before every push; do not mix unrelated stylistic and behavioral changes in one PR.

## Testing Guidelines
Use `pytest` with fixtures that fabricate minimal SQLite snapshots instead of mutating `banished.db`. Name tests after the behavior under test (`test_card_injection_respects_token_budget`). Prioritize coverage of the quality guards: quote verification at write time, injection budget enforcement, merge/tree correctness, and CLI argument parsing.

## Commit & Pull Request Guidelines
Write imperative, concise commit subjects (`Add story log merge tree`). Each PR should include: a one-paragraph summary, bullet list of major changes, test evidence (`pytest` output), and any follow-up TODOs. Request review when the schema or the write-path guards change.

## Security & Configuration Tips
Treat `banished.db` as read-only; create derived files under `data/` (gitignored). Do not commit API keys—load provider credentials from environment variables or `.env` files ignored by git. New configuration knobs need a documented default and a safe fallback so the CLI starts without secrets.

## Agent Workflow

This section guides AI agents working on the codebase across context windows.

### Starting a Session

1. Read `SPEC.md` — what we're building and why
2. Read `docs/design/v2-architecture.md` — the current design (memory model, two-tier glossary, provenance, verification)
3. Skim recent `docs/worklog/` entries for session context
4. Create your own worklog file before coding: `docs/worklog/YYYY-MM-DD-brief-description.md`

### During Development

- **Modularity first**: each module has a single clear purpose
- **Follow the design doc**: deviating from `docs/design/v2-architecture.md` is fine, but update the doc in the same change and say why
- **Document decisions**: non-obvious choices go in your worklog
- **Test as you go**: mirror source structure in `tests/`

### Ending a Session

1. Update your worklog with what you accomplished
2. Note open questions or blockers for the next agent
3. Suggest concrete next steps
4. Run tests and document results

### Key Principles

1. **Future agents will read your code**: write for clarity, not cleverness
2. **Context is precious**: document *why*, not just *what*
3. **Small commits**: each a logical unit with a clear purpose
4. **Don't break the build**: run tests before finishing
5. **Leave breadcrumbs**: your worklog entry is a gift to the next agent

### Documentation Hierarchy

1. `SPEC.md` — what we are building and why
2. `docs/design/v2-architecture.md` — how v2 fits together (the load-bearing doc)
3. `docs/design/research-memory-rag.md` — evidence base for design choices
4. `docs/design/dev-verification.md` — test layers for agent-driven development (L0–L5)
5. `docs/design/wiki-format.md` — target wiki page conventions (from the live steelbea.me wiki)
6. `docs/design/context-improvements.md` — pre-cutover design notes (historical, still useful)
7. `docs/worklog/` — what happened recently
