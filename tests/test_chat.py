"""Tests for the chat surface (goal criterion 6): read-only boundary,
tool-loop wiring via ScriptedModel, and the CLI --once path."""

from __future__ import annotations

import json

import pytest
from test_corpus import add_thread, make_corpus

from terrarium_annotator.chat import (
    CHAT_SYSTEM_PROMPT,
    READONLY_TOOLS,
    chat_turn,
)
from terrarium_annotator.cli import main
from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import Evidence, GlossaryStore, Provenance
from terrarium_annotator.llm import ChatResponse, ScriptedModel, ToolCall
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.state import connect_annotator_db
from terrarium_annotator.tools import ToolDispatcher


@pytest.fixture
def chat_env(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn0 = make_corpus(corpus_path)
    add_thread(conn0, 1, "t", [(10, 100, "Mik channeled Vys.", ["story_post"])])
    conn0.close()
    corpus = CorpusReader(corpus_path)
    conn = connect_annotator_db(tmp_path / "annotator.db")
    store = GlossaryStore(conn, corpus.post_body)
    store.propose_entry(
        term="Vys",
        gloss="Raw magical energy.",
        evidence=[Evidence(10, "channeled Vys")],
        provenance=Provenance(thread_id=1, pass_id="t"),
    )
    dispatcher = ToolDispatcher(
        store,
        corpus,
        StoryLog(conn),
        provenance=lambda: None,
        allowed=READONLY_TOOLS,
    )
    return dispatcher


class TestReadOnlyBoundary:
    def test_write_tools_rejected(self, chat_env):
        result = json.loads(
            chat_env.dispatch(
                ToolCall(
                    name="propose_entry",
                    arguments={"term": "X", "gloss": "y", "evidence": []},
                )
            )
        )
        assert result["ok"] is False
        assert "not available" in result["error"]

    def test_schemas_advertise_only_readonly(self, chat_env):
        names = {s["function"]["name"] for s in chat_env.schemas}
        assert names == READONLY_TOOLS


class TestChatTurn:
    def test_tool_loop_wiring(self, chat_env):
        model = ScriptedModel(
            [
                ChatResponse(
                    content=None,
                    tool_calls=(
                        ToolCall(
                            name="fetch_entry", arguments={"term": "Vys"}, id="c1"
                        ),
                    ),
                ),
                ChatResponse(content="Vys is raw magical energy, per the entry."),
            ]
        )
        messages = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": "What is Vys?"},
        ]
        answer = chat_turn(messages, model, chat_env)
        assert "raw magical energy" in answer
        # The second request carried the tool result back to the model.
        second = model.requests[1]["messages"]
        tool_msgs = [m for m in second if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "Raw magical energy." in tool_msgs[0]["content"]
        # History accumulates across the turn.
        assert messages[-1]["role"] == "assistant"


def test_cli_once_real(tmp_path, capsys):
    corpus_path = tmp_path / "corpus.db"
    conn0 = make_corpus(corpus_path)
    add_thread(conn0, 1, "t", [(10, 100, "Mik channeled Vys.", ["story_post"])])
    conn0.close()
    annotator_path = tmp_path / "annotator.db"
    model = ScriptedModel([ChatResponse(content="an answer")])
    code = main(
        [
            "chat",
            "--corpus-db",
            str(corpus_path),
            "--annotator-db",
            str(annotator_path),
            "--once",
            "ping",
        ],
        client_factory=lambda model_name: model,
    )
    assert code == 0
    assert "an answer" in capsys.readouterr().out
