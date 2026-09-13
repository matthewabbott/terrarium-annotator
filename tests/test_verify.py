"""L0 tests for the verify CLI, per docs/plan T7: the checker passes a
clean scripted run and fails on seeded violations, one per invariant."""

from __future__ import annotations

import json

import pytest
from test_runner import SCRIPT, build_corpus, make_runner

from terrarium_annotator.cli import main
from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.llm import ScriptedModel
from terrarium_annotator.verify import verify


@pytest.fixture
def run_output(tmp_path):
    """A completed scripted pass: corpus path + annotator conn, clean."""
    corpus_path = tmp_path / "corpus.db"
    build_corpus(corpus_path)
    annotator_path = tmp_path / "annotator.db"
    runner, conn = make_runner(corpus_path, annotator_path, ScriptedModel(list(SCRIPT)))
    runner.run()
    return corpus_path, conn


def violations_for(run_output, mutate=None):
    corpus_path, conn = run_output
    if mutate:
        mutate(conn)
        conn.commit()
    with CorpusReader(corpus_path) as corpus:
        return verify(conn, corpus)


class TestCleanRun:
    def test_no_violations(self, run_output):
        assert violations_for(run_output) == []

    def test_cli_clean_exit_0(self, run_output, capsys):
        corpus_path, conn = run_output
        path = conn.execute("PRAGMA database_list").fetchone()[2]
        conn.close()
        code = main(["verify", "--corpus-db", str(corpus_path), "--annotator-db", path])
        assert code == 0
        assert "all invariants hold" in capsys.readouterr().out


class TestSeededViolations:
    def checks(self, vio):
        return {v.check for v in vio}

    def test_nonverbatim_quote(self, run_output):
        def seed(conn):
            conn.execute("UPDATE entry_source SET quote = 'not in the post'")

        assert "quote-validity" in self.checks(violations_for(run_output, seed))

    def test_verbatim_but_term_free_quote(self, run_output):
        def seed(conn):
            conn.execute("UPDATE entry_source SET quote = 'Mik channeled'")

        assert "quote-validity" in self.checks(violations_for(run_output, seed))

    def test_entry_without_sources(self, run_output):
        def seed(conn):
            conn.execute("DELETE FROM entry_source")

        assert "provenance-coverage" in self.checks(violations_for(run_output, seed))

    def test_bogus_post_id(self, run_output):
        def seed(conn):
            conn.execute("UPDATE entry_source SET post_id = 999999")

        vio = violations_for(run_output, seed)
        assert "backlink-integrity" in self.checks(vio)
        assert "quote-validity" in self.checks(vio)  # same seed trips both

    def test_dangling_revision_link(self, run_output):
        def seed(conn):
            conn.execute("PRAGMA foreign_keys = OFF")  # seeding past the guard
            conn.execute("UPDATE entry_source SET revision_id = 99999")

        assert "revision-links" in self.checks(violations_for(run_output, seed))

    def test_misaligned_tree_block(self, run_output):
        def seed(conn):
            conn.execute("INSERT INTO story_tree VALUES (1, 3, 'bad', 0)")

        assert "story-tree" in self.checks(violations_for(run_output, seed))

    def test_multiline_gist(self, run_output):
        def seed(conn):
            conn.execute(
                "UPDATE story_log SET gist = 'a' || char(10) || 'b' WHERE seq = 0"
            )

        assert "story-log" in self.checks(violations_for(run_output, seed))

    def test_run_state_unknown_thread(self, run_output):
        def seed(conn):
            conn.execute("UPDATE run_state SET thread_id = 999999")

        assert "run-state" in self.checks(violations_for(run_output, seed))

    def test_run_state_negative_batch(self, run_output):
        def seed(conn):
            conn.execute("UPDATE run_state SET batch_index = -1")

        assert "run-state" in self.checks(violations_for(run_output, seed))

    def test_budget_violation_detected(self, run_output):
        def seed(conn):
            cfg = json.loads(
                conn.execute(
                    "SELECT value FROM run_meta WHERE key = 'config'"
                ).fetchone()[0]
            )
            cfg["digest_budget_lines"] = 0  # any non-empty digest trips this
            conn.execute(
                "UPDATE run_meta SET value = ? WHERE key = 'config'", (json.dumps(cfg),)
            )

        assert "budget-compliance" in self.checks(violations_for(run_output, seed))

    def test_card_block_joined_rounding_not_flagged(self, run_output):
        """Regression: block-level len//4 adds separators and loses per-card
        floor discounts — a compliant batch must not be flagged. 10 cards x
        15 chars: runner accounting 10x3=30 <= 35 budget; naive block
        measure 159//4=39 > 35."""

        def seed(conn):
            cfg = json.loads(
                conn.execute(
                    "SELECT value FROM run_meta WHERE key = 'config'"
                ).fetchone()[0]
            )
            cfg["context_tokens"] = 70
            cfg["card_budget_fraction"] = 0.5  # card budget = 35 tokens
            conn.execute(
                "UPDATE run_meta SET value = ? WHERE key = 'config'",
                (json.dumps(cfg),),
            )
            line = "a" * 10 + ": " + "b" * 3  # 15 chars -> 3 tokens per card
            block = "\n".join([line] * 10)
            content = (
                "<story_so_far>\n\n</story_so_far>\n"
                f"<known_glossary>\n{block}\n</known_glossary>"
            )
            conn.execute(
                "UPDATE transcript SET content = ? WHERE role = 'user'", (content,)
            )

        assert "budget-compliance" not in self.checks(violations_for(run_output, seed))

    def test_cli_reports_violations_exit_1(self, run_output, capsys):
        corpus_path, conn = run_output
        conn.execute("DELETE FROM entry_source")
        conn.commit()
        path = conn.execute("PRAGMA database_list").fetchone()[2]
        conn.close()
        code = main(["verify", "--corpus-db", str(corpus_path), "--annotator-db", path])
        assert code == 1
        assert "provenance-coverage" in capsys.readouterr().out
