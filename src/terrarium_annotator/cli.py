"""Command-line interface for the terrarium annotator harness."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterator

from terrarium_annotator.exporters import JsonExporter, YamlExporter
from terrarium_annotator.runner import AnnotationRunner, RunnerConfig
from terrarium_annotator.storage import GlossaryEntry, GlossaryStore


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

    # Export command
    export_parser = subparsers.add_parser("export", help="Export glossary to JSON/YAML")
    export_parser.add_argument(
        "--annotator-db",
        default="annotator.db",
        help="Path to annotator database (default: annotator.db)",
    )
    export_parser.add_argument(
        "--format",
        choices=["json", "yaml"],
        default="json",
        help="Export format (default: json)",
    )
    export_parser.add_argument(
        "--output",
        "-o",
        help="Output file path (default: glossary_YYYY-MM-DD.{format})",
    )
    export_parser.add_argument(
        "--status",
        choices=["confirmed", "tentative", "all"],
        default="all",
        help="Filter by status (default: all)",
    )
    export_parser.add_argument(
        "--tags",
        nargs="+",
        help="Filter by tags (entries must have ALL specified tags)",
    )

    return parser


def filter_entries(
    entries: Iterator[GlossaryEntry],
    status: str | None,
    tags: list[str] | None,
) -> Iterator[GlossaryEntry]:
    """Filter entries by status and/or tags.

    Args:
        entries: Iterator of entries to filter.
        status: Filter by status ('confirmed', 'tentative', or 'all').
        tags: Filter by tags (entry must have ALL specified tags).

    Yields:
        Entries matching the filters.
    """
    for entry in entries:
        # Status filter
        if status and status != "all" and entry.status != status:
            continue

        # Tags filter (entry must have ALL specified tags)
        if tags:
            entry_tags_set = set(entry.tags)
            if not all(tag in entry_tags_set for tag in tags):
                continue

        yield entry


def export(args: argparse.Namespace) -> None:
    """Export glossary to JSON or YAML."""
    db_path = Path(args.annotator_db)
    if not db_path.exists():
        print(f"Error: Database not found: {db_path}")
        return

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_path = Path(f"glossary_{date_str}.{args.format}")

    # Select exporter
    if args.format == "json":
        exporter = JsonExporter()
    else:
        exporter = YamlExporter()

    # Open glossary and export
    glossary = GlossaryStore(db_path)
    try:
        entries = glossary.all_entries()
        filtered = filter_entries(entries, args.status, args.tags)
        count = exporter.export(filtered, output_path)
        print(f"Exported {count} entries to {output_path}")
    finally:
        glossary.close()


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
    elif args.command == "export":
        export(args)
    else:
        parser.error(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
