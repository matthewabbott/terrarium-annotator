"""L0 tests for the quota circuit breaker (docs/design/prompt-laddering.md
§usage circuit-breaking): probe parsing + fail-safe, breaker threshold
semantics, Runner/Researcher halt behavior with injected fakes. No real
`omp` invocation, no live model.
"""

from __future__ import annotations

import json
import sys

import pytest
from test_runner import build_corpus, make_runner  # tests-dir siblings

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import GlossaryStore
from terrarium_annotator.llm import ChatResponse, ScriptedModel
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.quota import (
    QuotaExceeded,
    QuotaProbeError,
    make_quota_breaker,
    probe_weekly_fraction,
)
from terrarium_annotator.research import Researcher
from terrarium_annotator.state import connect_annotator_db, load_run_state

USAGE_JSON = {
    "reports": [
        {
            "provider": "kimi-code",
            "limits": [
                {
                    "window": {"id": "5h"},
                    "amount": {"usedFraction": 0.9},
                },
                {
                    "window": {"id": "7d"},
                    "amount": {"usedFraction": 0.42},
                },
            ],
        }
    ]
}


def probe_command(payload: str, exit_code: int = 0) -> list[str]:
    return [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.write({payload!r}); sys.exit({exit_code})",
    ]


class TestProbe:
    def test_parses_7d_window(self):
        frac = probe_weekly_fraction(probe_command(json.dumps(USAGE_JSON)))
        assert frac == 0.42  # the 7d window, not the 5h one

    def test_nonzero_exit_halts(self):
        with pytest.raises(QuotaProbeError, match="exited 1"):
            probe_weekly_fraction(probe_command("{}", exit_code=1))

    def test_non_json_halts(self):
        with pytest.raises(QuotaProbeError, match="not JSON"):
            probe_weekly_fraction(probe_command("not json{"))

    def test_missing_weekly_window_halts(self):
        with pytest.raises(QuotaProbeError, match="no 7-day window"):
            probe_weekly_fraction(probe_command(json.dumps({"reports": []})))

    def test_malformed_fields_halt(self):
        with pytest.raises(QuotaProbeError, match="missing fields"):
            probe_weekly_fraction(
                probe_command(
                    json.dumps({"reports": [{"limits": [{"window": {"id": "7d"}}]}]})
                )
            )


class TestBreaker:
    def test_below_threshold_passes(self):
        make_quota_breaker(0.50, probe=lambda: 0.49)()

    def test_at_threshold_halts(self):
        with pytest.raises(QuotaExceeded, match="50%"):
            make_quota_breaker(0.50, probe=lambda: 0.50)()

    def test_above_threshold_halts(self):
        with pytest.raises(QuotaExceeded, match="90%"):
            make_quota_breaker(0.50, probe=lambda: 0.90)()

    def test_probe_failure_propagates(self):
        def bad_probe():
            raise QuotaProbeError("omp missing")

        with pytest.raises(QuotaProbeError):
            make_quota_breaker(0.50, probe=bad_probe)()


class TestRunnerHalt:
    def test_halt_preserves_checkpoint(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        annotator_path = tmp_path / "annotator.db"
        model = ScriptedModel([ChatResponse(content=f"gist {i}") for i in range(20)])
        runner, conn = make_runner(corpus_path, annotator_path, model)

        calls = []

        def check():
            calls.append(1)
            if len(calls) >= 2:
                raise QuotaExceeded("weekly quota at 50% >= threshold 50%")

        runner.quota_check = check
        with pytest.raises(QuotaExceeded, match="50%"):
            runner.run()

        # First batch processed + checkpointed; halt before the second.
        assert load_run_state(conn, "test-pass") == (101, 1)
        # Resume re-attempts the interrupted unit (batch 1 of thread 101).
        runner2, conn2 = make_runner(
            corpus_path, annotator_path, ScriptedModel([ChatResponse(content="g")])
        )
        runner2.run(max_batches=1)
        assert load_run_state(conn2, "test-pass") == (101, 2)

    def test_disabled_breaker_zero_behavior_change(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        runner, conn = make_runner(
            corpus_path,
            tmp_path / "annotator.db",
            ScriptedModel([ChatResponse(content=f"gist {i}") for i in range(20)]),
        )
        assert runner.quota_check is None
        runner.run()
        assert load_run_state(conn, "test-pass") == (103, 1)


class TestResearcherHalt:
    def make_researcher(self, tmp_path, model, quota_check=None):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(tmp_path / "annotator.db")
        return Researcher(
            corpus,
            GlossaryStore(conn, corpus.post_body),
            StoryLog(conn),
            model,
            conn,
            quota_check=quota_check,
        )

    def test_halt_at_session_start_makes_no_calls(self, tmp_path):
        model = ScriptedModel([ChatResponse(content="report")])
        r = self.make_researcher(
            tmp_path,
            model,
            quota_check=lambda: (_ for _ in ()).throw(QuotaExceeded("halt")),
        )
        with pytest.raises(QuotaExceeded):
            r.research()
        assert model.requests == []

    def test_halt_mid_round_stops_session(self, tmp_path):
        from terrarium_annotator.llm import ToolCall

        model = ScriptedModel(
            [
                ChatResponse(
                    content=None,
                    tool_calls=(ToolCall("search_glossary", {"query": "vys"}, "c1"),),
                ),
                ChatResponse(content="report"),
            ]
        )
        calls = []

        def check():
            # Call 1 = session start, call 2 = round 1 (proceeds),
            # call 3 = round 2 (halt — after exactly one LLM round).
            calls.append(1)
            if len(calls) >= 3:
                raise QuotaExceeded("halt")

        r = self.make_researcher(tmp_path, model, quota_check=check)
        with pytest.raises(QuotaExceeded):
            r.research()
        # Exactly one LLM round happened before the halt.
        assert len(model.requests) == 1

    def test_disabled_breaker_runs_normally(self, tmp_path):
        # Content-only replies before any tool work are nudged (cap 2):
        # announcement + 2 nudged replies, then the report is accepted.
        model = ScriptedModel([ChatResponse(content="report")] * 3)
        r = self.make_researcher(tmp_path, model)
        assert r.research() == "report"
