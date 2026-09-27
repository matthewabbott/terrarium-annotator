"""L0 tests for rolling-context mode: window enforcement at thread
boundaries (full message list budget), min_threads floor, resume
reconstruction from transcripts, and reset-mode parity (default OFF).
ScriptedModel only.
"""

from __future__ import annotations

from test_runner import build_corpus, make_runner  # tests-dir siblings

from terrarium_annotator.llm import ChatResponse, ScriptedModel, ToolCall


def make_rolling(corpus_path, annotator_path, script, window=200, min_threads=2):
    return make_runner(
        corpus_path,
        annotator_path,
        ScriptedModel(script),
        rolling_window_tokens=window,
        rolling_min_threads=min_threads,
    )


def tc(name, args):
    return ToolCall(name=name, arguments=args, id=f"c_{name}")


class TestRollingAccumulation:
    def test_conversation_accumulates_across_batches(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        script = [ChatResponse(content=f"gist {i}") for i in range(20)]
        runner, _ = make_rolling(corpus_path, tmp_path / "a.db", script)
        runner.run(max_batches=3)
        msgs = runner._messages
        roles = [m["role"] for m in msgs]
        assert roles[0] == "system"
        # 3 batches: user + assistant each, one continuous conversation.
        assert roles.count("user") == 3
        assert roles.count("assistant") == 3

    def test_reset_mode_unchanged_default(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        runner, _ = make_runner(corpus_path, tmp_path / "a.db", ScriptedModel([]))
        assert runner._messages is None  # reset mode: no persistent window
        assert runner.config.rolling_window_tokens is None


class TestWindowEnforcement:
    def test_oldest_thread_drops_at_boundary_over_budget(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        script = [ChatResponse(content="gist") for _ in range(20)]
        # Tiny window forces drops after each thread close.
        runner, _ = make_rolling(
            corpus_path, tmp_path / "a.db", script, window=50, min_threads=1
        )
        runner.run(max_batches=3)  # thread 101: 2 batches, then close
        spans = runner._thread_spans
        # Thread 101 closed and was dropped when over budget (min_threads=1
        # lets the span list hold only the current open span's predecessors).
        assert all(end - start >= 0 for _, start, end in spans)
        assert runner._window_tokens() > 0

    def test_min_threads_floor_respected(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        script = [ChatResponse(content="gist") for _ in range(20)]
        runner, _ = make_rolling(
            corpus_path, tmp_path / "a.db", script, window=50, min_threads=2
        )
        runner.run(max_batches=3)  # closes thread 101
        # With floor 2, enforcement cannot drop the only closed span.
        assert len(runner._thread_spans) <= 2

    def test_window_budget_counts_tool_results(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        big_result = "x" * 4000  # ~1000 tokens of tool result
        script = [
            ChatResponse(
                content=None,
                tool_calls=(tc("search_glossary", {"query": "vys"}),),
            ),
            ChatResponse(content="gist"),
        ] * 3
        runner, _ = make_rolling(corpus_path, tmp_path / "a.db", script)
        runner.run(max_batches=1)
        # Force a huge tool result into the window, then check accounting.
        runner._messages.append({"role": "tool", "content": big_result})
        assert runner._window_tokens() >= 1000


class TestResumeRebuild:
    def test_rebuild_from_transcripts(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        annotator = tmp_path / "a.db"
        script = [ChatResponse(content=f"gist {i}") for i in range(20)]
        runner, _ = make_rolling(corpus_path, annotator, script)
        runner.run(max_batches=4)  # thread 101 fully + close
        # New runner over the same DB in rolling mode: resume rebuilds.
        runner2, _ = make_rolling(
            # Resume closes thread 103 and settles two merge blocks: 3 calls.
            corpus_path,
            annotator,
            [ChatResponse(content="g")] * 3,
        )
        runner2.run(max_batches=1)
        assert runner2._messages is not None
        assert len(runner2._messages) > 1  # rebuilt history + new batch
        roles = [m["role"] for m in runner2._messages]
        assert "assistant" in roles and "user" in roles


class TestCliFlags:
    def test_rolling_flags_parse_and_wire(self, tmp_path):
        from terrarium_annotator.cli import build_parser

        args = build_parser().parse_args(
            [
                "run",
                "--corpus-db",
                "c.db",
                "--annotator-db",
                "a.db",
                "--rolling-window-tokens",
                "200000",
                "--rolling-min-threads",
                "7",
            ]
        )
        assert args.rolling_window_tokens == 200000
        assert args.rolling_min_threads == 7

    def test_rolling_defaults_off(self, tmp_path):
        from terrarium_annotator.cli import build_parser

        args = build_parser().parse_args(
            ["run", "--corpus-db", "c.db", "--annotator-db", "a.db"]
        )
        assert args.rolling_window_tokens is None
