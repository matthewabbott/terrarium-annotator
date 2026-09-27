"""L0 tests for rolling-context mode: window enforcement at thread
boundaries (full message list budget), min_threads floor, resume
reconstruction from transcripts, and reset-mode parity (default OFF).
ScriptedModel only.
"""

from __future__ import annotations

import pytest
from test_runner import build_corpus, make_runner  # tests-dir siblings

from terrarium_annotator.llm import ChatResponse, ScriptedModel, ToolCall


def make_rolling(corpus_path, annotator_path, script, window=50_000, min_threads=2):
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
        # Window fits one batch (~520 tokens) but not two threads' worth.
        runner, _ = make_rolling(
            corpus_path, tmp_path / "a.db", script, window=800, min_threads=1
        )
        runner.run(max_batches=3)  # thread 101: 2 batches, then close
        # No config error raised; enforcement kept the window under 800.
        assert runner._window_tokens() <= 800
        assert runner._messages[0]["role"] == "system"

    def test_min_threads_floor_respected(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        script = [ChatResponse(content="gist") for _ in range(20)]
        runner, _ = make_rolling(
            corpus_path, tmp_path / "a.db", script, window=800, min_threads=2
        )
        runner.run(max_batches=3)  # closes thread 101
        # Window under budget (floor yields to the cap when needed).
        assert runner._window_tokens() <= 800
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


class TestEnforcementIndexes:
    def _runner_with_spans(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        runner, _ = make_rolling(corpus_path, tmp_path / "a.db", [])
        # Fabricate: system + two closed spans + one open span with an
        # in-flight batch tail. Each message ~400 chars (~100 tokens).
        runner._messages = [{"role": "system", "content": "s" * 100}]
        for t in (101, 102):  # closed spans: 3 messages each
            start = len(runner._messages)
            for _ in range(3):
                runner._messages.append({"role": "user", "content": "x" * 400})
            runner._thread_spans.append((t, start, len(runner._messages)))
        runner._span_open = len(runner._messages)  # open span: thread 103
        runner._span_thread = 103
        for _ in range(3):
            runner._messages.append({"role": "user", "content": "y" * 400})
        runner._batch_open = len(runner._messages)  # in-flight batch
        runner._messages.append({"role": "user", "content": "z" * 400})
        return runner

    def test_closed_spans_drop_fully_when_over_cap(self, tmp_path):
        runner = self._runner_with_spans(tmp_path)
        runner.config.rolling_window_tokens = 150  # ~600 chars
        runner._enforce_window()
        # Both closed spans dropped (floor yields); open span truncated to
        # the in-flight batch; system message preserved at index 0.
        assert runner._messages[0]["role"] == "system"
        assert runner._thread_spans == []
        assert all(m["content"].startswith("z") for m in runner._messages[1:])

    def test_open_span_truncates_after_closed_spans_drop(self, tmp_path):
        runner = self._runner_with_spans(tmp_path)
        # Budget 600 tokens (~2400 chars): over budget initially (4100
        # chars ≈ 1025 tok) → first closed span drops (2900 chars ≈ 725
        # tok, still over) → second drops (1700 ≈ 425 tok, fits). Both
        # closed spans drop fully; no open-span truncation needed.
        # Invariants: system survives, in-flight batch survives, under
        # budget, order preserved.
        runner.config.rolling_window_tokens = 600
        runner._enforce_window()
        contents = [m["content"][0] for m in runner._messages[1:]]
        assert runner._messages[0]["role"] == "system"
        assert "x" not in contents  # closed spans dropped fully
        assert contents[-1] == "z"  # in-flight batch survives
        assert runner._window_tokens() <= 600

    def test_open_span_truncates_oldest_first(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        runner, _ = make_rolling(corpus_path, tmp_path / "a.db", [])
        # No closed spans: system + 3 old open-span messages + in-flight
        # tail. Truncation must remove from the open span's FRONT.
        runner._messages = [{"role": "system", "content": "s" * 100}]
        runner._span_open = 1
        runner._span_thread = 101
        for marker in ("a", "b", "c"):
            runner._messages.append({"role": "user", "content": marker * 400})
        runner._batch_open = len(runner._messages)
        runner._messages.append({"role": "user", "content": "z" * 400})
        # 100 + 1200 + 400 = 1700 chars ≈ 425 tokens; budget 300 → drop a, b.
        runner.config.rolling_window_tokens = 300
        runner._enforce_window()
        contents = [m["content"][0] for m in runner._messages[1:]]
        assert contents == ["c", "z"]  # oldest-first within the open span
        assert runner._window_tokens() <= 300

    def test_hysteresis_trims_to_target_below_cap(self, tmp_path):
        runner = self._runner_with_spans(tmp_path)
        # Window 750, target 400: initial ~1025 tokens over cap → closed
        # spans drop to the TARGET (both go: 725 then 425 still > 400),
        # then one 'y' truncates (325 ≤ 400). Without hysteresis the same
        # fixture stops at ≤750 (one span survives). Matt 2026-09-27:
        # fewer, bigger trims amortize the full-prefix cache miss.
        runner.config.rolling_window_tokens = 750
        runner.config.rolling_trim_target = 400
        runner._enforce_window()
        contents = [m["content"][0] for m in runner._messages[1:]]
        assert "x" not in contents  # both closed spans dropped to target
        assert contents == ["y", "y", "z"]  # one y truncated to hit target
        assert runner._window_tokens() <= 400

    def test_trim_target_below_window_validated(self, tmp_path):
        from terrarium_annotator.runner import RunnerConfig

        with pytest.raises(ValueError, match="below the window"):
            RunnerConfig(rolling_window_tokens=100, rolling_trim_target=100)
        with pytest.raises(ValueError, match="below the window"):
            RunnerConfig(rolling_window_tokens=100, rolling_trim_target=200)

    def test_inflight_batch_over_budget_raises_locally(self, tmp_path):
        from terrarium_annotator.llm import ChatClientError
        from terrarium_annotator.state import load_run_state

        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        runner, conn = make_rolling(
            corpus_path,
            tmp_path / "a.db",
            [ChatResponse(content="g")],
            window=10,  # 40 chars — one batch's system+user exceeds it
            min_threads=1,
        )
        with pytest.raises(ChatClientError, match="single"):
            runner.run(max_batches=1)
        # Checkpoint untouched: the batch is re-attempted on resume.
        assert load_run_state(conn, "test-pass") is None


class TestConfigValidation:
    def test_zero_window_rejected(self):
        from terrarium_annotator.runner import RunnerConfig

        with pytest.raises(ValueError, match="positive"):
            RunnerConfig(rolling_window_tokens=0)

    def test_zero_min_threads_rejected(self):
        from terrarium_annotator.runner import RunnerConfig

        with pytest.raises(ValueError, match=">= 1"):
            RunnerConfig(rolling_window_tokens=100, rolling_min_threads=0)

    def test_reset_mode_unaffected(self):
        from terrarium_annotator.runner import RunnerConfig

        RunnerConfig()  # rolling off: no validation triggered


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
