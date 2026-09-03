"""Post-run invariant checker (`annotator verify`), per docs/plan T7.

Re-verifies every stored claim against the immutable corpus — the
machine-checkable half of quality (dev-verification L3's checker, and the
first metrics of the §6 dashboard). Returns violations, never raises on
bad data: bad data is the finding.

All checks share the signature (conn, corpus); corpus-free checks ignore
the second argument, keeping dispatch trivial.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from terrarium_annotator.corpus import CorpusReader


@dataclass(frozen=True)
class Violation:
    check: str
    detail: str


def check_quote_validity(
    conn: sqlite3.Connection, corpus: CorpusReader
) -> list[Violation]:
    """Every source quote is a verbatim substring of its cited post and
    contains the entry's term or a registered alias (case-sensitive)."""
    out: list[Violation] = []
    rows = conn.execute(
        "SELECT s.id, s.post_id, s.quote, e.term, e.id FROM entry_source s "
        "JOIN entry e ON e.id = s.entry_id"
    ).fetchall()
    for source_id, post_id, quote, term, entry_id in rows:
        aliases = [
            r[0]
            for r in conn.execute(
                "SELECT alias FROM entry_alias WHERE entry_id = ?", (entry_id,)
            )
        ]
        body = corpus.post_body(post_id)
        if body is None:
            out.append(
                Violation(
                    "quote-validity",
                    f"source {source_id}: post {post_id} not in corpus",
                )
            )
            continue
        if quote not in body:
            out.append(
                Violation(
                    "quote-validity",
                    f"source {source_id}: quote not verbatim in post {post_id}",
                )
            )
        elif not any(n in quote for n in (term, *aliases)):
            out.append(
                Violation(
                    "quote-validity",
                    f"source {source_id}: quote contains neither term nor alias",
                )
            )
    return out


def check_provenance_coverage(
    conn: sqlite3.Connection, corpus: CorpusReader
) -> list[Violation]:
    """Every entry has at least one source."""
    rows = conn.execute(
        "SELECT e.id, e.term FROM entry e "
        "LEFT JOIN entry_source s ON s.entry_id = e.id "
        "WHERE s.id IS NULL"
    ).fetchall()
    return [
        Violation("provenance-coverage", f"entry {r[0]} ({r[1]!r}) has no sources")
        for r in rows
    ]


def check_backlink_integrity(
    conn: sqlite3.Connection, corpus: CorpusReader
) -> list[Violation]:
    """Every cited post exists in the corpus."""
    out = []
    rows = conn.execute("SELECT DISTINCT post_id FROM entry_source").fetchall()
    for (post_id,) in rows:
        if corpus.post_body(post_id) is None:
            out.append(
                Violation("backlink-integrity", f"post {post_id} does not exist")
            )
    return out


def check_revision_links(
    conn: sqlite3.Connection, corpus: CorpusReader
) -> list[Violation]:
    """Non-null revision references point at a revision of the same entry."""
    rows = conn.execute(
        "SELECT s.id FROM entry_source s LEFT JOIN revision r "
        "ON r.id = s.revision_id AND r.entry_id = s.entry_id "
        "WHERE s.revision_id IS NOT NULL AND r.id IS NULL"
    ).fetchall()
    return [
        Violation("revision-links", f"source {r[0]}: dangling revision link")
        for r in rows
    ]


def check_story_tree(conn: sqlite3.Connection, corpus: CorpusReader) -> list[Violation]:
    """Tree blocks are aligned powers of two within the log."""
    out = []
    (log_len,) = conn.execute("SELECT COUNT(*) FROM story_log").fetchone()
    rows = conn.execute("SELECT lo, hi FROM story_tree").fetchall()
    for lo, hi in rows:
        size = hi - lo
        if size < 2 or size & (size - 1) or lo % size:
            out.append(
                Violation(
                    "story-tree", f"block {lo}-{hi} is not an aligned power of two"
                )
            )
        if hi > log_len:
            out.append(
                Violation("story-tree", f"block {lo}-{hi} exceeds log ({log_len})")
            )
    return out


def check_story_log(conn: sqlite3.Connection, corpus: CorpusReader) -> list[Violation]:
    """Gists are non-empty single lines."""
    rows = conn.execute(
        "SELECT seq FROM story_log WHERE gist = '' OR gist LIKE '%\n%'"
    ).fetchall()
    return [
        Violation("story-log", f"entry {r[0]}: empty or multi-line gist") for r in rows
    ]


def check_run_state(conn: sqlite3.Connection, corpus: CorpusReader) -> list[Violation]:
    """Checkpoint points at a real thread with a reachable batch index."""
    row = conn.execute(
        "SELECT thread_id, batch_index FROM run_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return [Violation("run-state", "no run_state row")]
    thread_id, batch_index = row
    threads = corpus.thread_order()
    matches = [t for t in threads if t.id == thread_id]
    if not matches:
        return [Violation("run-state", f"thread {thread_id} not in corpus")]
    batch_count = sum(1 for _ in corpus.batches(thread_id))
    if batch_index < 0:
        return [Violation("run-state", f"batch_index {batch_index} is negative")]
    if batch_index > batch_count:
        return [
            Violation(
                "run-state",
                f"batch_index {batch_index} past thread's {batch_count} batches",
            )
        ]
    return []


def check_budget_compliance(
    conn: sqlite3.Connection, corpus: CorpusReader
) -> list[Violation]:
    """Per recorded batch request: digest within the line budget and the
    injected glossary block within the token share, per the run's stored
    config (run_meta). Skipped when no requests recorded."""
    from terrarium_annotator.runner import count_tokens

    user_rows = conn.execute(
        "SELECT thread_id, batch_index, content FROM transcript WHERE role = 'user'"
    ).fetchall()
    if not user_rows:
        return []
    row = conn.execute("SELECT value FROM run_meta WHERE key = 'config'").fetchone()
    if row is None:
        return [Violation("budget-compliance", "requests recorded but no run config")]
    cfg = json.loads(row[0])
    digest_budget = cfg["digest_budget_lines"]
    card_budget = int(cfg["context_tokens"] * cfg["card_budget_fraction"])

    out: list[Violation] = []
    for thread_id, batch_index, content in user_rows:
        where = f"thread {thread_id} batch {batch_index}"
        digest = content.split("<story_so_far>")[1].split("</story_so_far>")[0]
        lines = len([ln for ln in digest.splitlines() if ln.strip()])
        if lines > digest_budget:
            out.append(
                Violation(
                    "budget-compliance",
                    f"{where}: digest {lines} lines > {digest_budget}",
                )
            )
        cards = content.split("<known_glossary>")[1].split("</known_glossary>")[0]
        if count_tokens(cards) > card_budget:
            out.append(
                Violation("budget-compliance", f"{where}: card block over token budget")
            )
    return out


ALL_CHECKS = [
    check_quote_validity,
    check_provenance_coverage,
    check_backlink_integrity,
    check_revision_links,
    check_story_tree,
    check_story_log,
    check_run_state,
    check_budget_compliance,
]


def verify(conn: sqlite3.Connection, corpus: CorpusReader) -> list[Violation]:
    """Run every invariant check; violations are findings, not exceptions."""
    out: list[Violation] = []
    for check in ALL_CHECKS:
        out.extend(check(conn, corpus))
    return out
