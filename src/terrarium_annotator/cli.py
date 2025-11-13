"""Command-line interface for the terrarium annotator harness."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .runner import AnnotationRunner, RunnerConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Terrarium annotator harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Iterate the corpus and update the codex")
    run_parser.add_argument("--db", default="banished.db", help="Path to Banished Quest SQLite DB")
    run_parser.add_argument("--agent-url", default="http://localhost:8080", help="Terrarium-agent base URL")
    run_parser.add_argument("--codex", default="data/codex.json", help="Codex JSON path")
    run_parser.add_argument("--state", default="data/state.json", help="Progress state path")
    run_parser.add_argument("--batch-size", type=int, default=1, help="Posts per LLM call")
    run_parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    run_parser.add_argument("--max-tokens", type=int, default=768, help="Generation budget")
    run_parser.add_argument("--limit", type=int, default=None, help="Limit number of posts for this run")
    run_parser.add_argument("--no-resume", action="store_true", help="Start from the first post every time")

    return parser


def run(args: argparse.Namespace) -> None:
    config = RunnerConfig(
        db_path=Path(args.db),
        agent_url=args.agent_url,
        codex_path=Path(args.codex),
        state_path=Path(args.state),
        batch_size=args.batch_size,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        resume=not args.no_resume,
    )
    runner = AnnotationRunner(config)
    runner.run(limit=args.limit)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        run(args)
    else:
        parser.error(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
