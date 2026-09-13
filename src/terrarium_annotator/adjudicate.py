"""Bounded adjudication pass: quote-audit shadow-flagged candidates.

Design: docs/worklog/2026-09-12-full-run-stocktake.md rec #1. The shadow
gate flagged 239 candidates in the full-run DB; this module samples them
(stratified by class and thread), drives chunked researcher sessions under
the ADJUDICATION_TOOLS allowlist (read/audit + propose_demotion ONLY), and
records per-chunk reports. Verdicts: TEXTURE verdicts land in demote_queue
(human-applied); VALID verdicts are recorded by absence plus the researcher's
chunk report. This is precision measurement, not cleanup: borderline = keep.

Entry point: `terrarium-annotator adjudicate` (CLI). The stock `research`
command is structurally separate — it keeps RESEARCHER_TOOLS and must not
serve adjudication sessions.
"""

from __future__ import annotations

import filecmp
import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import GlossaryStore
from terrarium_annotator.llm import ChatClient, ChatClientError
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.quota import QuotaExceeded, QuotaProbeError
from terrarium_annotator.research import Researcher
from terrarium_annotator.tools import ADJUDICATION_TOOLS


def backup_db(db_path: str | Path, backup_dir: str | Path = "data/backups") -> Path:
    """Byte-verified copy of the annotator DB before any adjudication
    write. Raises RuntimeError (and removes the copy) if verification
    fails — the pass must not run against an unbacked-up database.
    Safe here because no annotator run is live; never copy a live DB."""
    src = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"{src.stem}-{datetime.now(UTC):%Y%m%dT%H%M%S}.db"
    shutil.copy2(src, dest)
    if not filecmp.cmp(src, dest, shallow=False):
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"backup verification failed: {dest}")
    return dest


ADJUDICATION_SYSTEM_PROMPT = """You are the terrarium adjudicator: a quote-auditor, not an editor. You are given a sample of glossary entries that a lexical shadow-gate flagged as generic-suspect. Your ONLY job: decide, from corpus evidence, whether each entry is a real setting referent (VALID) or mere texture/scenery (TEXTURE).

For each candidate:
1. Read the stored quote and its post (fetch_post) — and surrounding posts (fetch_thread_range) when the quote alone doesn't settle it. search_corpus for other uses of the term when helpful.
2. Verdict VALID when the term is a named/identifiable setting referent (character, place, mechanic, faction, item with story weight) or carries setting-specific meaning — even if minor. Borderline = VALID.
3. Verdict TEXTURE only when the "entry" is generic scenery, an unnamed background extra, or a common word used in its ordinary sense. File propose_demotion with a rationale and a verbatim quote mentioning the term (typically the stored quote or a better one you found).

Rules:
- You CANNOT edit, rename, merge, or create entries — your tools are read/audit plus propose_demotion. Do not attempt other writes.
- When in doubt, KEEP: this is a precision measurement of the flag heuristic, not a cleanup pass.
- NEVER announce intentions — audit with tool calls immediately.
- End with a per-candidate verdict list: one line each, `TERM: VALID` or `TERM: TEXTURE (filed)` plus five words of why."""


@dataclass(frozen=True)
class Candidate:
    """One shadow-flagged candidate joined to its entry (when present)."""

    candidate_id: int
    term: str  # as flagged
    quote: str
    post_id: int
    thread_id: int | None
    entry_id: int | None = None
    entry_term: str | None = None  # canonical (may differ post-rename)
    gloss: str | None = None
    tags: tuple[str, ...] = ()


def load_flagged_candidates(conn: sqlite3.Connection) -> list[Candidate]:
    """All deferred_candidate rows joined to their entries (direct or via
    alias — the researcher may have retitled since the flag)."""
    rows = conn.execute(
        "SELECT d.id, d.term, d.term_normalized, d.quote, d.post_id,"
        " d.thread_id, e.id, e.term, e.gloss "
        "FROM deferred_candidate d "
        "LEFT JOIN entry e ON e.term_normalized = d.term_normalized "
        "ORDER BY d.id"
    ).fetchall()
    out: list[Candidate] = []
    for cid, term, norm, quote, post_id, thread_id, eid, eterm, gloss in rows:
        if eid is None:
            alias_row = conn.execute(
                "SELECT a.entry_id, e.term, e.gloss FROM entry_alias a "
                "JOIN entry e ON e.id = a.entry_id "
                "WHERE a.alias_normalized = ?",
                (norm,),
            ).fetchone()
            if alias_row is not None:
                eid, eterm, gloss = alias_row
        tags: tuple[str, ...] = ()
        if eid is not None:
            tags = tuple(
                r[0]
                for r in conn.execute(
                    "SELECT tag FROM entry_tag WHERE entry_id = ? ORDER BY tag",
                    (eid,),
                )
            )
        out.append(
            Candidate(
                candidate_id=cid,
                term=term,
                quote=quote,
                post_id=post_id,
                thread_id=thread_id,
                entry_id=eid,
                entry_term=eterm,
                gloss=gloss,
                tags=tags,
            )
        )
    return out


def _stratum_key(c: Candidate) -> str:
    return c.tags[0] if c.tags else "untagged"


def stratified_sample(candidates: list[Candidate], size: int) -> list[Candidate]:
    """Proportional-by-class allocation (largest remainder, min 1 per
    class), evenly spaced over (thread, id) within each class — covers
    classes AND threads; deterministic, never first-N, never random."""
    strata: dict[str, list[Candidate]] = {}
    for c in candidates:
        strata.setdefault(_stratum_key(c), []).append(c)
    for members in strata.values():
        members.sort(key=lambda c: (c.thread_id or 0, c.candidate_id))

    total = len(candidates)
    size = min(size, total)
    # Largest-remainder proportional allocation, min 1 per stratum.
    quotas = {k: max(1, size * len(m) / total) for k, m in strata.items()}
    floors = {k: min(int(q), len(strata[k])) for k, q in quotas.items()}
    remainder = size - sum(floors.values())
    by_frac = sorted(
        quotas,
        key=lambda k: (quotas[k] - floors[k], len(strata[k])),
        reverse=True,
    )
    alloc = dict(floors)
    for k in by_frac:
        if remainder <= 0:
            break
        if alloc[k] < len(strata[k]):
            alloc[k] += 1
            remainder -= 1

    sample: list[Candidate] = []
    for key in sorted(strata):
        members = strata[key]
        n = alloc[key]
        if n >= len(members):
            sample.extend(members)
            continue
        # Evenly spaced indices over the (thread, id)-sorted members.
        step = len(members) / n
        sample.extend(members[int(i * step)] for i in range(n))
    sample.sort(key=lambda c: c.candidate_id)
    return sample


def chunk_candidates(sample: list[Candidate], chunk_size: int) -> list[list[Candidate]]:
    return [sample[i : i + chunk_size] for i in range(0, len(sample), chunk_size)]


def chunk_focus(chunk: list[Candidate]) -> str:
    """The per-chunk user message: candidates with stored quotes and IDs."""
    lines = [
        (
            "Quote-audit these shadow-flagged candidates. For each: VALID (keep) "
            "or TEXTURE (file propose_demotion with rationale + verbatim quote)."
        ),
        "",
    ]
    for c in chunk:
        entry = c.entry_term or c.term
        lines.append(f"### {entry} (candidate #{c.candidate_id})")
        if c.tags:
            lines.append(f"tags: {', '.join(c.tags)}")
        if c.gloss:
            lines.append(f"gloss: {c.gloss}")
        lines.append(f"stored quote (post {c.post_id}, thread {c.thread_id}):")
        lines.append(f'"{c.quote}"')
        lines.append("")
    return "\n".join(lines)


@dataclass
class AdjudicationResult:
    """What a pass did: per-chunk report files and the demotion queue."""

    chunks_completed: int
    chunks_failed: int
    reports: list[Path] = field(default_factory=list)
    halted: str | None = None  # halt reason (quota, failures), if any


def run_adjudication(
    corpus: CorpusReader,
    conn: sqlite3.Connection,
    client: ChatClient,
    sample: list[Candidate],
    work_dir: Path,
    *,
    chunk_size: int = 12,
    quota_check=None,
    max_consecutive_failures: int = 3,
) -> AdjudicationResult:
    """One researcher session per chunk under ADJUDICATION_TOOLS. Chunk
    reports land in work_dir; a 3-failure streak halts the pass."""
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "sample.json").write_text(
        json.dumps([c.__dict__ for c in sample], indent=1)
    )
    glossary = GlossaryStore(conn, corpus.post_body)
    result = AdjudicationResult(chunks_completed=0, chunks_failed=0)
    failures = 0
    for i, chunk in enumerate(chunk_candidates(sample, chunk_size)):
        researcher = Researcher(
            corpus,
            glossary,
            StoryLog(conn),
            client,
            conn,
            pass_id="adjudicate",
            allowed=ADJUDICATION_TOOLS,
            system_prompt=ADJUDICATION_SYSTEM_PROMPT,
            quota_check=quota_check,
        )
        try:
            report = researcher.research(focus=chunk_focus(chunk), overview="")
        except (QuotaExceeded, QuotaProbeError) as exc:
            result.halted = f"quota breaker: {exc}"
            break
        except ChatClientError as exc:
            failures += 1
            result.chunks_failed += 1
            (work_dir / f"chunk-{i:02d}.error.txt").write_text(
                f"{type(exc).__name__}: {exc}"
            )
            if failures >= max_consecutive_failures:
                result.halted = f"{failures} consecutive chunk failures"
                break
            continue
        failures = 0
        result.chunks_completed += 1
        report_path = work_dir / f"chunk-{i:02d}.md"
        report_path.write_text(report)
        result.reports.append(report_path)
    return result
