"""L0 tests for usage telemetry (docs/plan/aspirations.md §NEXT).

Covers: record contents over ScriptedModel, null-provider-usage behavior,
failed/retry attempts as first-class records, and the load-bearing
internal-retry proof: OmpRpcClient retries INSIDE chat(), invisible to an
outer wrapper unless the attempt-observer seam reports them (fake_omp.py,
no real model).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from terrarium_annotator.llm import (
    ChatClientError,
    ChatResponse,
    InstrumentedClient,
    OmpRpcClient,
    OpenAICompatibleClient,
    ScriptedModel,
    ToolCall,
    UsageLog,
    make_run_id,
    summarize,
    usage_log_path,
)

FAKE = os.path.join(os.path.dirname(__file__), "fake_omp.py")


def make_wrapped(tmp_path, inner, run_id="test-run", call_type="annotation"):
    log = UsageLog(tmp_path / "usage.jsonl")
    return InstrumentedClient(inner, log, run_id=run_id, call_type=call_type), log


def read_records(log):
    log.close()
    with log.path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestScriptedPath:
    def test_success_record_contents(self, tmp_path):
        inner = ScriptedModel(
            [
                ChatResponse(
                    content="Done.",
                    tool_calls=(ToolCall(name="propose_entry", arguments={"t": 1}),),
                )
            ]
        )
        client, log = make_wrapped(tmp_path, inner)
        client.set_context({"system": 100, "cards": 50})
        messages = [
            {"role": "system", "content": "s" * 100},
            {"role": "user", "content": "u" * 40},
        ]
        tools = [
            {
                "function": {
                    "name": "propose_entry",
                    "description": "d",
                    "parameters": {},
                }
            }
        ]
        resp = client.chat(messages, tools=tools)
        assert resp.content == "Done."

        (rec,) = read_records(log)
        assert rec["run_id"] == "test-run"
        assert rec["call_type"] == "annotation"
        assert rec["seq"] == 1
        assert rec["status"] == "success"
        assert rec["error_type"] is None
        assert rec["attempts"] == [
            {
                "attempt": 1,
                "status": "success",
                "error_type": None,
                "duration_s": rec["attempts"][0]["duration_s"],
            }
        ]
        assert rec["attempts"][0]["duration_s"] >= 0
        # prompt = system 100 + user 40 + serialized tool schemas
        assert rec["prompt_chars"] == 140 + len(json.dumps(tools))
        assert rec["completion_chars"] == len("Done.") + len("propose_entry") + len(
            json.dumps({"t": 1})
        )
        assert rec["tool_calls"] == 1
        # No provider usage on ScriptedModel — null, never invented.
        assert rec["usage"] is None
        assert rec["est_prompt_tokens"] == max(1, rec["prompt_chars"] // 4)
        # Reported components exact; unreported marked unknown.
        assert rec["context"] == {
            "system": 100,
            "cards": 50,
            "digest": "unknown",
            "scene": "unknown",
        }

    def test_provider_usage_recorded_verbatim_when_reported(self, tmp_path):
        usage = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
        inner = ScriptedModel([ChatResponse(content="x", raw={"usage": usage})])
        client, log = make_wrapped(tmp_path, inner)
        client.chat([{"role": "user", "content": "hi"}])
        (rec,) = read_records(log)
        assert rec["usage"] == usage

    def test_error_then_success_both_recorded(self, tmp_path):
        inner = ScriptedModel(
            [ChatClientError("boom"), ChatResponse(content="recovered")]
        )
        client, log = make_wrapped(tmp_path, inner, call_type="researcher")
        with pytest.raises(ChatClientError, match="boom"):
            client.chat([{"role": "user", "content": "hi"}])
        client.chat([{"role": "user", "content": "hi"}])

        err, ok = read_records(log)
        assert err["seq"] == 1 and ok["seq"] == 2
        assert err["status"] == "error"
        assert err["error_type"] == "ChatClientError"
        assert err["completion_chars"] is None
        assert err["usage"] is None
        assert [a["status"] for a in err["attempts"]] == ["error"]
        assert err["attempts"][0]["error_type"] == "ChatClientError"
        assert ok["status"] == "success"
        assert ok["call_type"] == "researcher"

    def test_unknown_context_component_rejected(self, tmp_path):
        client, _ = make_wrapped(tmp_path, ScriptedModel([]))
        with pytest.raises(ValueError, match="unknown context components"):
            client.set_context({"cards": 1, "bogus": 2})


class TestInternalRetrySeam:
    """The load-bearing tests: internal retries must surface as attempts."""

    def test_omp_rpc_retry_records_both_attempts(self, tmp_path, monkeypatch):
        # First process returns an empty assistant text (EmptyResponseError),
        # the retry process answers for real. Counter carries position
        # across the two fresh processes.
        script = tmp_path / "script.json"
        script.write_text(json.dumps([{"text": ""}, {"text": "real answer"}]))
        monkeypatch.setenv("FAKE_OMP_SCRIPT", str(script))
        monkeypatch.setenv("FAKE_OMP_COUNTER", str(tmp_path / "counter"))
        inner = OmpRpcClient(
            command=[sys.executable, FAKE, "--no-tools"], timeout=10.0, attempts=2
        )
        client, log = make_wrapped(tmp_path, inner)

        resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "real answer"

        (rec,) = read_records(log)
        assert rec["status"] == "success"
        attempts = [
            (a["attempt"], a["status"], a["error_type"]) for a in rec["attempts"]
        ]
        assert attempts == [
            (1, "error", "EmptyResponseError"),
            (2, "success", None),
        ]

    def test_omp_rpc_persistent_failure_records_all_attempts(
        self, tmp_path, monkeypatch
    ):
        script = tmp_path / "script.json"
        script.write_text(json.dumps([{"text": ""}]))
        monkeypatch.setenv("FAKE_OMP_SCRIPT", str(script))
        monkeypatch.setenv("FAKE_OMP_COUNTER", str(tmp_path / "counter"))
        inner = OmpRpcClient(
            command=[sys.executable, FAKE, "--no-tools"], timeout=10.0, attempts=2
        )
        client, log = make_wrapped(tmp_path, inner)

        with pytest.raises(ChatClientError):
            client.chat([{"role": "user", "content": "hi"}])

        (rec,) = read_records(log)
        assert rec["status"] == "error"
        assert rec["error_type"] == "EmptyResponseError"
        assert [(a["attempt"], a["status"]) for a in rec["attempts"]] == [
            (1, "error"),
            (2, "error"),
        ]

    def test_omp_rpc_agent_end_usage_flows_to_record(self, tmp_path, monkeypatch):
        # Plumbing proof: whatever usage the runtime emits on agent_end is
        # captured verbatim into the record (field names undocumented).
        script = tmp_path / "script.json"
        script.write_text(json.dumps([{"text": "answer"}]))
        monkeypatch.setenv("FAKE_OMP_SCRIPT", str(script))
        monkeypatch.setenv(
            "FAKE_OMP_USAGE",
            json.dumps({"prompt_tokens": 99, "completion_tokens": 7}),
        )
        inner = OmpRpcClient(command=[sys.executable, FAKE, "--no-tools"], timeout=10.0)
        client, log = make_wrapped(tmp_path, inner)
        resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.raw["agent_end"]["usage"] == {
            "prompt_tokens": 99,
            "completion_tokens": 7,
        }
        (rec,) = read_records(log)
        assert rec["usage"] == {"prompt_tokens": 99, "completion_tokens": 7}

    def test_openai_retry_and_usage_recorded(self, tmp_path):
        from test_llm import StubServer, ok_body  # tests-dir sibling

        body = ok_body("fine")
        body["usage"] = {"prompt_tokens": 5, "completion_tokens": 2}
        server = StubServer([(500, "boom"), body])
        try:
            inner = OpenAICompatibleClient(server.url, max_retries=2)
            client, log = make_wrapped(tmp_path, inner)
            assert client.chat([{"role": "user", "content": "hi"}]).content == "fine"
        finally:
            server.close()

        (rec,) = read_records(log)
        assert [
            (a["attempt"], a["status"], a["error_type"]) for a in rec["attempts"]
        ] == [
            (1, "error", "HTTP500"),
            (2, "success", None),
        ]
        assert rec["usage"] == {"prompt_tokens": 5, "completion_tokens": 2}

    def test_openai_malformed_200_is_error_not_success(self, tmp_path):
        # 200 with an unusable envelope must NOT log a success attempt.
        from test_llm import StubServer  # tests-dir sibling

        server = StubServer([{"unexpected": "shape"}])
        try:
            inner = OpenAICompatibleClient(server.url, max_retries=1)
            client, log = make_wrapped(tmp_path, inner)
            with pytest.raises(ChatClientError):
                client.chat([{"role": "user", "content": "hi"}])
        finally:
            server.close()

        (rec,) = read_records(log)
        assert rec["status"] == "error"
        assert [(a["attempt"], a["status"]) for a in rec["attempts"]] == [(1, "error")]
        assert rec["attempts"][0]["error_type"] == "ChatClientError"

    def test_omp_rpc_launch_failure_records_attempt(self, tmp_path):
        # Popen OSError (missing binary): not retried today, re-raised —
        # and now recorded as an attempt rather than escaping invisibly.
        inner = OmpRpcClient(
            command=["/nonexistent/omp-binary", "--no-tools"], attempts=2
        )
        client, log = make_wrapped(tmp_path, inner)
        with pytest.raises(OSError):
            client.chat([{"role": "user", "content": "hi"}])

        (rec,) = read_records(log)
        assert rec["status"] == "error"
        assert rec["error_type"] == "FileNotFoundError"
        assert [
            (a["attempt"], a["status"], a["error_type"]) for a in rec["attempts"]
        ] == [(1, "error", "FileNotFoundError")]


class TestSummarize:
    def test_per_run_and_call_type_aggregates(self, tmp_path):
        inner = ScriptedModel(
            [
                ChatResponse(content="a" * 40),
                ChatClientError("boom"),
                ChatResponse(
                    content="b" * 8,
                    tool_calls=(ToolCall(name="fetch_entry", arguments={}),),
                    raw={"usage": {"prompt_tokens": 3}},
                ),
            ]
        )
        client, log = make_wrapped(tmp_path, inner, run_id="run-1")
        client.chat([{"role": "user", "content": "u" * 400}])
        client.set_call_type("merge-settle")
        with pytest.raises(ChatClientError):
            client.chat([{"role": "user", "content": "u" * 200}])
        client.chat([{"role": "user", "content": "u" * 80}])
        log.close()

        summary = summarize(log.path)
        run = summary["runs"]["run-1"]
        assert run["calls"] == 3
        assert run["errors"] == 1
        assert run["error_types"] == {"ChatClientError": 1}
        assert run["prompt_chars"] == 680
        assert run["completion_chars"] == 48 + len("fetch_entry") + len("{}")
        assert run["tool_calls"] == 1
        assert run["attempts"] == {"success": 2, "error": 1}
        assert run["provider_usage_records"] == 1

        by_type = summary["call_types"]
        assert by_type["annotation"]["calls"] == 1
        assert by_type["merge-settle"]["calls"] == 2
        assert by_type["merge-settle"]["attempts"] == {"success": 1, "error": 1}

    def test_summarize_directory(self, tmp_path):
        for name in ("a", "b"):
            inner = ScriptedModel([ChatResponse(content="x")])
            client, log = make_wrapped(tmp_path, inner, run_id=name)
            client.chat([{"role": "user", "content": "hi"}])
            log.close()
        summary = summarize(tmp_path)
        assert set(summary["runs"]) == {"a", "b"}


class TestRunIdentity:
    def test_run_id_and_path_sanitization(self, tmp_path):
        run_id = make_run_id("pass/x")
        assert run_id.startswith("pass/x-")
        path = usage_log_path(tmp_path, run_id)
        assert path.parent == tmp_path
        assert "/" not in path.name
        assert path.suffix == ".jsonl"
