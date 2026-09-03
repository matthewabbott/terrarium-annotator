"""L0 tests for the run CLI, per the smoke-run goal: argument parsing,
--threads filtering, and a full scripted pass through main() with an
injected client factory (no real model)."""

from __future__ import annotations

import pytest
from test_runner import (  # noqa: F401  (fixture reuse)
    CORPUS_POSTS,
    SCRIPT,
    build_corpus,
    make_runner,
)

from terrarium_annotator.cli import main, parse_threads
from terrarium_annotator.llm import ChatResponse, ScriptedModel
from terrarium_annotator.state import load_run_meta


class TestParseThreads:
    def test_comma_list(self):
        assert parse_threads("30265887, 30305969") == [30265887, 30305969]

    def test_rejects_garbage(self):
        with pytest.raises(Exception, match="comma-separated"):
            parse_threads("abc,123")

    def test_empty_is_empty_list(self):
        assert parse_threads("") == []


@pytest.fixture
def corpus_db(tmp_path):
    path = tmp_path / "corpus.db"
    build_corpus(path)
    return path


class TestRunCommand:
    def test_full_run_via_injected_factory(self, corpus_db, tmp_path):
        annotator_db = tmp_path / "annotator.db"
        record = tmp_path / "rec.jsonl"
        code = main(
            [
                "run",
                "--corpus-db",
                str(corpus_db),
                "--annotator-db",
                str(annotator_db),
                "--pass-id",
                "cli-pass",
                "--record",
                str(record),
            ],
            client_factory=lambda model: ScriptedModel(list(SCRIPT)),
        )
        assert code == 0
        # The pass ran: checkpoint at the end, config recorded, L4 log written.
        import sqlite3

        conn = sqlite3.connect(annotator_db)
        assert load_run_meta(conn, "config") is not None
        assert record.exists() and record.stat().st_size > 0

    def test_threads_filter_through_cli(self, corpus_db, tmp_path):
        annotator_db = tmp_path / "annotator.db"
        # Only thread 103: one batch, gist-only script + one merge.
        script = [
            ChatResponse(content="Aghtaki bandits appear; Mik pays them off."),
        ]
        code = main(
            [
                "run",
                "--corpus-db",
                str(corpus_db),
                "--annotator-db",
                str(annotator_db),
                "--threads",
                "103",
            ],
            client_factory=lambda model: ScriptedModel(script),
        )
        assert code == 0
        import sqlite3

        conn = sqlite3.connect(annotator_db)
        gists = conn.execute("SELECT gist FROM story_log").fetchall()
        assert len(gists) == 1
        assert "Aghtaki" in gists[0][0]

    def test_unknown_thread_id_exits_2(self, corpus_db, tmp_path, capsys):
        code = main(
            [
                "run",
                "--corpus-db",
                str(corpus_db),
                "--annotator-db",
                str(tmp_path / "a.db"),
                "--threads",
                "999999",
            ],
            client_factory=lambda model: ScriptedModel([]),
        )
        assert code == 2
        assert "unknown thread ids" in capsys.readouterr().err

    def test_missing_required_args_system_exit(self):
        with pytest.raises(SystemExit):
            main(["run", "--corpus-db", "x.db"])
