"""L0 tests for adjudication mechanics: stratified sampler, demote_queue
+ propose_demotion gating, the ADJUDICATION_TOOLS allowlist (write
rejections), verified backup, and the chunked pass runner over
ScriptedModel. No live model, no real quota probe.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest
from test_runner import build_corpus  # tests-dir sibling

from terrarium_annotator.adjudicate import (
    Candidate,
    backup_db,
    load_flagged_candidates,
    run_adjudication,
    stratified_sample,
)
from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import (
    Evidence,
    GlossaryError,
    GlossaryStore,
    Provenance,
    QuoteRejected,
)
from terrarium_annotator.llm import ChatResponse, ScriptedModel, ToolCall
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.quota import QuotaExceeded
from terrarium_annotator.state import connect_annotator_db
from terrarium_annotator.tools import ADJUDICATION_TOOLS, ToolDispatcher

PROV = Provenance(thread_id=101, pass_id="t")


@pytest.fixture
def env(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    build_corpus(corpus_path)
    corpus = CorpusReader(corpus_path)
    conn = connect_annotator_db(tmp_path / "annotator.db")
    store = GlossaryStore(conn, corpus.post_body)
    return corpus, conn, store


def add_flagged_entry(store, term, gloss, tags, quote_post=1001, quote=None):
    quote = quote or "channeled Vys into the cloak"
    entry = store.propose_entry(
        term=term,
        gloss=gloss,
        evidence=[Evidence(quote_post, quote)],
        provenance=PROV,
        tags=tags,
    )
    return entry


class TestSampler:
    def make_candidates(self, specs):
        return [
            Candidate(
                candidate_id=i,
                term=term,
                quote="q",
                post_id=1001,
                thread_id=thread,
                entry_id=i,
                entry_term=term,
                gloss="g",
                tags=tags,
            )
            for i, (term, thread, tags) in enumerate(specs)
        ]

    def test_stratifies_by_class_and_thread(self):
        # 3 classes, uneven sizes, spread across 5 threads.
        specs = (
            [(f"mech{i}", 100 + i % 5, ("mechanic",)) for i in range(10)]
            + [(f"char{i}", 100 + i % 5, ("character",)) for i in range(6)]
            + [(f"tex{i}", 100 + i % 5, ()) for i in range(4)]
        )
        sample = stratified_sample(self.make_candidates(specs), 9)
        assert len(sample) == 9
        classes = {c.tags[0] if c.tags else "untagged" for c in sample}
        assert classes == {"mechanic", "character", "untagged"}
        threads = {c.thread_id for c in sample}
        assert len(threads) >= 4  # thread spread, not one-thread clusters
        # Proportional: mechanic (10/20) gets the most slots.
        mech = sum(1 for c in sample if c.tags == ("mechanic",))
        assert mech >= 4

    def test_deterministic_and_not_first_n(self):
        specs = [(f"t{i}", 100 + (i % 3), ("mechanic",)) for i in range(20)]
        candidates = self.make_candidates(specs)
        a = stratified_sample(candidates, 5)
        b = stratified_sample(candidates, 5)
        assert [c.candidate_id for c in a] == [c.candidate_id for c in b]
        assert [c.candidate_id for c in a] != list(range(5))  # not first-N

    def test_sample_capped_at_population(self):
        candidates = self.make_candidates([("a", 100, ("mechanic",))] * 3)
        assert len(stratified_sample(candidates, 50)) == 3

    def test_every_stratum_represented(self):
        specs = [(f"c{i}", 100, ("character",)) for i in range(2)] + [
            (f"m{i}", 100, ("mechanic",)) for i in range(50)
        ]
        sample = stratified_sample(self.make_candidates(specs), 5)
        assert any(c.tags == ("character",) for c in sample)


class TestCandidateLoading:
    def test_joins_entries_aliases_and_tags(self, env):
        _, conn, store = env
        entry = add_flagged_entry(store, "Vys", "Raw magical energy.", ("mechanic",))
        renamed = add_flagged_entry(
            store,
            "Suresh",
            "A librarian.",
            ("character",),
            quote_post=1003,
            quote="He met Suresh at the library.",
        )
        store.rename_entry("Suresh", "Suresh of Anthus", PROV)
        conn.execute(
            "INSERT INTO deferred_candidate(term, term_normalized, quote,"
            " post_id, thread_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("Vys", "vys", "channeled Vys into the cloak", 1001, 101, "now"),
        )
        conn.execute(
            "INSERT INTO deferred_candidate(term, term_normalized, quote,"
            " post_id, thread_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("Suresh", "suresh", "met Suresh at the library", 1003, 101, "now"),
        )
        conn.execute(
            "INSERT INTO deferred_candidate(term, term_normalized, quote,"
            " post_id, thread_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("Ghost", "ghost", "no entry for this", 1001, 101, "now"),
        )
        conn.commit()

        candidates = load_flagged_candidates(conn)
        assert len(candidates) == 3
        vys = next(c for c in candidates if c.term == "Vys")
        assert vys.entry_id == entry.id and vys.tags == ("mechanic",)
        suresh = next(c for c in candidates if c.term == "Suresh")
        assert suresh.entry_term == "Suresh of Anthus"  # alias-joined
        assert suresh.entry_id == renamed.id
        ghost = next(c for c in candidates if c.term == "Ghost")
        assert ghost.entry_id is None and ghost.tags == ()


class TestDemoteQueue:
    def test_round_trip(self, env):
        _, _, store = env
        entry = add_flagged_entry(store, "Vys", "Raw magical energy.", ("mechanic",))
        qid = store.propose_demotion(
            "Vys",
            "generic scenery in context",
            Evidence(1001, "channeled Vys into the cloak"),
        )
        (row,) = store.demote_queue_pending()
        assert row[0] == qid and row[1] == entry.id and row[2] == "Vys"
        # Entry untouched: no status change, no deletion.
        assert store.get("Vys").status == "tentative"

    def test_requires_verbatim_quote(self, env):
        _, _, store = env
        add_flagged_entry(store, "Vys", "Raw magical energy.", ("mechanic",))
        with pytest.raises(QuoteRejected, match="not verbatim"):
            store.propose_demotion(
                "Vys", "rationale", Evidence(1001, "paraphrased Vys quote")
            )

    def test_quote_must_mention_term(self, env):
        _, _, store = env
        add_flagged_entry(store, "Vys", "Raw magical energy.", ("mechanic",))
        with pytest.raises(QuoteRejected, match="does not mention"):
            store.propose_demotion(
                "Vys", "rationale", Evidence(1003, "He met Suresh at the library.")
            )

    def test_requires_rationale(self, env):
        _, _, store = env
        add_flagged_entry(store, "Vys", "Raw magical energy.", ("mechanic",))
        with pytest.raises(GlossaryError, match="rationale"):
            store.propose_demotion(
                "Vys", "  ", Evidence(1001, "channeled Vys into the cloak")
            )


class TestAdjudicationAllowlist:
    FORBIDDEN: ClassVar = [
        ("propose_entry", {"term": "x", "gloss": "y", "evidence": []}),
        ("update_entry", {"term": "x", "gloss": "y", "evidence": []}),
        ("add_alias", {"term": "x", "alias": "y", "evidence": {}}),
        ("rename_entry", {"term": "x", "new_term": "y"}),
        (
            "propose_merge",
            {
                "term_a": "x",
                "term_b": "y",
                "rationale": "r",
                "evidence": {"post_id": 1, "quote": "q"},
            },
        ),
        ("confirm_entry", {"term": "x"}),
    ]

    def test_forbidden_writes_rejected(self, env):
        corpus, conn, store = env
        dispatcher = ToolDispatcher(
            store,
            corpus,
            StoryLog(conn),
            provenance=lambda: PROV,
            allowed=ADJUDICATION_TOOLS,
        )
        for name, args in self.FORBIDDEN:
            result = json.loads(dispatcher.dispatch(ToolCall(name, args, "c")))
            assert not result["ok"], f"{name} was not rejected"
            assert "not available" in result["error"]

    def test_allowed_tools_dispatch(self, env):
        corpus, conn, store = env
        add_flagged_entry(store, "Vys", "Raw magical energy.", ("mechanic",))
        dispatcher = ToolDispatcher(
            store,
            corpus,
            StoryLog(conn),
            provenance=lambda: PROV,
            allowed=ADJUDICATION_TOOLS,
        )
        for name, args in [
            ("fetch_entry", {"term": "Vys"}),
            ("fetch_post", {"post_id": 1001}),
            ("fetch_thread_range", {"thread_id": 101}),
            ("recall_story", {"pattern": "vys"}),
            ("search_glossary", {"query": "Vys"}),
            ("search_corpus", {"needle": "Vys"}),
        ]:
            result = json.loads(dispatcher.dispatch(ToolCall(name, args, "c")))
            assert result["ok"], f"{name} failed: {result}"
        demote = json.loads(
            dispatcher.dispatch(
                ToolCall(
                    "propose_demotion",
                    {
                        "term": "Vys",
                        "rationale": "texture",
                        "evidence": {
                            "post_id": 1001,
                            "quote": "channeled Vys into the cloak",
                        },
                    },
                    "c",
                )
            )
        )
        assert demote["ok"] and demote["result"]["status"] == "pending"

    def test_schemas_match_allowlist(self, env):
        corpus, conn, store = env
        dispatcher = ToolDispatcher(
            store,
            corpus,
            StoryLog(conn),
            provenance=lambda: PROV,
            allowed=ADJUDICATION_TOOLS,
        )
        names = {s["function"]["name"] for s in dispatcher.schemas}
        assert names == ADJUDICATION_TOOLS


class TestBackup:
    def test_creates_verified_copy(self, tmp_path):
        src = tmp_path / "annotator.db"
        src.write_bytes(b"sqlite-bytes" * 100)
        dest = backup_db(src, tmp_path / "backups")
        assert dest.read_bytes() == src.read_bytes()
        assert dest.parent.name == "backups"

    def test_unverifiable_copy_raises_and_removes(self, tmp_path, monkeypatch):
        src = tmp_path / "annotator.db"
        src.write_bytes(b"original")
        import terrarium_annotator.adjudicate as adj

        monkeypatch.setattr(adj.filecmp, "cmp", lambda *a, **k: False)
        with pytest.raises(RuntimeError, match="verification failed"):
            backup_db(src, tmp_path / "backups")
        assert list((tmp_path / "backups").iterdir()) == []


class TestPassRunner:
    def test_chunked_pass_files_demotion_and_report(self, env, tmp_path):
        corpus, conn, store = env
        add_flagged_entry(store, "Vys", "Raw magical energy.", ("mechanic",))
        sample = [
            Candidate(
                candidate_id=1,
                term="Vys",
                quote="channeled Vys into the cloak",
                post_id=1001,
                thread_id=101,
                entry_id=1,
                entry_term="Vys",
                gloss="Raw magical energy.",
                tags=("mechanic",),
            )
        ]
        model = ScriptedModel(
            [
                ChatResponse(
                    content=None,
                    tool_calls=(
                        ToolCall(
                            "propose_demotion",
                            {
                                "term": "Vys",
                                "rationale": "generic energy texture",
                                "evidence": {
                                    "post_id": 1001,
                                    "quote": "channeled Vys into the cloak",
                                },
                            },
                            "c1",
                        ),
                    ),
                ),
                ChatResponse(content="Vys: TEXTURE (filed) — generic energy."),
            ]
        )
        result = run_adjudication(
            corpus, conn, model, sample, tmp_path / "adj", chunk_size=12
        )
        assert result.chunks_completed == 1 and result.halted is None
        assert (tmp_path / "adj" / "sample.json").exists()
        report = (tmp_path / "adj" / "chunk-00.md").read_text()
        assert "TEXTURE" in report
        assert len(store.demote_queue_pending()) == 1

    def test_quota_halt_marks_result(self, env, tmp_path):
        corpus, conn, _ = env
        sample = [
            Candidate(1, "a", "q", 1001, 101),
            Candidate(2, "b", "q", 1002, 101),
        ]
        model = ScriptedModel([ChatResponse(content="done")])
        result = run_adjudication(
            corpus,
            conn,
            model,
            sample,
            tmp_path / "adj",
            chunk_size=1,
            quota_check=lambda: (_ for _ in ()).throw(QuotaExceeded("halt")),
        )
        assert result.halted is not None and "quota" in result.halted
        assert result.chunks_completed == 0

    def test_three_failure_streak_halts(self, env, tmp_path):
        from terrarium_annotator.llm import ChatClientError

        corpus, conn, _ = env
        sample = [Candidate(i, f"t{i}", "q", 1001, 101) for i in range(5)]
        model = ScriptedModel([ChatClientError("boom")] * 10)
        result = run_adjudication(
            corpus, conn, model, sample, tmp_path / "adj", chunk_size=1
        )
        assert result.chunks_failed == 3  # stops at the streak cap
        assert "consecutive" in result.halted
