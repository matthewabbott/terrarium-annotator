"""L2 tests for the LLM seam, per docs/plan T5: stub-server HTTP behavior
for OpenAICompatibleClient; exact replay for ScriptedModel/ReplayClient."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from terrarium_annotator.llm import (
    ChatClientError,
    ChatResponse,
    OpenAICompatibleClient,
    RecordingClient,
    ReplayClient,
    ScriptedModel,
    ToolCall,
)


class StubServer:
    """Minimal /v1/chat/completions stub running a queued behavior script."""

    def __init__(self, behaviors: list[dict | tuple[int, str]]):
        self.behaviors = behaviors
        self.requests: list[dict] = []
        self.paths: list[str] = []

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                outer.paths.append(self.path)
                outer.requests.append(json.loads(self.rfile.read(length)))
                idx = min(len(outer.requests) - 1, len(outer.behaviors) - 1)
                behavior = outer.behaviors[idx]
                if isinstance(behavior, tuple):
                    status, text = behavior
                    self.send_response(status)
                    self.end_headers()
                    self.wfile.write(text.encode())
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(behavior).encode())

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._thread.join()


def ok_body(content="hello", tool_calls=None):
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


@pytest.fixture
def stub():
    servers = []

    def make(behaviors):
        s = StubServer(behaviors)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


MESSAGES = [{"role": "user", "content": "hi"}]


class TestOpenAIClient:
    def test_content_response(self, stub):
        server = stub([ok_body("world")])
        client = OpenAICompatibleClient(server.url, model="m", max_retries=1)
        resp = client.chat(MESSAGES)
        assert resp.content == "world"
        assert resp.tool_calls == ()
        assert server.requests[0]["model"] == "m"
        assert server.requests[0]["messages"] == MESSAGES

    def test_endpoint_accepts_server_root(self, stub):
        server = stub([ok_body()])
        OpenAICompatibleClient(server.url).chat(MESSAGES)
        assert server.paths == ["/v1/chat/completions"]

    def test_endpoint_accepts_v1_base_without_doubling(self, stub):
        # Kimi-style base: https://host/coding/v1 — must not become /v1/v1/...
        server = stub([ok_body()])
        OpenAICompatibleClient(server.url + "/v1").chat(MESSAGES)
        assert server.paths == ["/v1/chat/completions"]

    def test_tool_calls_parsed(self, stub):
        tc = {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "propose_entry",
                "arguments": json.dumps({"term": "Vys"}),
            },
        }
        server = stub([ok_body(None, [tc])])
        client = OpenAICompatibleClient(server.url)
        resp = client.chat(MESSAGES)
        assert resp.tool_calls == (
            ToolCall(name="propose_entry", arguments={"term": "Vys"}, id="call_1"),
        )

    def test_retries_5xx_then_succeeds(self, stub):
        server = stub([(500, "boom"), (500, "boom"), ok_body("ok")])
        client = OpenAICompatibleClient(server.url, max_retries=3)
        assert client.chat(MESSAGES).content == "ok"
        assert len(server.requests) == 3

    def test_persistent_5xx_raises(self, stub):
        server = stub([(500, "boom")])
        client = OpenAICompatibleClient(server.url, max_retries=2)
        with pytest.raises(ChatClientError, match="after 2 attempts"):
            client.chat(MESSAGES)

    def test_4xx_fails_fast_no_retry(self, stub):
        server = stub([(400, "bad request")])
        client = OpenAICompatibleClient(server.url, max_retries=3)
        with pytest.raises(ChatClientError, match="HTTP 400"):
            client.chat(MESSAGES)
        assert len(server.requests) == 1

    def test_malformed_json_raises(self, stub):
        server = stub([(200, "not json{")])
        client = OpenAICompatibleClient(server.url, max_retries=1)
        with pytest.raises(ChatClientError, match="non-JSON"):
            client.chat(MESSAGES)

    def test_schema_drift_raises(self, stub):
        server = stub([{"unexpected": "shape"}])
        client = OpenAICompatibleClient(server.url, max_retries=1)
        with pytest.raises(ChatClientError, match="envelope"):
            client.chat(MESSAGES)

    def test_malformed_tool_call_arguments_raise(self, stub):
        tc = {
            "id": "c",
            "type": "function",
            "function": {"name": "x", "arguments": "{not json"},
        }
        server = stub([ok_body(None, [tc])])
        client = OpenAICompatibleClient(server.url, max_retries=1)
        with pytest.raises(ChatClientError, match="tool call"):
            client.chat(MESSAGES)


class TestScriptedModel:
    def test_replays_in_order_and_records(self):
        model = ScriptedModel(
            [ChatResponse(content="one"), ChatResponse(content="two")]
        )
        assert model.chat([{"role": "user", "content": "a"}]).content == "one"
        assert model.chat([{"role": "user", "content": "b"}]).content == "two"
        assert model.requests[0]["messages"][0]["content"] == "a"

    def test_scripted_exception(self):
        model = ScriptedModel([ChatClientError("boom")])
        with pytest.raises(ChatClientError, match="boom"):
            model.chat(MESSAGES)

    def test_exhaustion_raises(self):
        model = ScriptedModel([])
        with pytest.raises(ChatClientError, match="exhausted"):
            model.chat(MESSAGES)


class TestRecordingReplay:
    def test_roundtrip_and_idempotent_replay(self, tmp_path):
        record = tmp_path / "calls.jsonl"
        inner = ScriptedModel(
            [
                ChatResponse(content="r1"),
                ChatResponse(
                    content=None, tool_calls=(ToolCall(name="t", arguments={"a": 1}),)
                ),
            ]
        )
        client = RecordingClient(inner, record)
        client.chat([{"role": "user", "content": "m1"}], temperature=0.4)
        client.chat([{"role": "user", "content": "m2"}], temperature=0.4)

        replay = ReplayClient(record)
        r1 = replay.chat([{"role": "user", "content": "m1"}], temperature=0.4)
        r2 = replay.chat([{"role": "user", "content": "m2"}], temperature=0.4)
        assert r1.content == "r1"
        assert r2.tool_calls[0].arguments == {"a": 1}

    def test_replay_divergence_detected(self, tmp_path):
        record = tmp_path / "calls.jsonl"
        client = RecordingClient(ScriptedModel([ChatResponse(content="r")]), record)
        client.chat([{"role": "user", "content": "m1"}])

        replay = ReplayClient(record)
        with pytest.raises(ChatClientError, match="divergence"):
            replay.chat([{"role": "user", "content": "DIFFERENT"}])
