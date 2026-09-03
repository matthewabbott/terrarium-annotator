"""Story memory: append-only gist log + lazy binary merge tree.

OptMem's algorithm (github.com/VictorTaelin/OptMem) reimplemented in-process
per docs/design/v2-architecture.md §1-2, with three deliberate deviations:

1. Blocks are aligned powers of two over the global log (as OptMem), but a
   block is *eligible to settle* only when every entry in it belongs to a
   closed thread — so merges never touch the thread currently being read.
   A block straddling a thread boundary waits for both threads to close.
2. Settlement is strictly in order (smallest pending block first), like
   OptMem's nap; the merge function itself is injected by the caller.
3. `cover()` falls back to raw log entries when a needed summary is
   unsettled, rather than refusing (OptMem's wake behavior). The digest is
   always renderable; budget is only guaranteed when the tree is settled.

Storage is SQLite. `tree_version` increments on `forget`; revisions record
it for rehydration diagnostics (design doc §4).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS story_log(
    seq INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL,
    batch_lo INTEGER,
    batch_hi INTEGER,
    gist TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_status(
    thread_id INTEGER PRIMARY KEY,
    closed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS story_tree(
    lo INTEGER NOT NULL,
    hi INTEGER NOT NULL,
    summary TEXT NOT NULL,
    tree_version INTEGER NOT NULL,
    PRIMARY KEY(lo, hi)
);
CREATE TABLE IF NOT EXISTS tree_meta(
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class LogEntry:
    """One gist in the story log. Position (seq) is identity."""

    seq: int
    thread_id: int
    batch_lo: int | None
    batch_hi: int | None
    gist: str
    created_at: str


@dataclass(frozen=True)
class DigestItem:
    """One line of the budgeted digest: a raw entry or a settled summary."""

    lo: int  # inclusive
    hi: int  # exclusive
    kind: str  # "raw" | "summary"
    text: str


class StoryLog:
    """Append-only story log with lazy merge tree over an SQLite store."""

    def __init__(self, db: Path | str | sqlite3.Connection = ":memory:") -> None:
        # Accept a shared connection so story log, glossary, and run state
        # live in one annotator.db (architecture §7); close() is a no-op
        # for connections we do not own.
        self._owns = not isinstance(db, sqlite3.Connection)
        self._conn = db if isinstance(db, sqlite3.Connection) else sqlite3.connect(db)
        self._conn.executescript(SCHEMA)
        self._conn.execute("INSERT OR IGNORE INTO tree_meta VALUES ('tree_version', 0)")
        self._conn.commit()

    # ------------------------------------------------------------- log

    def append(
        self,
        thread_id: int,
        gist: str,
        batch_lo: int | None = None,
        batch_hi: int | None = None,
    ) -> int:
        """Append one gist; returns its seq. The only way the log grows."""
        gist = gist.strip()
        if not gist or "\n" in gist:
            raise ValueError("a gist is one non-empty line")
        seq = self.log_len()
        self._conn.execute(
            "INSERT INTO story_log VALUES (?, ?, ?, ?, ?, ?)",
            (
                seq,
                thread_id,
                batch_lo,
                batch_hi,
                gist,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO thread_status VALUES (?, 0)", (thread_id,)
        )
        self._conn.commit()
        return seq

    def log_len(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM story_log").fetchone()[0]

    def entry(self, seq: int) -> LogEntry:
        rows = self.slice(seq, seq + 1)
        if not rows:
            raise IndexError(f"no log entry #{seq}")
        return rows[0]

    def slice(self, lo: int, hi: int) -> list[LogEntry]:
        rows = self._conn.execute(
            "SELECT seq, thread_id, batch_lo, batch_hi, gist, created_at "
            "FROM story_log WHERE seq >= ? AND seq < ? ORDER BY seq",
            (lo, hi),
        )
        return [LogEntry(*r) for r in rows]

    # --------------------------------------------------------- threads

    def close_thread(self, thread_id: int) -> None:
        """Mark a thread closed; blocks over its entries become settleable."""
        self._conn.execute(
            "INSERT INTO thread_status VALUES (?, 1) "
            "ON CONFLICT(thread_id) DO UPDATE SET closed = 1",
            (thread_id,),
        )
        self._conn.commit()

    def _all_closed(self, lo: int, hi: int) -> bool:
        (open_count,) = self._conn.execute(
            "SELECT COUNT(*) FROM story_log s "
            "JOIN thread_status t ON t.thread_id = s.thread_id "
            "WHERE s.seq >= ? AND s.seq < ? AND t.closed = 0",
            (lo, hi),
        ).fetchone()
        return open_count == 0

    # --------------------------------------------------------- tree

    @property
    def tree_version(self) -> int:
        return self._conn.execute(
            "SELECT value FROM tree_meta WHERE key = 'tree_version'"
        ).fetchone()[0]

    def _settled(self, lo: int, hi: int) -> str | None:
        row = self._conn.execute(
            "SELECT summary FROM story_tree WHERE lo = ? AND hi = ?", (lo, hi)
        ).fetchone()
        return row[0] if row else None

    def pending(self) -> list[tuple[int, int]]:
        """Settleable blocks, smallest first. A block is settleable when it
        is aligned, fully written, unsettled, and all its entries belong to
        closed threads."""
        T = self.log_len()
        out: list[tuple[int, int]] = []
        size = 2
        while size <= T:
            for k in range(T // size):
                lo, hi = k * size, (k + 1) * size
                if self._settled(lo, hi) is None and self._all_closed(lo, hi):
                    out.append((lo, hi))
            size *= 2
        return out

    def settle(self, lo: int, hi: int, summary: str) -> None:
        """Record a block summary. Must be the first pending block (strict
        in-order settlement, as OptMem's nap)."""
        summary = summary.strip()
        if not summary or "\n" in summary:
            raise ValueError("a summary is one non-empty line")
        todo = self.pending()
        if not todo:
            raise ValueError("nothing to settle")
        if (lo, hi) != todo[0]:
            raise ValueError(
                f"blocks settle in order; next is {todo[0][0]}-{todo[0][1]}"
            )
        self._conn.execute(
            "INSERT INTO story_tree VALUES (?, ?, ?, ?)",
            (lo, hi, summary, self.tree_version),
        )
        self._conn.commit()

    def forget(self, lo: int, hi: int) -> list[tuple[int, int]]:
        """Drop a block's summary, its ancestors, and later blocks at those
        levels (OptMem's truncation semantics). The log is never touched;
        `pending()` will offer rebuilds. Bumps tree_version."""
        size = hi - lo
        if size < 2 or size & (size - 1) or lo % size:
            raise ValueError(f"{lo}-{hi} is not an aligned power-of-two block")
        dropped: list[tuple[int, int]] = []
        while size <= self.log_len():
            base = (lo // size) * size
            rows = self._conn.execute(
                "SELECT lo, hi FROM story_tree WHERE hi - lo = ? AND lo >= ?",
                (size, base),
            ).fetchall()
            dropped.extend((r[0], r[1]) for r in rows)
            self._conn.execute(
                "DELETE FROM story_tree WHERE hi - lo = ? AND lo >= ?",
                (size, base),
            )
            size *= 2
        if not dropped:
            raise ValueError(f"no summary at {lo}-{hi}")
        self._conn.execute(
            "UPDATE tree_meta SET value = value + 1 WHERE key = 'tree_version'"
        )
        self._conn.commit()
        return dropped

    # --------------------------------------------------------- digest

    @staticmethod
    def _tile(T: int, alpha: float) -> list[tuple[int, int]]:
        """Tile [0,T) with aligned power-of-two blocks; keep a block whole
        iff its size <= alpha * its age. Ported from OptMem's _cover."""
        root = 1
        while root < T:
            root *= 2
        out, stack = [], [(0, root)]
        while stack:
            lo, hi = stack.pop()
            if lo >= T:
                continue
            size = hi - lo
            if size > 1 and (hi > T or size > alpha * (T - lo)):
                mid = (lo + hi) // 2
                stack.append((mid, hi))
                stack.append((lo, mid))
            else:
                out.append((lo, hi))
        out.sort()
        return out

    def cover_blocks(self, T: int, budget: int) -> list[tuple[int, int]]:
        """The tiling a digest of at most `budget` lines uses: verbatim near
        the present, coarse near the past. Ported from OptMem's cover."""
        if T <= 0:
            return []
        if T <= budget:
            return [(i, i + 1) for i in range(T)]
        lo_a, hi_a = 0.0, 1.0
        for _ in range(60):
            mid = (lo_a + hi_a) / 2
            if len(self._tile(T, mid)) > budget:
                lo_a = mid
            else:
                hi_a = mid
        out = self._tile(T, hi_a)
        # Spend leftover lines on the present, where detail is worth most.
        while len(out) < budget:
            i = max((i for i, b in enumerate(out) if b[1] - b[0] > 1), default=None)
            if i is None:
                break
            lo, hi = out[i]
            mid = (lo + hi) // 2
            out[i : i + 1] = [(lo, mid), (mid, hi)]
        return out

    def cover(self, budget: int) -> list[DigestItem]:
        """Budgeted digest, oldest first. Settled blocks render as summaries;
        unsettled blocks expand to raw entries (may exceed budget — the tree
        being settled is what guarantees the budget)."""
        if budget < 1:
            raise ValueError("budget must be >= 1")
        T = self.log_len()
        items: list[DigestItem] = []
        for lo, hi in self.cover_blocks(T, budget):
            if hi - lo == 1:
                items.append(DigestItem(lo, hi, "raw", self.entry(lo).gist))
                continue
            summary = self._settled(lo, hi)
            if summary is not None:
                items.append(DigestItem(lo, hi, "summary", summary))
            else:
                for e in self.slice(lo, hi):
                    items.append(DigestItem(e.seq, e.seq + 1, "raw", e.gist))
        return items

    # --------------------------------------------------------- misc

    def close(self) -> None:
        if self._owns:
            self._conn.close()

    def __enter__(self) -> StoryLog:  # noqa: PYI034 — Self needs 3.11; floor is 3.10
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
