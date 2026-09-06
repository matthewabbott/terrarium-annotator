"""L0 tests for the run CLI, per the smoke-run goal: argument parsing,
--threads filtering, and a full scripted pass through main() with an
injected client factory (no real model)."""

from __future__ import annotations

import argparse

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
        with pytest.raises(argparse.ArgumentTypeError, match="comma-separated"):
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


def test_research_subcommand(tmp_path, capsys):
    """research CLI: wiring via injected factory; scripted one-shot session."""

    from terrarium_annotator.corpus import CorpusReader
    from terrarium_annotator.glossary import Evidence, GlossaryStore, Provenance
    from terrarium_annotator.llm import ChatResponse, ScriptedModel
    from terrarium_annotator.state import connect_annotator_db

    corpus_path = tmp_path / "corpus.db"
    build_corpus(corpus_path)
    annotator_path = tmp_path / "annotator.db"
    conn = connect_annotator_db(annotator_path)
    corpus = CorpusReader(corpus_path)
    store = GlossaryStore(conn, corpus.post_body)
    store.propose_entry(
        term="Vys",
        gloss="Raw magical energy.",
        evidence=[Evidence(1001, "channeled Vys into the cloak")],
        provenance=Provenance(thread_id=101, pass_id="t"),
    )
    conn.close()

    model = ScriptedModel(
        [
            ChatResponse(content="Nothing stands out."),
            ChatResponse(content="Still nothing actionable."),
            ChatResponse(content="Nothing to change."),
        ]
    )  # nudge cap is 2: announcement + 2 nudged replies
    code = main(
        [
            "research",
            "--corpus-db",
            str(corpus_path),
            "--annotator-db",
            str(annotator_path),
        ],
        client_factory=lambda m: model,
    )
    assert code == 0
    assert "Nothing to change" in capsys.readouterr().out
    # The session ran and saw the glossary (the scripted model's request).
    assert "Vys" in str(model.requests[0]["messages"])


def test_research_subcommand_parse():
    from terrarium_annotator.cli import build_parser

    args = build_parser().parse_args(
        [
            "research",
            "--corpus-db",
            "c.db",
            "--annotator-db",
            "a.db",
            "--focus",
            "aliases",
        ]
    )
    assert args.command == "research" and args.focus == "aliases"
