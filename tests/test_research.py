"""Tests for the researcher tier: toolset boundaries, merge queue,
corpus-wide search, provenance-from-evidence, scripted session (L1)."""

from __future__ import annotations

import json

import pytest
from test_corpus import add_thread, make_corpus

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import (
    Evidence,
    GlossaryStore,
    Provenance,
    QuoteRejected,
    UnknownEntry,
)
from terrarium_annotator.llm import ChatResponse, ScriptedModel, ToolCall
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.research import Researcher
from terrarium_annotator.state import connect_annotator_db
from terrarium_annotator.tools import (
    ANNOTATOR_TOOLS,
    RESEARCHER_TOOLS,
    ToolDispatcher,
)


@pytest.fixture
def env(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn0 = make_corpus(corpus_path)
    add_thread(
        conn0,
        1,
        "t1",
        [
            (10, 100, "Mik channeled Vys into the cloak.", ["story_post"]),
            (11, 110, "Archmagos Megalos Suresh welcomed him.", ["story_post"]),
        ],
    )
    conn0.close()
    corpus = CorpusReader(corpus_path)
    conn = connect_annotator_db(tmp_path / "annotator.db")
    store = GlossaryStore(conn, corpus.post_body)
    prov = Provenance(thread_id=1, pass_id="t")
    store.propose_entry(
        term="Vys",
        gloss="Raw magical energy.",
        evidence=[Evidence(10, "channeled Vys into the cloak")],
        provenance=prov,
    )
    store.propose_entry(
        term="Archmagos Megalos",
        gloss="The academy archmagos.",
        evidence=[Evidence(11, "Archmagos Megalos Suresh welcomed him")],
        provenance=prov,
    )
    return corpus, conn, store


class TestToolsetBoundaries:
    def test_annotator_excludes_researcher_tools(self, env):
        corpus, conn, store = env
        d = ToolDispatcher(
            store,
            corpus,
            StoryLog(conn),
            provenance=lambda: None,
            allowed=ANNOTATOR_TOOLS,
        )
        names = {s["function"]["name"] for s in d.schemas}
        assert "search_corpus" not in names
        assert "rename_entry" not in names
        assert "propose_merge" not in names
        assert "propose_entry" in names

    def test_researcher_includes_both_sets(self, env):
        corpus, conn, store = env
        d = ToolDispatcher(
            store,
            corpus,
            StoryLog(conn),
            provenance=lambda: None,
            allowed=RESEARCHER_TOOLS,
        )
        names = {s["function"]["name"] for s in d.schemas}
        assert {"search_corpus", "rename_entry", "propose_merge"} <= names
        assert "fetch_entry" in names

    def test_annotator_dispatch_rejects_search_corpus(self, env):
        corpus, conn, store = env
        d = ToolDispatcher(
            store,
            corpus,
            StoryLog(conn),
            provenance=lambda: None,
            allowed=ANNOTATOR_TOOLS,
        )
        result = json.loads(
            d.dispatch(ToolCall(name="search_corpus", arguments={"needle": "x"}))
        )
        assert result["ok"] is False


class TestMergeQueue:
    def test_propose_merge_queues_never_merges(self, env):
        _, _, store = env
        qid = store.propose_merge(
            "Vys",
            "Archmagos Megalos",
            "test rationale",
            Evidence(10, "channeled Vys into the cloak"),
        )
        pending = store.merge_queue_pending()
        assert pending[0][0] == qid
        assert pending[0][1] == "Vys"
        assert store.get("Vys") and store.get("Archmagos Megalos")

    def test_merge_requires_existing_entries(self, env):
        _, _, store = env
        with pytest.raises(UnknownEntry):
            store.propose_merge(
                "Vys", "Ghost", "x", Evidence(10, "channeled Vys into the cloak")
            )

    def test_merge_quote_must_mention_a_term(self, env):
        _, _, store = env
        with pytest.raises(QuoteRejected, match="neither term"):
            store.propose_merge(
                "Vys", "Archmagos Megalos", "x", Evidence(11, "welcomed him")
            )


class TestSearchCorpus:
    def test_corpus_wide_search(self, env):
        corpus, conn, store = env
        d = ToolDispatcher(
            store,
            corpus,
            StoryLog(conn),
            provenance=lambda: None,
            allowed=RESEARCHER_TOOLS,
        )
        result = json.loads(
            d.dispatch(ToolCall(name="search_corpus", arguments={"needle": "Suresh"}))
        )
        assert result["ok"] is True
        assert result["result"]["total"] == 1
        assert result["result"]["posts"][0]["id"] == 11


class TestResearcherSession:
    def test_scripted_session_applies_charter(self, env):
        corpus, conn, store = env
        script = [
            ChatResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        name="add_alias",
                        arguments={
                            "term": "Archmagos Megalos",
                            "alias": "Suresh",
                            "evidence": {
                                "post_id": 11,
                                "quote": "Megalos Suresh welcomed him",
                            },
                        },
                        id="c1",
                    ),
                    ToolCall(
                        name="propose_merge",
                        arguments={
                            "term_a": "Vys",
                            "term_b": "Archmagos Megalos",
                            "rationale": "deliberate bogus pair to test queueing",
                            "evidence": {
                                "post_id": 10,
                                "quote": "channeled Vys into the cloak",
                            },
                        },
                        id="c2",
                    ),
                ),
            ),
            ChatResponse(content="Did the alias work; queued one merge for humans."),
        ]
        r = Researcher(corpus, store, StoryLog(conn), ScriptedModel(script), conn)
        report = r.research(focus="aliases")
        assert "alias" in report.lower()
        assert store.find("suresh").term == "Archmagos Megalos"
        assert len(store.merge_queue_pending()) == 1
        assert store.get("Vys") and store.get("Archmagos Megalos")

    def test_researcher_writes_never_blame_thread_0(self, env):
        """Provenance derives thread from cited evidence posts."""
        corpus, conn, store = env
        script = [
            ChatResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        name="add_alias",
                        arguments={
                            "term": "Archmagos Megalos",
                            "alias": "Suresh",
                            "evidence": {
                                "post_id": 11,
                                "quote": "Megalos Suresh welcomed him",
                            },
                        },
                        id="c1",
                    ),
                ),
            ),
            ChatResponse(content="done"),
        ]
        r = Researcher(corpus, store, StoryLog(conn), ScriptedModel(script), conn)
        r.research()
        # Positive check on the ALIAS row (revision_id NULL): thread 1.
        row = conn.execute(
            "SELECT s.thread_id FROM entry_source s "
            "JOIN entry e ON e.id = s.entry_id "
            "WHERE e.term = 'Archmagos Megalos' AND s.post_id = 11 "
            "AND s.revision_id IS NULL"
        ).fetchone()
        assert row is not None and row[0] == 1
        rows = conn.execute("SELECT DISTINCT thread_id FROM entry_source").fetchall()
        assert all(r[0] != 0 for r in rows)

    def test_announcement_gets_nudged_then_works(self, env):
        """Regression for the live failure: content-only preamble must not
        end the session; the nudge drives the model to tool calls."""
        corpus, conn, store = env
        script = [
            ChatResponse(content="I'll start by probing the corpus."),  # no calls
            ChatResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        name="add_alias",
                        arguments={
                            "term": "Archmagos Megalos",
                            "alias": "Suresh",
                            "evidence": {
                                "post_id": 11,
                                "quote": "Megalos Suresh welcomed him",
                            },
                        },
                        id="c1",
                    ),
                ),
            ),
            ChatResponse(content="Aliased the Archmagos."),
        ]
        model = ScriptedModel(script)
        r = Researcher(corpus, store, StoryLog(conn), model, conn)
        report = r.research()
        assert store.find("suresh").term == "Archmagos Megalos"
        assert "Aliased" in report
        # The nudge message went out between the announcement and the work.
        user_msgs = [m["messages"][-1]["content"] for m in model.requests]
        assert any("called no tools" in c for c in user_msgs)
