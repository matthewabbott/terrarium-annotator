# Agent Onboarding

Start here when beginning work on terrarium-annotator.

## 1. Orientation Reading (in order)

1. **SPEC.md** (root) - Understand what we're building and why
2. **docs/ARCHITECTURE.md** - Component structure and data flow
3. **docs/ROADMAP.md** - Current progress and what's next
4. **docs/worklog/** - Recent work sessions (newest first)

## 2. Current State Checklist

Before starting work, verify:

- [ ] Check `docs/ROADMAP.md` for current feature in progress
- [ ] Read latest worklog entry in `docs/worklog/`
- [ ] Check for open questions in previous worklog
- [ ] Verify `data/annotator.db` exists (or needs creation)
- [ ] Verify terrarium-agent is reachable at configured URL

## 3. Codebase Orientation

### Key Entry Points
- `src/terrarium_annotator/cli.py` - CLI commands
- `src/terrarium_annotator/runner.py` - Main orchestration loop
- `banished.db` - Read-only corpus (DO NOT MODIFY)

### Storage Locations
- `data/annotator.db` - Glossary, snapshots, state (created at runtime)
- `data/exports/` - JSON/YAML exports
- `docs/worklog/` - Your notes for next agent

## 4. Before You Start Coding

1. Create a new worklog file: `docs/worklog/YYYY-MM-DD-brief-description.md`
2. Record your objective and understanding
3. Check `docs/INTERFACES.md` for the component you're modifying
4. Review relevant ADRs in `docs/adr/`

## 5. Before You Hand Off

1. Update your worklog with:
   - What you accomplished
   - Decisions you made (and why)
   - Open questions
   - Suggested next steps
2. Update `docs/ROADMAP.md` if you completed a feature
3. Run tests and note any failures

## 6. Style and Standards

See `AGENTS.md` for coding standards. Key points:
- Keep `banished.db` untouched
- All code in `src/terrarium_annotator/`
- Type hints on public functions
- Tests mirror source structure in `tests/`
