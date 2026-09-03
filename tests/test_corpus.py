"""L0 tests for the corpus reader, per docs/plan/v2-foundation.md T1.

Fixtures fabricate minimal corpora with the real schema; banished.db is
never touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from terrarium_annotator.corpus import CorpusReader

SCHEMA = """
CREATE TABLE thread(id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE post(thread_id INTEGER, id INTEGER PRIMARY KEY, name TEXT,
                  trip_code TEXT, subject TEXT, time INTEGER,
                  file_url TEXT, file_name TEXT, body TEXT);
CREATE TABLE link(link_from INTEGER, link_to INTEGER);
CREATE TABLE tag(post_id INTEGER, name TEXT);
"""


def make_corpus(path: Path) -> sqlite3.Connection:
    """Empty corpus with the banished.db schema. Caller inserts rows."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def add_thread(conn, thread_id, title, posts, op_index=0):
    """posts: list of (post_id, time, body, tags). op_index marks the OP post
    (gets an extra 'op_post' tag); None means the thread has no OP tag."""
    conn.execute("INSERT INTO thread VALUES (?, ?)", (thread_id, title))
    for i, (pid, time, body, tags) in enumerate(posts):
        conn.execute(
            "INSERT INTO post(thread_id, id, name, time, body) "
            "VALUES (?, ?, 'qm', ?, ?)",
            (thread_id, pid, time, body),
        )
        for tag in tags:
            conn.execute("INSERT INTO tag VALUES (?, ?)", (pid, tag))
        if i == op_index:
            conn.execute("INSERT INTO tag VALUES (?, 'op_post')", (pid,))
    conn.commit()


@pytest.fixture
def corpus_path(tmp_path):
    return tmp_path / "corpus.db"


class TestThreadOrder:
    def test_chronological_by_op_time_not_id(self, corpus_path):
        # Thread IDs deliberately anti-correlated with time (mixed sources).
        conn = make_corpus(corpus_path)
        add_thread(conn, 900, "late", [(900, 300, "c", ["story_post"])])
        add_thread(conn, 100, "early", [(100, 100, "a", ["story_post"])])
        add_thread(conn, 500, "middle", [(500, 200, "b", ["story_post"])])
        conn.close()

        with CorpusReader(corpus_path) as r:
            threads = r.thread_order()
        assert [t.id for t in threads] == [100, 500, 900]
        assert [t.title for t in threads] == ["early", "middle", "late"]
        assert [t.started for t in threads] == [100, 200, 300]

    def test_fallback_when_op_tag_missing(self, corpus_path):
        conn = make_corpus(corpus_path)
        # Thread 7 has no op_post tag: earliest post time must order it.
        add_thread(
            conn,
            7,
            "no-op",
            [(71, 150, "first", ["story_post"]), (72, 160, "second", ["story_post"])],
            op_index=None,
        )
        add_thread(conn, 9, "has-op", [(90, 100, "op", ["story_post"])])
        conn.close()

        with CorpusReader(corpus_path) as r:
            assert [t.id for t in r.thread_order()] == [9, 7]

    def test_duplicate_op_tag_rows_do_not_duplicate_threads(self, corpus_path):
        conn = make_corpus(corpus_path)
        add_thread(conn, 1, "t1", [(10, 100, "x", ["story_post"])])
        conn.execute("INSERT INTO tag VALUES (10, 'op_post')")  # double-tag
        conn.commit()
        conn.close()

        with CorpusReader(corpus_path) as r:
            threads = r.thread_order()
        assert [t.id for t in threads] == [1]

    def test_matches_banished_db_first_threads(self):
        """Resolver regression against the real corpus, read-only."""
        real = Path("banished.db")
        if not real.exists():
            pytest.skip("banished.db not present")
        with CorpusReader(real) as r:
            first = [t.id for t in r.thread_order()[:3]]
        assert first == [30265887, 30305969, 30392208]


class TestStoryPosts:
    def test_filters_by_tag_and_orders_by_time(self, corpus_path):
        conn = make_corpus(corpus_path)
        add_thread(
            conn,
            1,
            "t",
            [
                (10, 100, "story-1", ["story_post"]),
                (11, 110, "tally", ["vote_tally_post"]),
                (12, 120, "story-2", ["story_post", "vote_choices"]),
                (13, 130, "meta", ["qm_post"]),
                (14, 140, "story-3", ["story_post"]),
            ],
        )
        conn.close()

        with CorpusReader(corpus_path) as r:
            posts = list(r.story_posts(1))
        assert [p.body for p in posts] == ["story-1", "story-2", "story-3"]

    def test_custom_tag_predicate(self, corpus_path):
        conn = make_corpus(corpus_path)
        add_thread(
            conn,
            1,
            "t",
            [
                (10, 100, "story", ["story_post"]),
                (11, 110, "meta", ["qm_post"]),
            ],
        )
        conn.close()

        with CorpusReader(corpus_path, tag="qm_post") as r:
            posts = list(r.story_posts(1))
        assert [p.body for p in posts] == ["meta"]

    def test_double_tagged_post_yields_one_row(self, corpus_path):
        conn = make_corpus(corpus_path)
        add_thread(conn, 1, "t", [(10, 100, "x", ["story_post"])])
        conn.execute("INSERT INTO tag VALUES (10, 'story_post')")  # double
        conn.commit()
        conn.close()

        with CorpusReader(corpus_path) as r:
            assert len(list(r.story_posts(1))) == 1


class TestBatches:
    def test_size_cap_and_remainder(self, corpus_path):
        conn = make_corpus(corpus_path)
        add_thread(
            conn, 1, "t", [(10 + i, 100 + i, f"p{i}", ["story_post"]) for i in range(7)]
        )
        conn.close()

        with CorpusReader(corpus_path) as r:
            batches = list(r.batches(1, batch_size=3))
        assert [len(b.posts) for b in batches] == [3, 3, 1]
        assert [b.index for b in batches] == [0, 1, 2]
        assert batches[0].text == "p0\n\np1\n\np2"

    def test_empty_thread_yields_nothing(self, corpus_path):
        conn = make_corpus(corpus_path)
        add_thread(conn, 1, "t", [(10, 100, "meta only", ["qm_post"])])
        conn.close()

        with CorpusReader(corpus_path) as r:
            assert list(r.batches(1)) == []

    def test_rejects_invalid_batch_size(self, corpus_path):
        conn = make_corpus(corpus_path)
        add_thread(conn, 1, "t", [(10, 100, "x", ["story_post"])])
        conn.close()

        with CorpusReader(corpus_path) as r, pytest.raises(ValueError):
            list(r.batches(1, batch_size=0))


class TestReadOnly:
    def test_connection_rejects_writes(self, corpus_path):
        conn = make_corpus(corpus_path)
        add_thread(conn, 1, "t", [(10, 100, "x", ["story_post"])])
        conn.close()
        with CorpusReader(corpus_path) as r, pytest.raises(sqlite3.OperationalError):
            r._conn.execute("INSERT INTO thread VALUES (99, 'nope')")

    def test_missing_db_raises(self, tmp_path):
        with pytest.raises(sqlite3.OperationalError):
            CorpusReader(tmp_path / "absent.db").thread_order()
