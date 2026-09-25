"""CLI entry point: `terrarium-annotator run|verify ...`.

`run` drives the annotator over the corpus via an omp-RPC client (Kimi
subscription); `verify` is model-free and works on any annotator DB +
corpus pair.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import GlossaryStore
from terrarium_annotator.llm import (
    ChatClient,
    InstrumentedClient,
    OmpRpcClient,
    OpenAICompatibleClient,
    RecordingClient,
    UsageLog,
    format_summary,
    make_run_id,
    summarize,
    usage_log_path,
)
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.quota import (
    QuotaExceeded,
    QuotaProbeError,
    make_quota_breaker,
)
from terrarium_annotator.runner import Runner, RunnerConfig
from terrarium_annotator.state import connect_annotator_db, save_run_meta
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


LOCAL_DEFAULT_BASE_URL = "http://127.0.0.1:8888/v1"


def add_provider_args(cmd: argparse.ArgumentParser) -> None:
    """Provider selection flags, shared by all model-driving commands."""
    cmd.add_argument(
        "--provider",
        choices=["kimi", "local"],
        default="kimi",
        help="kimi = omp RPC subscription; local = OpenAI-compatible server",
    )
    cmd.add_argument(
        "--base-url",
        default=None,
        help="Local server base URL (default: $TERRARIUM_BASE_URL or "
        f"{LOCAL_DEFAULT_BASE_URL})",
    )
    cmd.add_argument(
        "--no-thinking",
        action="store_true",
        help="Local path: disable reasoning (chat_template_kwargs)",
    )
    cmd.add_argument("--top-p", type=float, default=None)


def build_provider_client(args: argparse.Namespace) -> ChatClient:
    """Construct the provider client from CLI args (local = OpenAI-
    compatible; kimi = omp RPC). Sampling knobs recorded by callers."""
    if args.provider == "local":
        base_url = (
            args.base_url
            or os.environ.get("TERRARIUM_BASE_URL")
            or LOCAL_DEFAULT_BASE_URL
        )
        kwargs: dict = {}
        if args.no_thinking:
            kwargs["chat_template_kwargs"] = {"enable_thinking": False}
        if args.top_p is not None:
            kwargs["top_p"] = args.top_p
        return OpenAICompatibleClient(
            base_url, model=args.model, timeout=args.timeout, **kwargs
        )
    return OmpRpcClient(model=args.model, timeout=args.timeout)


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
    run.add_argument(
        "--usage-dir",
        default="data/recordings/usage",
        help="Per-run usage telemetry JSONL directory (always recorded)",
    )
    run.add_argument(
        "--quota-breaker",
        type=float,
        default=0.50,
        help="Halt when 7-day Kimi quota usedFraction reaches this "
        "(default 0.50; <=0 disables)",
    )
    run.add_argument(
        "--prompt",
        default=None,
        help="Prompt file (default: prompts/reader-v2.md)",
    )
    run.add_argument(
        "--context-tokens",
        type=int,
        default=None,
        help="Model context window (default: 262144; card budget derives "
        "from it — set to the server's real window)",
    )
    add_provider_args(run)

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
    chat.add_argument(
        "--usage-dir",
        default="data/recordings/usage",
        help="Per-session usage telemetry JSONL directory (always recorded)",
    )
    add_provider_args(chat)

    research = sub.add_parser(
        "research", help="Top-down glossary work over the whole corpus"
    )
    research.add_argument("--corpus-db", required=True)
    research.add_argument("--annotator-db", required=True)
    research.add_argument("--model", default="kimi-k2.5")
    research.add_argument("--timeout", type=float, default=300.0)
    research.add_argument(
        "--focus", default=None, help="Session focus, e.g. 'aliases and retitles'"
    )
    research.add_argument("--record", default=None)
    research.add_argument(
        "--usage-dir",
        default="data/recordings/usage",
        help="Per-session usage telemetry JSONL directory (always recorded)",
    )
    research.add_argument(
        "--quota-breaker",
        type=float,
        default=0.50,
        help="Halt when 7-day Kimi quota usedFraction reaches this "
        "(default 0.50; <=0 disables)",
    )
    add_provider_args(research)

    adj = sub.add_parser(
        "adjudicate",
        help="Bounded quote-audit of shadow-flagged candidates "
        "(read/audit tools + propose_demotion ONLY; hard-wired allowlist)",
    )
    adj.add_argument("--corpus-db", required=True)
    adj.add_argument("--annotator-db", required=True)
    adj.add_argument("--model", default="kimi-k2.5")
    adj.add_argument("--timeout", type=float, default=300.0)
    adj.add_argument("--sample-size", type=int, default=50)
    adj.add_argument("--chunk-size", type=int, default=12)
    adj.add_argument(
        "--work-dir",
        default=None,
        help="Chunk reports + sample.json (default: data/adjudication/<run-id>)",
    )
    adj.add_argument(
        "--quota-breaker",
        type=float,
        default=0.50,
        help="Halt when 7-day Kimi quota usedFraction reaches this "
        "(default 0.50; <=0 disables)",
    )
    adj.add_argument(
        "--usage-dir",
        default="data/recordings/usage",
        help="Per-session usage telemetry JSONL directory (always recorded)",
    )
    add_provider_args(adj)

    verify_parser = sub.add_parser(
        "verify", help="Check annotator DB invariants against the corpus"
    )
    verify_parser.add_argument("--corpus-db", required=True)
    verify_parser.add_argument("--annotator-db", required=True)

    usage = sub.add_parser(
        "usage-summary", help="Aggregate usage telemetry per run / call type"
    )
    usage.add_argument(
        "path",
        help="Usage JSONL file or directory (default: data/recordings/usage)",
        nargs="?",
        default="data/recordings/usage",
    )

    ladder = sub.add_parser(
        "ladder-score", help="Scorecard for one variant DB (gold coverage + cost)"
    )
    ladder.add_argument("--corpus-db", required=True)
    ladder.add_argument("--annotator-db", required=True)
    ladder.add_argument(
        "--threads",
        type=parse_threads,
        required=True,
        help="Comma-separated thread IDs the variant processed",
    )
    ladder.add_argument("--usage-file", default=None)
    ladder.add_argument("--gold-set", default="data/exports/gold-set.json")
    ladder.add_argument("--name", default="variant")
    return parser


def _default_quota_factory(threshold: float) -> Callable[[], None] | None:
    """Real breaker: probe `omp usage`; <=0 disables (unchecked)."""
    return make_quota_breaker(threshold) if threshold > 0 else None


def run_pass(
    args: argparse.Namespace,
    client_factory: Callable[[str], ChatClient],
    quota_check_factory: Callable[[float], Callable[[], None] | None],
) -> int:
    """Wire stores + client and run. client_factory takes the model name."""
    corpus = CorpusReader(args.corpus_db)
    conn = connect_annotator_db(args.annotator_db)
    memory = StoryLog(conn)
    glossary = GlossaryStore(conn, corpus.post_body)
    client: ChatClient = client_factory(args.model)
    run_id = make_run_id(args.pass_id)
    log = UsageLog(usage_log_path(args.usage_dir, run_id))
    # Telemetry innermost: internal client retries stay observable even
    # when RecordingClient wraps outside it.
    instrumented = InstrumentedClient(
        client, log, run_id=run_id, call_type="annotation"
    )
    client = instrumented
    if args.record:
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        client = RecordingClient(client, args.record)
    runner = Runner(
        corpus,
        memory,
        glossary,
        client,
        conn,
        RunnerConfig(
            pass_id=args.pass_id,
            quota_threshold=args.quota_breaker,
            prompt_file=args.prompt,
            context_tokens=(
                args.context_tokens if args.context_tokens is not None else 262144
            ),
        ),
        telemetry=instrumented,
        quota_check=quota_check_factory(args.quota_breaker),
    )
    import json as _json  # local: only run_pass needs it

    save_run_meta(conn, "model", args.model)
    save_run_meta(
        conn,
        "sampling",
        _json.dumps(
            {
                "provider": args.provider,
                "thinking": not args.no_thinking,
                "top_p": args.top_p,
                "base_url": getattr(args, "base_url", None),
                "context_tokens": args.context_tokens
                if args.context_tokens is not None
                else 262144,
            }
        ),
    )
    runner.run(max_batches=args.max_batches, only_threads=args.threads)
    return 0


def main(
    argv: list[str] | None = None,
    client_factory: Callable[[str], ChatClient] | None = None,
    quota_check_factory: Callable[[float], Callable[[], None] | None] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if quota_check_factory is None:
        quota_check_factory = _default_quota_factory
    if args.command == "usage-summary":
        print(format_summary(summarize(args.path)))
        return 0

    if args.command == "ladder-score":
        import json as _json

        from terrarium_annotator.ladder import format_scorecard, scorecard

        with open(args.gold_set) as f:
            gold = _json.load(f)
        conn = sqlite3.connect(f"file:{args.annotator_db}?mode=ro", uri=True)
        with CorpusReader(args.corpus_db) as corpus:
            sc = scorecard(conn, corpus, gold, args.threads, args.usage_file)
        print(format_scorecard(args.name, sc))
        return 0

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
        factory = client_factory or (lambda model: build_provider_client(args))
        run_id = make_run_id("chat")
        client = InstrumentedClient(
            factory(args.model),
            UsageLog(usage_log_path(args.usage_dir, run_id)),
            run_id=run_id,
            call_type="chat",
        )
        if args.once is not None:
            messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
            messages.append({"role": "user", "content": args.once})
            print(chat_turn(messages, client, dispatcher))
            return 0
        repl(client, dispatcher)
        return 0

    if args.command == "research":
        from terrarium_annotator.research import Researcher

        corpus = CorpusReader(args.corpus_db)
        conn = connect_annotator_db(args.annotator_db)
        client: ChatClient = (
            client_factory(args.model)
            if client_factory
            else build_provider_client(args)
        )
        run_id = make_run_id("research")
        client = InstrumentedClient(
            client,
            UsageLog(usage_log_path(args.usage_dir, run_id)),
            run_id=run_id,
            call_type="researcher",
        )
        if args.record:
            Path(args.record).parent.mkdir(parents=True, exist_ok=True)
            client = RecordingClient(client, args.record)
        researcher = Researcher(
            corpus,
            GlossaryStore(conn, corpus.post_body),
            StoryLog(conn),
            client,
            conn,
            pass_id="research",
            quota_check=quota_check_factory(args.quota_breaker),
        )
        try:
            report = researcher.research(focus=args.focus)
        except (QuotaExceeded, QuotaProbeError) as exc:
            print(f"research: quota breaker halt: {exc}", file=sys.stderr)
            return 3
        print(report)
        return 0

    if args.command == "adjudicate":
        from terrarium_annotator.adjudicate import (
            backup_db,
            load_flagged_candidates,
            run_adjudication,
            stratified_sample,
        )

        try:
            backup_path = backup_db(args.annotator_db)
        except (OSError, RuntimeError) as exc:
            print(f"adjudicate: backup failed, refusing to run: {exc}", file=sys.stderr)
            return 2
        print(f"adjudicate: backup verified at {backup_path}")

        corpus = CorpusReader(args.corpus_db)
        conn = connect_annotator_db(args.annotator_db)
        client: ChatClient = (
            client_factory(args.model)
            if client_factory
            else build_provider_client(args)
        )
        run_id = make_run_id("adjudicate")
        client = InstrumentedClient(
            client,
            UsageLog(usage_log_path(args.usage_dir, run_id)),
            run_id=run_id,
            call_type="researcher",
        )
        candidates = load_flagged_candidates(conn)
        sample = stratified_sample(candidates, args.sample_size)
        work_dir = (
            Path(args.work_dir)
            if args.work_dir
            else (Path("data/adjudication") / run_id)
        )
        result = run_adjudication(
            corpus,
            conn,
            client,
            sample,
            work_dir,
            chunk_size=args.chunk_size,
            quota_check=quota_check_factory(args.quota_breaker),
        )
        print(
            f"adjudicate: {result.chunks_completed} chunks completed, "
            f"{result.chunks_failed} failed; reports in {work_dir}"
        )
        if result.halted:
            print(f"adjudicate: HALTED — {result.halted}", file=sys.stderr)
            return 3
        return 0

    if args.command == "run":
        factory = client_factory or (lambda model: build_provider_client(args))
        try:
            return run_pass(args, factory, quota_check_factory)
        except ValueError as exc:  # e.g. unknown --threads IDs
            print(f"run: {exc}", file=sys.stderr)
            return 2
        except (QuotaExceeded, QuotaProbeError) as exc:
            print(f"run: quota breaker halt: {exc}", file=sys.stderr)
            return 3

    return 2


if __name__ == "__main__":
    sys.exit(main())
