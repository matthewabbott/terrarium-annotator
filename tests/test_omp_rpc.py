"""Tests for OmpRpcClient (goal criterion 2): launch invariant, preflight,
tool-call parsing round trips, failure modes. All against fake_omp.py —
no real omp, no model spend."""

from __future__ import annotations

import json
import os
import sys

import pytest

from terrarium_annotator.llm import ChatClientError, ToolCall
from terrarium_annotator.llm.omp_rpc import (
    OmpRpcClient,
    parse_response_text,
    serialize_messages,
)

FAKE = os.path.join(os.path.dirname(__file__), "fake_omp.py")


def make_client(tmp_path, script, env=None, **kwargs):
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script))
    monkey = pytest.MonkeyPatch()
    monkey.setenv("FAKE_OMP_SCRIPT", str(script_path))
    for k, v in (env or {}).items():
        monkey.setenv(k, v)
    kwargs.setdefault("timeout", 10.0)
    client = OmpRpcClient(command=[sys.executable, FAKE, "--no-tools"], **kwargs)
    return client, monkey


class TestInvariant:
    def test_default_command_includes_no_tools(self):
        assert "--no-tools" in OmpRpcClient()._command

    def test_custom_command_without_flag_rejected(self):
        with pytest.raises(ChatClientError, match="--no-tools"):
            OmpRpcClient(command=[sys.executable, FAKE])

    def test_custom_command_with_flag_accepted(self):
        client = OmpRpcClient(command=[sys.executable, FAKE, "--no-tools"])
        assert client.model == "kimi-k2.5"


class TestExchange:
    def test_tool_call_round_trip(self, tmp_path):
        text = (
            'Adding that now.\n<tool_call>{"name": "propose_entry", '
            '"arguments": {"term": "Vys", "gloss": "energy"}}</tool_call>'
        )
        client, monkey = make_client(tmp_path, [{"text": text}])
        try:
            resp = client.chat(
                [{"role": "user", "content": "hi"}],
                tools=[
                    {
                        "function": {
                            "name": "propose_entry",
                            "description": "d",
                            "parameters": {},
                        }
                    }
                ],
            )
        finally:
            monkey.undo()
        assert resp.content == "Adding that now."
        assert resp.tool_calls == (
            ToolCall(
                name="propose_entry",
                arguments={"term": "Vys", "gloss": "energy"},
                id="text_0",
            ),
        )

    def test_malformed_tool_call_tolerated(self, tmp_path):
        client, monkey = make_client(
            tmp_path, [{"text": "x <tool_call>{bad}</tool_call>"}]
        )
        try:
            resp = client.chat([{"role": "user", "content": "hi"}])
        finally:
            monkey.undo()
        assert resp.tool_calls == ()
        assert "malformed tool_call ignored" in resp.content

    def test_preflight_rejects_exposed_tools(self, tmp_path):
        client, monkey = make_client(
            tmp_path, [{"text": "hi"}], env={"FAKE_OMP_TOOLS": '["bash"]'}
        )
        try:
            with pytest.raises(ChatClientError, match="preflight"):
                client.chat([{"role": "user", "content": "hi"}])
        finally:
            monkey.undo()

    def test_set_model_failure_raises(self, tmp_path):
        client, monkey = make_client(
            tmp_path, [{"text": "hi"}], env={"FAKE_OMP_FAIL_SET_MODEL": "1"}
        )
        try:
            with pytest.raises(ChatClientError, match="no such model"):
                client.chat([{"role": "user", "content": "hi"}])
        finally:
            monkey.undo()

    def test_timeout_when_silent(self, tmp_path):
        client, monkey = make_client(
            tmp_path, [{"text": "hi"}], env={"FAKE_OMP_HANG": "1"}, timeout=1.0
        )
        try:
            with pytest.raises(ChatClientError, match="timed out"):
                client.chat([{"role": "user", "content": "hi"}])
        finally:
            monkey.undo()


class TestSerialization:
    def test_parse_response_text_strips_blocks(self):
        resp = parse_response_text(
            'a <tool_call>{"name": "t", "arguments": {}}</tool_call> b'
        )
        assert resp.content == "a  b"
        assert resp.tool_calls[0].name == "t"

    def test_serialize_full_history(self):
        tools = [
            {
                "function": {
                    "name": "t",
                    "description": "d",
                    "parameters": {"type": "object"},
                }
            }
        ]
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "batch text"},
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {"name": "t", "arguments": '{"a": 1}'},
                    }
                ],
            },
            {
                "role": "tool",
                "name": "t",
                "content": '{"ok": true}',
                "tool_call_id": "1",
            },
        ]
        out = serialize_messages(messages, tools)
        assert "<system>" in out and "sys" in out
        assert "<instructions>" in out and '"name": "t"' in out
        assert "batch text" in out
        assert "<previous_assistant>" in out and "<tool_call>" in out
        assert '<tool_result name="t">' in out and '{"ok": true}' in out
