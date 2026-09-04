"""CLI entry point: `terrarium-annotator run|verify ...`.

`run` drives the annotator over the corpus via an omp-RPC client (Kimi
subscription); `verify` is model-free and works on any annotator DB +
corpus pair.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import GlossaryStore
from terrarium_annotator.llm import ChatClient, OmpRpcClient, RecordingClient
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.runner import Runner, RunnerConfig
from terrarium_annotator.state import connect_annotator_db
from terrarium_annotator.tools import ToolDispatcher
from terrarium_annotator.verify import verify


def parse_threads(value: str) -> list[int]:
    """Parse `--threads 30265887,30305969` into thread IDs."""
    try:
        return [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--threads takes comma-separated integers: {exc}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="terrarium-annotator")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Annotate the corpus via an LLM pass")
    run.add_argument("--corpus-db", required=True)
    run.add_argument("--annotator-db", required=True)
    run.add_argument("--model", default="kimi-k2.5")
    run.add_argument("--pass-id", default="run")
    run.add_argument("--max-batches", type=int, default=None)
    run.add_argument(
        "--threads",
        type=parse_threads,
        default=None,
        help="Comma-separated thread IDs; pass covers exactly "
        "these, chronologically, ignoring checkpoints",
    )
    run.add_argument("--timeout", type=float, default=300.0)
    run.add_argument(
        "--record", default=None, help="Append raw request/response JSONL here (L4)"
    )

    chat = sub.add_parser(
        "chat", help="Talk to the archivist about the glossary/story (read-only)"
    )
    chat.add_argument("--corpus-db", required=True)
    chat.add_argument("--annotator-db", required=True)
    chat.add_argument("--model", default="kimi-k2.5")
    chat.add_argument("--timeout", type=float, default=300.0)
    chat.add_argument(
        "--once", default=None, help="Ask one question and exit (non-interactive)"
    )

    verify_parser = sub.add_parser(
        "verify", help="Check annotator DB invariants against the corpus"
    )
    verify_parser.add_argument("--corpus-db", required=True)
    verify_parser.add_argument("--annotator-db", required=True)
    return parser


def run_pass(
    args: argparse.Namespace, client_factory: Callable[[str], ChatClient]
) -> int:
    """Wire stores + client and run. client_factory takes the model name."""
    corpus = CorpusReader(args.corpus_db)
    conn = connect_annotator_db(args.annotator_db)
    memory = StoryLog(conn)
    glossary = GlossaryStore(conn, corpus.post_body)
    client: ChatClient = client_factory(args.model)
    if args.record:
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        client = RecordingClient(client, args.record)
    runner = Runner(
        corpus,
        memory,
        glossary,
        client,
        conn,
        RunnerConfig(pass_id=args.pass_id),
    )
    runner.run(max_batches=args.max_batches, only_threads=args.threads)
    return 0


def main(
    argv: list[str] | None = None,
    client_factory: Callable[[str], ChatClient] | None = None,
) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "verify":
        conn = sqlite3.connect(f"file:{args.annotator_db}?mode=ro", uri=True)
        with CorpusReader(args.corpus_db) as corpus:
            violations = verify(conn, corpus)
        if not violations:
            print("verify: all invariants hold")
            return 0
        print(f"verify: {len(violations)} violation(s)")
        for v in violations:
            print(f"  [{v.check}] {v.detail}")
        return 1

    if args.command == "chat":
        from terrarium_annotator.chat import (
            CHAT_SYSTEM_PROMPT,
            READONLY_TOOLS,
            chat_turn,
            repl,
        )

        corpus = CorpusReader(args.corpus_db)
        conn = connect_annotator_db(args.annotator_db)
        dispatcher = ToolDispatcher(
            GlossaryStore(conn, corpus.post_body),
            corpus,
            StoryLog(conn),
            provenance=lambda: None,  # chat never writes; no provenance needed
            allowed=READONLY_TOOLS,
        )
        factory = client_factory or (
            lambda model: OmpRpcClient(model=model, timeout=args.timeout)
        )
        client = factory(args.model)
        if args.once is not None:
            messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
            messages.append({"role": "user", "content": args.once})
            print(chat_turn(messages, client, dispatcher))
            return 0
        repl(client, dispatcher)
        return 0

    if args.command == "run":
        factory = client_factory or (
            lambda model: OmpRpcClient(model=model, timeout=args.timeout)
        )
        try:
            return run_pass(args, factory)
        except ValueError as exc:  # e.g. unknown --threads IDs
            print(f"run: {exc}", file=sys.stderr)
            return 2

    return 2


if __name__ == "__main__":
    sys.exit(main())
