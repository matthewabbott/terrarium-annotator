"""L0 tests for prompts-as-data (docs/design/prompt-laddering.md):
prompt file loading, run_meta provenance (prompt + model), CLI default
behavior unchanged.
"""

from __future__ import annotations

import json

import pytest
from test_runner import SCRIPT, build_corpus, make_runner  # tests-dir siblings

from terrarium_annotator.cli import main
from terrarium_annotator.llm import ChatResponse, ScriptedModel
from terrarium_annotator.runner import DEFAULT_PROMPT_PATH, load_prompt
from terrarium_annotator.state import load_run_meta


class TestPromptLoading:
    def test_default_is_reader_v2_vintage(self):
        assert DEFAULT_PROMPT_PATH.stem == "reader-v2"
        text = load_prompt(None)
        assert "When in doubt, add it" in text  # the relaxed admission line

    def test_loads_explicit_file(self, tmp_path):
        p = tmp_path / "variant.md"
        p.write_text("Custom prompt.\n")
        assert load_prompt(p) == "Custom prompt."

    def test_missing_file_fails_loudly(self, tmp_path):
        with pytest.raises(OSError):
            load_prompt(tmp_path / "nope.md")

    def test_runner_uses_loaded_prompt(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        p = tmp_path / "variant.md"
        p.write_text("VARIANT MARKER prompt")
        runner, _ = make_runner(
            corpus_path,
            tmp_path / "annotator.db",
            ScriptedModel([ChatResponse(content="gist")]),
            prompt_file=str(p),
        )
        assert runner.system_prompt == "VARIANT MARKER prompt"
        assert runner.prompt_name == "variant"

    def test_custom_prompt_reaches_llm_request(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        p = tmp_path / "variant.md"
        p.write_text("VARIANT MARKER prompt")
        model = ScriptedModel([ChatResponse(content="gist")])
        runner, _ = make_runner(
            corpus_path,
            tmp_path / "annotator.db",
            model,
            prompt_file=str(p),
        )
        runner.run(max_batches=1, only_threads=[101])
        system = model.requests[0]["messages"][0]
        assert system["role"] == "system"
        assert system["content"] == "VARIANT MARKER prompt"


class TestProvenance:
    def test_run_meta_records_prompt_and_model(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        annotator_path = tmp_path / "annotator.db"
        code = main(
            [
                "run",
                "--corpus-db",
                str(corpus_path),
                "--annotator-db",
                str(annotator_path),
                "--pass-id",
                "reader-v9/kimi-k2.5",
                "--prompt",
                "prompts/reader-v1.md",
                "--model",
                "kimi-k2.5",
                "--usage-dir",
                str(tmp_path / "usage"),
            ],
            client_factory=lambda model: ScriptedModel(list(SCRIPT)),
            quota_check_factory=lambda threshold: None,
        )
        assert code == 0
        import sqlite3

        conn = sqlite3.connect(annotator_path)
        assert load_run_meta(conn, "prompt") == "reader-v1"
        assert load_run_meta(conn, "model") == "kimi-k2.5"
        cfg = json.loads(load_run_meta(conn, "config"))
        assert cfg["prompt_file"] == "prompts/reader-v1.md"

    def test_default_run_records_reader_v2(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        annotator_path = tmp_path / "annotator.db"
        code = main(
            [
                "run",
                "--corpus-db",
                str(corpus_path),
                "--annotator-db",
                str(annotator_path),
                "--usage-dir",
                str(tmp_path / "usage"),
            ],
            client_factory=lambda model: ScriptedModel(list(SCRIPT)),
            quota_check_factory=lambda threshold: None,
        )
        assert code == 0
        import sqlite3

        conn = sqlite3.connect(annotator_path)
        assert load_run_meta(conn, "prompt") == "reader-v2"
