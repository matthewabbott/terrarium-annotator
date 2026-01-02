"""Command-line interface for the terrarium annotator harness."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from terrarium_annotator.runner import AnnotationRunner, RunnerConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Terrarium annotator harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Iterate the corpus and update glossary")
    run_parser.add_argument(
        "--corpus-db",
        default="banished.db",
        help="Path to corpus SQLite DB (default: banished.db)",
    )
    run_parser.add_argument(
        "--annotator-db",
        default="annotator.db",
        help="Path to annotator SQLite DB (default: annotator.db)",
    )
    run_parser.add_argument(
        "--agent-url",
        default="http://localhost:8080",
        help="Terrarium-agent base URL",
    )
    run_parser.add_argument(
        "--temperature",
        type=float,
        default=0.4,
        help="Sampling temperature (default: 0.4)",
    )
    run_parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Generation budget (default: 1024)",
    )
    run_parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Agent request timeout in seconds (default: 120)",
    )
    run_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of scenes for this run",
    )
    run_parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start from first post, ignore checkpoint",
    )
    run_parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=10,
        help="Max tool call rounds per scene (default: 10)",
    )

    return parser


def run(args: argparse.Namespace) -> None:
    config = RunnerConfig(
        corpus_db_path=Path(args.corpus_db),
        annotator_db_path=Path(args.annotator_db),
        agent_url=args.agent_url,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        resume=not args.no_resume,
        max_tool_rounds=args.max_tool_rounds,
    )
    runner = AnnotationRunner(config)
    try:
        result = runner.run(limit=args.limit)
        print(f"Processed {result.scenes_processed} scenes, {result.posts_processed} posts")
        print(f"Created {result.entries_created} entries, updated {result.entries_updated}")
        print(f"Total tool calls: {result.tool_calls_total}")
        print(f"Duration: {result.run_duration_seconds:.1f}s")
    finally:
        runner.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        run(args)
    else:
        parser.error(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
