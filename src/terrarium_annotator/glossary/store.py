"""Glossary store: entries, append-only revisions, evidence, quote gate.

Design: docs/design/v2-architecture.md §3-4, §7.

The quote gate is the primary defense against overzealous extraction
(v1's failure mode): every write requires evidence quotes that are
*verbatim substrings of the cited post* AND contain the term or a
registered alias (case-sensitive). No quote, no entry.

Corpus access is injected as `post_body(post_id) -> str | None` so the
store stays testable without a real corpus (wire `CorpusReader.post_body`
in production).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from terrarium_annotator.glossary.models import Entry, Evidence, Provenance, Revision

MAX_QUOTE_CHARS = 2000  # quotes are evidence snippets, not whole posts

SCHEMA = """
CREATE TABLE IF NOT EXISTS entry(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    term_normalized TEXT NOT NULL UNIQUE,
    gloss TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'tentative'
        CHECK (status IN ('tentative', 'confirmed')),
    pass_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entry_alias(
    entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    PRIMARY KEY (entry_id, alias_normalized)
);
CREATE INDEX IF NOT EXISTS idx_alias_norm ON entry_alias(alias_normalized);
CREATE TABLE IF NOT EXISTS entry_tag(
    entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (entry_id, tag)
);
CREATE TABLE IF NOT EXISTS revision(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entry(id),
    gloss TEXT NOT NULL,
    thread_id INTEGER,
    batch_lo INTEGER,
    batch_hi INTEGER,
    log_seq INTEGER,
    pass_id TEXT NOT NULL,
    tree_version INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entry_source(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
    revision_id INTEGER REFERENCES revision(id),
    thread_id INTEGER,
    post_id INTEGER NOT NULL,
    quote TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS entry_fts USING fts5(
    term, gloss, content=entry, content_rowid=id
);
"""


class GlossaryError(Exception):
    """Base for glossary store failures."""


class QuoteRejected(GlossaryError):
    """Evidence quote failed the gate (not verbatim, or term absent)."""


class DuplicateEntry(GlossaryError):
    """Term or alias collides with an existing entry."""


class UnknownEntry(GlossaryError):
    """No entry with that term/id."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text: str) -> str:
    return text.strip().casefold()


class GlossaryStore:
    """Read-write glossary database with a verified-evidence write path."""

    def __init__(
        self,
        db: Path | str | sqlite3.Connection,
        post_body: Callable[[int], str | None],
    ) -> None:
        # Accept a shared connection (architecture §7: one annotator.db);
        # close() is a no-op for connections we do not own.
        self._owns = not isinstance(db, sqlite3.Connection)
        self._conn = db if isinstance(db, sqlite3.Connection) else sqlite3.connect(db)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._post_body = post_body

    # ----------------------------------------------------- quote gate

    def _check_evidence(
        self, term: str, aliases: tuple[str, ...], evidence: list[Evidence]
    ) -> None:
        if not evidence:
            raise QuoteRejected("at least one evidence quote is required")
        needles = (term, *aliases)
        for ev in evidence:
            quote = ev.quote
            if not quote.strip() or len(quote) > MAX_QUOTE_CHARS:
                raise QuoteRejected(
                    f"quote empty or over {MAX_QUOTE_CHARS} chars (post {ev.post_id})"
                )
            body = self._post_body(ev.post_id)
            if body is None:
                raise QuoteRejected(f"post {ev.post_id} not in corpus")
            if quote not in body:
                raise QuoteRejected(f"quote is not verbatim in post {ev.post_id}")
            if not any(n in quote for n in needles):
                raise QuoteRejected(
                    f"quote contains neither term nor alias (post {ev.post_id})"
                )

    # -------------------------------------------------------- lookups

    def _row_to_entry(self, row: tuple) -> Entry:
        entry_id = row[0]
        tags = tuple(
            r[0]
            for r in self._conn.execute(
                "SELECT tag FROM entry_tag WHERE entry_id = ? ORDER BY tag",
                (entry_id,),
            )
        )
        aliases = tuple(
            r[0]
            for r in self._conn.execute(
                "SELECT alias FROM entry_alias WHERE entry_id = ? ORDER BY alias",
                (entry_id,),
            )
        )
        return Entry(
            id=entry_id,
            term=row[1],
            gloss=row[2],
            status=row[3],
            tags=tags,
            aliases=aliases,
            created_at=row[4],
            updated_at=row[5],
        )

    def get(self, term_or_id: str | int) -> Entry:
        if isinstance(term_or_id, int):
            row = self._conn.execute(
                "SELECT id, term, gloss, status, created_at, updated_at "
                "FROM entry WHERE id = ?",
                (term_or_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id, term, gloss, status, created_at, updated_at "
                "FROM entry WHERE term_normalized = ?",
                (_norm(term_or_id),),
            ).fetchone()
        if row is None:
            raise UnknownEntry(f"no entry {term_or_id!r}")
        return self._row_to_entry(row)

    def find(self, term_or_alias: str) -> Entry | None:
        """Resolve any surface form (term or alias) to its entry."""
        n = _norm(term_or_alias)
        row = self._conn.execute(
            "SELECT id, term, gloss, status, created_at, updated_at "
            "FROM entry WHERE term_normalized = ?",
            (n,),
        ).fetchone()
        if row is None:
            row = self._conn.execute(
                "SELECT e.id, e.term, e.gloss, e.status, e.created_at, "
                "e.updated_at FROM entry e JOIN entry_alias a "
                "ON a.entry_id = e.id WHERE a.alias_normalized = ?",
                (n,),
            ).fetchone()
        return self._row_to_entry(row) if row else None

    def search(self, query: str, limit: int = 25) -> list[Entry]:
        """FTS5 search over term + gloss."""
        rows = self._conn.execute(
            "SELECT e.id, e.term, e.gloss, e.status, e.created_at, "
            "e.updated_at FROM entry e JOIN entry_fts f ON f.rowid = e.id "
            "WHERE entry_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        )
        return [self._row_to_entry(r) for r in rows]

    def revisions(self, entry_id: int) -> list[Revision]:
        rows = self._conn.execute(
            "SELECT id, entry_id, gloss, thread_id, batch_lo, batch_hi, "
            "log_seq, pass_id, tree_version, created_at FROM revision "
            "WHERE entry_id = ? ORDER BY id",
            (entry_id,),
        )
        return [
            Revision(
                id=r[0],
                entry_id=r[1],
                gloss=r[2],
                provenance=Provenance(
                    thread_id=r[3],
                    batch_lo=r[4],
                    batch_hi=r[5],
                    log_seq=r[6],
                    pass_id=r[7],
                    tree_version=r[8],
                ),
                created_at=r[9],
            )
            for r in rows
        ]

    # -------------------------------------------------------- writes

    def _check_surface_free(self, key: str, allow_entry_id: int | None = None) -> None:
        """Reject if `key` is another entry's term or registered alias."""
        kn = _norm(key)
        row = self._conn.execute(
            "SELECT id FROM entry WHERE term_normalized = ?", (kn,)
        ).fetchone()
        if row and row[0] != allow_entry_id:
            raise DuplicateEntry(f"surface form {key!r} is entry #{row[0]}'s term")
        row = self._conn.execute(
            "SELECT entry_id FROM entry_alias WHERE alias_normalized = ?", (kn,)
        ).fetchone()
        if row and row[0] != allow_entry_id:
            raise DuplicateEntry(f"surface form {key!r} is entry #{row[0]}'s alias")

    def _check_collisions(self, term: str, keys: tuple[str, ...]) -> None:
        """For propose: term and all keys must be unclaimed surface forms."""
        self._check_surface_free(term)
        for key in keys:
            self._check_surface_free(key)

    def propose_entry(
        self,
        *,
        term: str,
        gloss: str,
        evidence: list[Evidence],
        provenance: Provenance,
        tags: tuple[str, ...] = (),
        keys: tuple[str, ...] = (),
    ) -> Entry:
        """Create an entry (status tentative) after the quote gate."""
        term = term.strip()
        gloss = gloss.strip()
        if not term or not gloss:
            raise GlossaryError("term and gloss must be non-empty")
        self._check_collisions(term, keys)
        self._check_evidence(term, keys, evidence)

        now = _now()
        cur = self._conn.execute(
            "INSERT INTO entry(term, term_normalized, gloss, status, pass_id,"
            " created_at, updated_at) VALUES (?, ?, ?, 'tentative', ?, ?, ?)",
            (term, _norm(term), gloss, provenance.pass_id, now, now),
        )
        entry_id = cur.lastrowid
        assert entry_id is not None
        for tag in tags:
            self._conn.execute("INSERT INTO entry_tag VALUES (?, ?)", (entry_id, tag))
        for key in keys:
            self._conn.execute(
                "INSERT INTO entry_alias VALUES (?, ?, ?)",
                (entry_id, key, _norm(key)),
            )
        revision_id = self._insert_revision(entry_id, gloss, provenance, now)
        self._insert_sources(entry_id, revision_id, evidence, provenance, now)
        self._conn.execute(
            "INSERT INTO entry_fts(rowid, term, gloss) VALUES (?, ?, ?)",
            (entry_id, term, gloss),
        )
        self._conn.commit()
        return self.get(entry_id)

    def update_entry(
        self,
        term_or_id: str | int,
        *,
        gloss: str,
        evidence: list[Evidence],
        provenance: Provenance,
    ) -> Revision:
        """Append a revision; the card gloss becomes the latest revision."""
        entry = self.get(term_or_id)
        gloss = gloss.strip()
        if not gloss:
            raise GlossaryError("gloss must be non-empty")
        self._check_evidence(entry.term, entry.aliases, evidence)

        now = _now()
        revision_id = self._insert_revision(entry.id, gloss, provenance, now)
        self._insert_sources(entry.id, revision_id, evidence, provenance, now)
        self._conn.execute(
            "UPDATE entry SET gloss = ?, updated_at = ? WHERE id = ?",
            (gloss, now, entry.id),
        )
        self._conn.execute(
            "INSERT INTO entry_fts(entry_fts, rowid, term, gloss) "
            "VALUES ('delete', ?, ?, ?)",
            (entry.id, entry.term, entry.gloss),
        )
        self._conn.execute(
            "INSERT INTO entry_fts(rowid, term, gloss) VALUES (?, ?, ?)",
            (entry.id, entry.term, gloss),
        )
        self._conn.commit()
        return self.revisions(entry.id)[-1]

    def add_alias(
        self, term_or_id: str | int, alias: str, *, evidence: Evidence
    ) -> None:
        """Register a surface form. Same gate as definition writes: a
        verbatim quote containing the alias, recorded to entry_source
        (revision_id NULL — alias registration is not a definition change)."""
        entry = self.get(term_or_id)
        alias = alias.strip()
        if not alias:
            raise GlossaryError("alias must be non-empty")
        self._check_surface_free(alias, allow_entry_id=entry.id)
        body = self._post_body(evidence.post_id)
        if body is None:
            raise QuoteRejected(f"post {evidence.post_id} not in corpus")
        if evidence.quote not in body:
            raise QuoteRejected(f"quote is not verbatim in post {evidence.post_id}")
        if alias not in evidence.quote:
            raise QuoteRejected(f"quote does not contain alias {alias!r}")
        self._conn.execute(
            "INSERT INTO entry_alias VALUES (?, ?, ?)",
            (entry.id, alias, _norm(alias)),
        )
        self._conn.execute(
            "INSERT INTO entry_source(entry_id, revision_id, thread_id,"
            " post_id, quote, created_at) VALUES (?, NULL, NULL, ?, ?, ?)",
            (entry.id, evidence.post_id, evidence.quote, _now()),
        )
        self._conn.commit()

    def confirm(self, term_or_id: str | int) -> None:
        """Promote tentative -> confirmed. Human/audit action, not agent."""
        entry = self.get(term_or_id)
        self._conn.execute(
            "UPDATE entry SET status = 'confirmed', updated_at = ? WHERE id = ?",
            (_now(), entry.id),
        )
        self._conn.commit()

    def merge_entries(self, survivor: str | int, merged: str | int) -> Entry:
        """Human-invoked merge: all aliases, sources, and revisions of the
        merged entry move to the survivor (evidence is unioned, never
        discarded); the merged entry row is removed."""
        keep = self.get(survivor)
        drop = self.get(merged)
        if keep.id == drop.id:
            raise GlossaryError("cannot merge an entry into itself")
        self._conn.execute(
            "UPDATE entry_alias SET entry_id = ? WHERE entry_id = ?",
            (keep.id, drop.id),
        )
        self._conn.execute(
            "UPDATE entry_source SET entry_id = ? WHERE entry_id = ?",
            (keep.id, drop.id),
        )
        self._conn.execute(
            "UPDATE revision SET entry_id = ? WHERE entry_id = ?",
            (keep.id, drop.id),
        )
        self._conn.execute(
            "INSERT INTO entry_fts(entry_fts, rowid, term, gloss) "
            "VALUES ('delete', ?, ?, ?)",
            (drop.id, drop.term, drop.gloss),
        )
        self._conn.execute("DELETE FROM entry WHERE id = ?", (drop.id,))
        self._conn.commit()
        return self.get(keep.id)

    # ------------------------------------------------------ internals

    def _insert_revision(
        self, entry_id: int, gloss: str, prov: Provenance, now: str
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO revision(entry_id, gloss, thread_id, batch_lo,"
            " batch_hi, log_seq, pass_id, tree_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                gloss,
                prov.thread_id,
                prov.batch_lo,
                prov.batch_hi,
                prov.log_seq,
                prov.pass_id,
                prov.tree_version,
                now,
            ),
        )
        revision_id = cur.lastrowid
        assert revision_id is not None
        return revision_id

    def _insert_sources(
        self,
        entry_id: int,
        revision_id: int,
        evidence: list[Evidence],
        prov: Provenance,
        now: str,
    ) -> None:
        for ev in evidence:
            self._conn.execute(
                "INSERT INTO entry_source(entry_id, revision_id, thread_id,"
                " post_id, quote, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (entry_id, revision_id, prov.thread_id, ev.post_id, ev.quote, now),
            )

    def close(self) -> None:
        if self._owns:
            self._conn.close()

    def __enter__(self) -> GlossaryStore:  # noqa: PYI034 — Self needs 3.11; floor is 3.10
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
