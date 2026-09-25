"""L0 tests for provider wiring: --provider/--base-url/--no-thinking/
--top-p flag parsing, build_provider_client construction, payload
passthrough of sampling knobs (stub server, no live calls), run_meta
sampling provenance. Default behavior (kimi path) unchanged.
"""

from __future__ import annotations

import json

from test_llm import StubServer, ok_body  # tests-dir sibling

from terrarium_annotator.cli import build_parser, build_provider_client
from terrarium_annotator.llm import OmpRpcClient, OpenAICompatibleClient


def parse(argv):
    return build_parser().parse_args(argv)


RUN_ARGS = ["run", "--corpus-db", "c.db", "--annotator-db", "a.db"]


class TestProviderFlags:
    def test_defaults_are_kimi(self):
        args = parse(RUN_ARGS)
        assert args.provider == "kimi"
        assert args.base_url is None
        assert args.no_thinking is False
        assert args.top_p is None
        assert args.context_tokens is None

    def test_local_flags_parse(self):
        args = parse(
            RUN_ARGS
            + [
                "--provider",
                "local",
                "--base-url",
                "http://host:9999/v1",
                "--no-thinking",
                "--top-p",
                "0.95",
                "--context-tokens",
                "131072",
            ]
        )
        assert args.provider == "local"
        assert args.no_thinking is True
        assert args.top_p == 0.95
        assert args.context_tokens == 131072

    def test_all_model_commands_have_provider_flags(self):
        for cmd in ("run", "chat", "research", "adjudicate"):
            args = parse([cmd, "--corpus-db", "c.db", "--annotator-db", "a.db"])
            assert args.provider == "kimi" and hasattr(args, "base_url"), cmd


class TestBuildProviderClient:
    def test_kimi_default_builds_rpc(self):
        args = parse(RUN_ARGS)
        client = build_provider_client(args)
        assert isinstance(client, OmpRpcClient)
        assert client.model == "kimi-k2.5"

    def test_local_builds_openai_client_with_knobs(self):
        args = parse(
            RUN_ARGS
            + [
                "--provider",
                "local",
                "--base-url",
                "http://host:9999",
                "--model",
                "DeepSeek-v4.1-Flash-EXL3",
                "--no-thinking",
                "--top-p",
                "0.95",
            ]
        )
        client = build_provider_client(args)
        assert isinstance(client, OpenAICompatibleClient)
        assert client._endpoint == "http://host:9999/v1/chat/completions"
        assert client.model == "DeepSeek-v4.1-Flash-EXL3"
        assert client.chat_template_kwargs == {"enable_thinking": False}
        assert client.top_p == 0.95

    def test_env_default_base_url(self, monkeypatch):
        monkeypatch.setenv("TERRARIUM_BASE_URL", "http://env-host:1/v1")
        args = parse(RUN_ARGS + ["--provider", "local"])
        client = build_provider_client(args)
        assert client._endpoint == "http://env-host:1/v1/chat/completions"


class TestPayloadPassthrough:
    def test_sampling_knobs_reach_payload(self):
        server = StubServer([ok_body("ok")])
        try:
            args = parse(
                RUN_ARGS
                + [
                    "--provider",
                    "local",
                    "--base-url",
                    server.url,
                    "--no-thinking",
                    "--top-p",
                    "0.95",
                ]
            )
            client = build_provider_client(args)
            client.chat([{"role": "user", "content": "hi"}])
            payload = server.requests[0]
            assert payload["chat_template_kwargs"] == {"enable_thinking": False}
            assert payload["top_p"] == 0.95
        finally:
            server.close()

    def test_knobs_absent_by_default(self):
        server = StubServer([ok_body("ok")])
        try:
            client = OpenAICompatibleClient(server.url, model="m")
            client.chat([{"role": "user", "content": "hi"}])
            payload = server.requests[0]
            assert "chat_template_kwargs" not in payload
            assert "top_p" not in payload
            assert payload["model"] == "m"
        finally:
            server.close()


class TestRunMetaSampling:
    def test_sampling_recorded(self, tmp_path):
        from test_runner import SCRIPT, build_corpus

        from terrarium_annotator.cli import main
        from terrarium_annotator.llm import ScriptedModel
        from terrarium_annotator.state import load_run_meta

        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        annotator_path = tmp_path / "a.db"
        code = main(
            [
                "run",
                "--corpus-db",
                str(corpus_path),
                "--annotator-db",
                str(annotator_path),
                "--provider",
                "local",
                "--base-url",
                "http://unused:1/v1",
                "--no-thinking",
                "--top-p",
                "0.95",
                "--context-tokens",
                "131072",
                "--usage-dir",
                str(tmp_path / "usage"),
            ],
            client_factory=lambda model: ScriptedModel(list(SCRIPT)),
            quota_check_factory=lambda threshold: None,
        )
        assert code == 0
        import sqlite3

        conn = sqlite3.connect(annotator_path)
        sampling = json.loads(load_run_meta(conn, "sampling"))
        assert sampling == {
            "provider": "local",
            "thinking": False,
            "top_p": 0.95,
            "base_url": "http://unused:1/v1",
            "context_tokens": 131072,
        }
        cfg = json.loads(load_run_meta(conn, "config"))
        assert cfg["context_tokens"] == 131072
