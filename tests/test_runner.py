"""L1 end-to-end runner tests, per docs/plan T6 + dev-verification.md:
ScriptedModel drives the real Runner over a fabricated 3-thread corpus.
No LLM anywhere; the wiring is what is under test.
"""

from __future__ import annotations

import json

import pytest
from test_corpus import add_thread, make_corpus

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import GlossaryStore, Provenance
from terrarium_annotator.llm import ChatResponse, ScriptedModel, ToolCall
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.runner import Runner, RunnerConfig
from terrarium_annotator.state import (
    connect_annotator_db,
    load_run_state,
)
from terrarium_annotator.tools import ToolDispatcher

CORPUS_POSTS = {
    101: [
        (1001, 1000, "Mik channeled Vys into the cloak.", ["story_post"]),
        (1002, 1010, "The Vys flared blue.", ["story_post"]),
        (1003, 1020, "He met Suresh at the library.", ["story_post"]),
    ],
    102: [
        (2001, 2000, "They traveled to Anthus.", ["story_post"]),
        (2002, 2010, "The cloak hummed.", ["story_post"]),
    ],
    103: [
        (3001, 3002, "Aghtaki bandits appeared.", ["story_post"]),
        (3002, 3010, "Mik paid them off.", ["story_post"]),
    ],
}


def build_corpus(path):
    conn = make_corpus(path)
    for tid, posts in CORPUS_POSTS.items():
        add_thread(conn, tid, f"thread {tid}", posts)
    conn.close()


def tc(name, arguments):
    return ToolCall(name=name, arguments=arguments, id=f"call_{name}")


# The full scripted pass, in call order. Merge calls interleave at thread
# closes (OptMem nap via the LLM).
SCRIPT = [
    # t1 b0: propose Vys (valid quote), then gist
    ChatResponse(
        content=None,
        tool_calls=(
            tc(
                "propose_entry",
                {
                    "term": "Vys",
                    "gloss": "Raw magical energy.",
                    "evidence": [
                        {"post_id": 1001, "quote": "channeled Vys into the cloak"}
                    ],
                    "tags": ["mechanic"],
                },
            ),
        ),
    ),
    ChatResponse(content="Mik channels Vys into the cloak."),
    # t1 b1: propose with a paraphrased quote (rejected), then gist
    ChatResponse(
        content=None,
        tool_calls=(
            tc(
                "propose_entry",
                {
                    "term": "Suresh",
                    "gloss": "A librarian.",
                    "evidence": [
                        {"post_id": 1003, "quote": "paraphrased Suresh intro"}
                    ],
                },
            ),
        ),
    ),
    ChatResponse(content="Mik meets Suresh at the library."),
    # t1 close: settle block (0,2)
    ChatResponse(content="Thread 1: Mik experiments with Vys; meets Suresh."),
    # t2 b0: gist only
    ChatResponse(content="They travel to Anthus; the cloak hums."),
    # t3 b0: update Vys (valid quote), then gist
    ChatResponse(
        content=None,
        tool_calls=(
            tc(
                "update_entry",
                {
                    "term": "Vys",
                    "gloss": "Raw magical energy; flares blue when channeled.",
                    "evidence": [{"post_id": 1002, "quote": "The Vys flared blue."}],
                },
            ),
        ),
    ),
    ChatResponse(content="Aghtaki bandits appear; Mik pays them off."),
    # t3 close: settle (2,4) then (0,4) from halves
    ChatResponse(content="Threads 2-3: Anthus travel; bandit payoff."),
    ChatResponse(content="Threads 1-3: Vys experiments, Anthus, bandits."),
]


@pytest.fixture
def env(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    build_corpus(corpus_path)
    annotator_path = tmp_path / "annotator.db"
    return corpus_path, annotator_path


def make_runner(corpus_path, annotator_path, model, **cfg):
    corpus = CorpusReader(corpus_path)
    conn = connect_annotator_db(annotator_path)
    memory = StoryLog(conn)
    glossary = GlossaryStore(conn, corpus.post_body)
    cfg.setdefault("batch_size", 2)
    cfg.setdefault("pass_id", "test-pass")
    config = RunnerConfig(**cfg)
    return Runner(corpus, memory, glossary, model, conn, config), conn


class TestEndToEnd:
    def test_full_pass_wiring(self, env):
        corpus_path, annotator_path = env
        runner, conn = make_runner(
            corpus_path, annotator_path, ScriptedModel(list(SCRIPT))
        )
        runner.run()

        # Valid write committed; card gloss is the latest revision.
        glossary = runner.glossary
        entry = glossary.get("Vys")
        assert entry.gloss == "Raw magical energy; flares blue when channeled."
        assert entry.status == "tentative"
        assert len(glossary.revisions(entry.id)) == 2

        # Rejected write left no entry; error was fed back as a tool result.
        assert glossary.find("Suresh") is None
        failures = conn.execute(
            "SELECT COUNT(*) FROM transcript WHERE role = 'tool'"
            " AND content LIKE '%\"ok\": false%'"
        ).fetchone()[0]
        assert failures == 1

        # One gist per batch; merge tree settled through (0,4).
        assert runner.memory.log_len() == 4
        assert runner.memory._settled(0, 4) is not None

        # Checkpoint at the end of thread 103.
        assert load_run_state(conn, "test-pass") == (103, 1)

        # Transcript has an assistant row per batch (plus tool rows).
        assistants = conn.execute(
            "SELECT COUNT(*) FROM transcript WHERE role = 'assistant'"
        ).fetchone()[0]
        assert assistants >= 4


def digest_lines(request: dict) -> int:
    """Non-empty lines in the <story_so_far> block of a batch request."""
    content = request["messages"][1]["content"]
    block = content.split("<story_so_far>")[1].split("</story_so_far>")[0]
    return len([ln for ln in block.splitlines() if ln.strip()])


class TestL1Assertions:
    def test_digest_within_budget_at_every_batch_boundary(self, env):
        corpus_path, annotator_path = env
        model = ScriptedModel(list(SCRIPT))
        runner, _ = make_runner(corpus_path, annotator_path, model)
        runner.run()
        batch_calls = [r for r in model.requests if r["tools"] is not None]
        assert len(batch_calls) == 7  # 2+2+1+2 (tool rounds per batch)
        for req in model.requests:
            if req["tools"] is not None:
                assert digest_lines(req) <= 96

    def test_merge_calls_ordered_after_thread_close(self, env):
        corpus_path, annotator_path = env
        model = ScriptedModel(list(SCRIPT))
        runner, _ = make_runner(corpus_path, annotator_path, model)
        runner.run()
        # Merge calls carry no tools; they must be exactly script indices
        # 4 (after t1 close: settle 0-2) and 8, 9 (after t3 close: 2-4, 0-4).
        merge_idx = [i for i, r in enumerate(model.requests) if r["tools"] is None]
        assert merge_idx == [4, 8, 9]
        prompts = [model.requests[i]["messages"][0]["content"] for i in merge_idx]
        assert "Mik channels Vys" in prompts[0]  # raw gists for small block
        # All fixture blocks are <= 16 entries, so all compress from raw
        # gists (OptMem RAW_MAX semantics); the halves path is tested below.
        assert "Aghtaki bandits appear" in prompts[2]

    def test_oversized_gist_and_merge_summary_truncated(self, env, tmp_path):
        corpus_path = tmp_path / "tiny.db"
        conn = make_corpus(corpus_path)
        add_thread(
            conn,
            201,
            "tiny",
            [
                (9001, 100, "The Vys pool shimmered.", ["story_post"]),
                (9002, 110, "It pulsed.", ["story_post"]),
            ],
        )
        conn.close()
        script = [
            ChatResponse(content=("Long gist one. " * 60)),  # > 280-char cap
            ChatResponse(content=("Long gist two. " * 60)),
            ChatResponse(content=("Long merge. " * 60)),
        ]
        runner, _ = make_runner(
            corpus_path, tmp_path / "a.db", ScriptedModel(script), batch_size=1
        )
        runner.run()
        for e in runner.memory.slice(0, 2):
            assert len(e.gist) <= 280 and "\n" not in e.gist
        assert len(runner.memory._settled(0, 2)) <= 280

    def test_large_block_merges_from_halves(self, tmp_path):
        # 32 gists, everything settled through size 16 via direct settle()
        # calls; the (0,32) merge must be built from halves' summaries.
        corpus_path = tmp_path / "c.db"
        conn0 = make_corpus(corpus_path)
        add_thread(conn0, 301, "t", [(1, 1, "x", ["story_post"])])
        conn0.close()
        model = ScriptedModel([ChatResponse(content="whole-thread summary")])
        runner, _ = make_runner(corpus_path, tmp_path / "a.db", model)
        memory = runner.memory
        for i in range(32):
            memory.append(301, f"gist {i}")
        memory.close_thread(301)
        while len(memory.pending()) > 1:  # settle all but the (0,32) block
            lo, hi = memory.pending()[0]
            if hi - lo > 16:
                break
            memory.settle(lo, hi, f"sum {lo}-{hi}")
        assert memory.pending() == [(0, 32)]
        runner._settle_merges()
        prompt = model.requests[0]["messages"][0]["content"]
        assert "#0-15 sum 0-16" in prompt and "#16-31 sum 16-32" in prompt
        assert memory._settled(0, 32) == "whole-thread summary"

    def test_nonexistent_term_update_is_error_and_run_continues(self, env):
        corpus_path, annotator_path = env
        script = [
            ChatResponse(
                content=None,
                tool_calls=(
                    tc(
                        "update_entry",
                        {
                            "term": "Ghost",
                            "gloss": "no such entry",
                            "evidence": [
                                {
                                    "post_id": 1001,
                                    "quote": "channeled Vys into the cloak",
                                }
                            ],
                        },
                    ),
                ),
            ),
            ChatResponse(content="Mik channels Vys into the cloak."),
        ] + list(SCRIPT[2:])
        runner, conn = make_runner(corpus_path, annotator_path, ScriptedModel(script))
        runner.run()
        failures = conn.execute(
            "SELECT COUNT(*) FROM transcript WHERE role = 'tool'"
            " AND content LIKE '%no entry%'"
        ).fetchone()[0]
        assert failures == 2  # Ghost update + later Vys update (never proposed)
        assert runner.glossary.find("Vys") is None
        assert runner.memory.log_len() == 4  # run completed regardless

    def test_digest_covers_full_log_after_settle(self, env):
        corpus_path, annotator_path = env
        runner, _ = make_runner(
            corpus_path, annotator_path, ScriptedModel(list(SCRIPT))
        )
        runner.run()
        items = runner.memory.cover(96)
        assert len(items) <= 96
        assert items[0].lo == 0 and items[-1].hi == 4

    def test_kill_and_resume_no_duplicate_work(self, env):
        corpus_path, annotator_path = env
        # Run A dies after the first batch (before thread close).
        runner_a, conn = make_runner(
            corpus_path, annotator_path, ScriptedModel(list(SCRIPT[:2]))
        )
        runner_a.run(max_batches=1)
        assert load_run_state(conn, "test-pass") == (101, 1)

        # Run B resumes: must process exactly the remaining batches.
        runner_b, conn = make_runner(
            corpus_path, annotator_path, ScriptedModel(list(SCRIPT[2:]))
        )
        runner_b.run()

        assert runner_b.memory.log_len() == 4
        gists = [e.gist for e in runner_b.memory.slice(0, 4)]
        assert len(set(gists)) == 4  # no reprocessed batch
        entry = runner_b.glossary.get("Vys")
        assert len(runner_b.glossary.revisions(entry.id)) == 2

    def test_new_pass_id_starts_fresh(self, env):
        """A checkpoint belongs to its pass; a different pass_id restarts."""
        corpus_path, annotator_path = env
        runner_a, conn = make_runner(
            corpus_path, annotator_path, ScriptedModel(list(SCRIPT[:2]))
        )
        runner_a.run(max_batches=1)
        assert load_run_state(conn, "pass-b") is None  # not pass-a's checkpoint

        # A pass-b runner therefore starts at batch 0, not batch 1.
        runner_b, _ = make_runner(
            corpus_path,
            annotator_path,
            ScriptedModel(list(SCRIPT[:2])),
            pass_id="pass-b",
        )
        runner_b.run(max_batches=1)
        # pass-b processed batch 0 again: its gist text was recorded twice.
        gists = [e.gist for e in runner_b.memory.slice(0, 10)]
        assert gists.count("Mik channels Vys into the cloak.") == 2


class TestDispatcher:
    def test_unknown_tool_returns_error_payload(self, env):
        corpus_path, annotator_path = env
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(annotator_path)
        dispatcher = ToolDispatcher(
            GlossaryStore(conn, corpus.post_body),
            corpus,
            StoryLog(conn),
            provenance=lambda: None,
        )
        result = json.loads(dispatcher.dispatch(tc("nonsense", {})))
        assert result["ok"] is False
        assert "unknown tool" in result["error"]

    def test_fetch_entry_missing_term(self, env):
        corpus_path, annotator_path = env
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(annotator_path)
        dispatcher = ToolDispatcher(
            GlossaryStore(conn, corpus.post_body),
            corpus,
            StoryLog(conn),
            provenance=lambda: None,
        )
        result = json.loads(dispatcher.dispatch(tc("fetch_entry", {"term": "X"})))
        assert result["ok"] is False

    def test_recall_story_regex(self, env):
        corpus_path, annotator_path = env
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(annotator_path)
        memory = StoryLog(conn)
        memory.append(101, "Mik channels Vys.")
        memory.append(101, "They buy supplies.")
        dispatcher = ToolDispatcher(
            GlossaryStore(conn, corpus.post_body),
            corpus,
            memory,
            provenance=lambda: None,
        )
        result = json.loads(dispatcher.dispatch(tc("recall_story", {"pattern": "vys"})))
        assert result["ok"] is True
        assert result["result"]["total"] == 1

    def test_bad_regex_is_error_payload(self, env):
        corpus_path, annotator_path = env
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(annotator_path)
        dispatcher = ToolDispatcher(
            GlossaryStore(conn, corpus.post_body),
            corpus,
            StoryLog(conn),
            provenance=lambda: None,
        )
        result = json.loads(dispatcher.dispatch(tc("recall_story", {"pattern": "["})))
        assert result["ok"] is False


class TestThreadFilter:
    def test_unknown_thread_id_rejected(self, env):
        corpus_path, annotator_path = env
        runner, _ = make_runner(
            corpus_path, annotator_path, ScriptedModel(list(SCRIPT))
        )
        with pytest.raises(ValueError, match="unknown thread ids"):
            runner.run(only_threads=[999999])

    def test_filter_preserves_chronological_order(self, env):
        corpus_path, annotator_path = env
        # Script for threads 102 then 103 (gist-only batches + 3 merges).
        script = [
            ChatResponse(content="They travel to Anthus; the cloak hums."),
            ChatResponse(content="Aghtaki bandits appear; Mik pays them off."),
            ChatResponse(content="Threads 2-3 together."),
        ]
        model = ScriptedModel(script)
        runner, _ = make_runner(corpus_path, annotator_path, model)
        # Input order is reversed; resolver order (102 before 103) must win.
        runner.run(only_threads=[103, 102])
        gists = [e.gist for e in runner.memory.slice(0, 2)]
        assert gists[0].startswith("They travel")
        assert gists[1].startswith("Aghtaki")

    def test_filtered_pass_resumes_own_checkpoint(self, env):
        """Same pass-id + filter containing the checkpoint thread: resume,
        not restart (the supervisor's t1-40 recovery semantics)."""
        corpus_path, annotator_path = env
        runner_a, conn = make_runner(
            corpus_path, annotator_path, ScriptedModel(list(SCRIPT[:2]))
        )
        runner_a.run(max_batches=1)
        assert load_run_state(conn, "test-pass") == (101, 1)
        runner_b, _ = make_runner(
            corpus_path, annotator_path, ScriptedModel(list(SCRIPT[2:]))
        )
        runner_b.run(max_batches=1, only_threads=[101])
        gists = [e.gist for e in runner_b.memory.slice(0, 10)]
        # Batch 0 was NOT reprocessed: its gist appears exactly once.
        assert gists.count("Mik channels Vys into the cloak.") == 1

    def test_filtered_pass_fresh_when_checkpoint_outside_filter(self, env):
        """A checkpoint on a thread outside the filter does not constrain:
        the filtered pass starts at the filter's first thread, batch 0."""
        corpus_path, annotator_path = env
        runner_a, conn = make_runner(
            corpus_path, annotator_path, ScriptedModel(list(SCRIPT[:2]))
        )
        runner_a.run(max_batches=1)
        assert load_run_state(conn, "test-pass") == (101, 1)
        script = [ChatResponse(content="Aghtaki bandits appear; Mik pays them off.")]
        runner_b, _ = make_runner(corpus_path, annotator_path, ScriptedModel(script))
        runner_b.run(max_batches=1, only_threads=[103])
        gists = [e.gist for e in runner_b.memory.slice(0, 10)]
        assert gists[-1].startswith("Aghtaki")  # fresh start at thread 103


class TestDispatcherIntegrity:
    def test_alias_readd_through_dispatch_is_ok(self, env):
        """The smoke-run crash: model re-registers an entry's own alias."""
        corpus_path, annotator_path = env
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(annotator_path)
        store = GlossaryStore(conn, corpus.post_body)
        dispatcher = ToolDispatcher(
            store,
            corpus,
            StoryLog(conn),
            provenance=lambda: Provenance(thread_id=101, pass_id="t"),
        )
        propose = tc(
            "propose_entry",
            {
                "term": "Vys",
                "gloss": "Raw magical energy.",
                "evidence": [
                    {"post_id": 1001, "quote": "channeled Vys into the cloak"}
                ],
            },
        )
        assert json.loads(dispatcher.dispatch(propose))["ok"] is True
        alias = tc(
            "add_alias",
            {
                "term": "Vys",
                "alias": "Vys",  # re-registering the term itself
                "evidence": {"post_id": 1001, "quote": "channeled Vys into the cloak"},
            },
        )
        assert json.loads(dispatcher.dispatch(alias))["ok"] is True
        # Second registration of the same alias: ok, no IntegrityError.
        assert json.loads(dispatcher.dispatch(alias))["ok"] is True

    def test_integrity_error_becomes_error_payload(self, env):
        import sqlite3 as _sqlite3

        corpus_path, annotator_path = env
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(annotator_path)
        store = GlossaryStore(conn, corpus.post_body)
        dispatcher = ToolDispatcher(
            store, corpus, StoryLog(conn), provenance=lambda: None
        )

        def broken(*args, **kwargs):
            raise _sqlite3.IntegrityError("UNIQUE constraint failed")

        store.add_alias = broken  # simulate a store-level integrity failure
        result = json.loads(
            dispatcher.dispatch(
                tc(
                    "add_alias",
                    {
                        "term": "Vys",
                        "alias": "vys",
                        "evidence": {"post_id": 1001, "quote": "q Vys"},
                    },
                )
            )
        )
        assert result["ok"] is False
        assert "UNIQUE constraint" in result["error"]


class TestRunnerSalience:
    def test_mention_count_drives_injection_priority(self, env):
        """Runner-level: more-cited entry survives budget pressure."""
        corpus_path, annotator_path = env
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(annotator_path)
        from terrarium_annotator.glossary import Evidence, Provenance

        store = GlossaryStore(conn, corpus.post_body)
        prov = Provenance(thread_id=101, pass_id="t")
        store.propose_entry(
            term="Vys",
            gloss="Raw magical energy.",
            evidence=[
                Evidence(1001, "channeled Vys into the cloak"),
                Evidence(1002, "The Vys flared blue."),
            ],
            provenance=prov,
            tags=("mechanic",),
        )
        store.propose_entry(
            term="Suresh",
            gloss="An archmagos.",
            evidence=[Evidence(1003, "He met Suresh at the library.")],
            provenance=prov,
        )
        # Both terms appear in thread 101 batch 0 text; card budget fits one.
        model = ScriptedModel([ChatResponse(content="gist b0")])
        from terrarium_annotator.runner import Runner, RunnerConfig

        runner = Runner(
            corpus,
            StoryLog(conn),
            store,
            model,
            conn,
            RunnerConfig(pass_id="t", context_tokens=30, card_budget_fraction=0.2),
        )
        runner.run(max_batches=1, only_threads=[101])
        request = model.requests[0]["messages"][1]["content"]
        cards = request.split("<known_glossary>")[1].split("</known_glossary>")[0]
        assert "Vys" in cards and "Suresh" not in cards


class TestCheckpointSafety:
    def test_failed_batch_never_checkpointed(self, env):
        """An EmptyResponseError mid-run must not advance run_state or log a
        gist for the failed batch — resume re-attempts it."""
        from terrarium_annotator.llm.omp_rpc import EmptyResponseError

        corpus_path, annotator_path = env
        script = [
            ChatResponse(
                content=None,
                tool_calls=(
                    tc(
                        "propose_entry",
                        {
                            "term": "Vys",
                            "gloss": "Raw magical energy.",
                            "evidence": [
                                {
                                    "post_id": 1001,
                                    "quote": "channeled Vys into the cloak",
                                }
                            ],
                            "tags": ["mechanic"],
                        },
                    ),
                ),
            ),
            ChatResponse(content="Mik channels Vys into the cloak."),
            EmptyResponseError("agent ended with no assistant text"),
        ]
        runner, conn = make_runner(corpus_path, annotator_path, ScriptedModel(script))
        with pytest.raises(EmptyResponseError):
            runner.run()
        assert load_run_state(conn, "test-pass") == (101, 1)
        assert runner.memory.log_len() == 1  # only batch 0's gist

        diagnostics = conn.execute(
            "SELECT COUNT(*) FROM transcript WHERE role = 'system'"
            " AND content LIKE 'LLM call failed: EmptyResponseError%'"
        ).fetchone()[0]
        assert diagnostics == 1
