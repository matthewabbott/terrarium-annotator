"""Command-line interface for the terrarium annotator harness."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterator

from terrarium_annotator.exporters import JsonExporter, YamlExporter
from terrarium_annotator.runner import AnnotationRunner, RunnerConfig
from terrarium_annotator.storage import (
    GlossaryEntry,
    GlossaryStore,
    ProgressTracker,
    SnapshotStore,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Terrarium annotator harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Iterate the corpus and update glossary"
    )
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
    run_parser.add_argument(
        "--context-budget",
        type=int,
        default=98304,
        help="Context budget in tokens (default: 98304)",
    )
    run_parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Log to file in addition to stderr (for long runs)",
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

    # Status command
    status_parser = subparsers.add_parser(
        "status", help="Show run status and glossary summary"
    )
    status_parser.add_argument(
        "--annotator-db",
        default="annotator.db",
        help="Path to annotator database (default: annotator.db)",
    )
    status_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    # Inspect command with subcommands
    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect entries, snapshots, threads"
    )
    inspect_parser.add_argument(
        "--annotator-db",
        default="annotator.db",
        help="Path to annotator database (default: annotator.db)",
    )
    inspect_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    inspect_subparsers = inspect_parser.add_subparsers(dest="target", required=True)

    # inspect snapshots
    snapshots_parser = inspect_subparsers.add_parser(
        "snapshots", help="List recent snapshots"
    )
    snapshots_parser.add_argument(
        "--limit", type=int, default=10, help="Number of snapshots to show"
    )
    snapshots_parser.add_argument(
        "--type",
        choices=["checkpoint", "curator_fork", "manual"],
        help="Filter by snapshot type",
    )

    # inspect snapshot <id>
    snapshot_parser = inspect_subparsers.add_parser(
        "snapshot", help="View snapshot details"
    )
    snapshot_parser.add_argument("id", type=int, help="Snapshot ID")

    # inspect entries
    entries_parser = inspect_subparsers.add_parser(
        "entries", help="List recent entries"
    )
    entries_parser.add_argument(
        "--limit", type=int, default=10, help="Number of entries to show"
    )
    entries_parser.add_argument(
        "--status",
        choices=["confirmed", "tentative"],
        help="Filter by status",
    )

    # inspect entry <id>
    entry_parser = inspect_subparsers.add_parser("entry", help="View entry details")
    entry_parser.add_argument("id", type=int, help="Entry ID")

    # inspect thread <id>
    thread_parser = inspect_subparsers.add_parser("thread", help="View thread state")
    thread_parser.add_argument("id", type=int, help="Thread ID")

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


def status(args: argparse.Namespace) -> None:
    """Show run status and glossary summary."""
    db_path = Path(args.annotator_db)
    if not db_path.exists():
        print(f"Error: Database not found: {db_path}")
        return

    tracker = ProgressTracker(db_path)
    glossary = GlossaryStore(db_path)
    snapshots = SnapshotStore(db_path)

    try:
        run_state = tracker.get_state()
        entry_count = glossary.count()
        entry_by_status = glossary.count_by_status()
        snapshot_count = snapshots.count()
        snapshot_by_type = snapshots.count_by_type()

        if args.format == "json":
            data = {
                "run_state": {
                    "last_post_id": run_state.last_post_id,
                    "last_thread_id": run_state.last_thread_id,
                    "current_snapshot_id": run_state.current_snapshot_id,
                    "run_started_at": run_state.run_started_at,
                    "run_updated_at": run_state.run_updated_at,
                },
                "stats": {
                    "posts_processed": run_state.total_posts_processed,
                    "entries_created": run_state.total_entries_created,
                    "entries_updated": run_state.total_entries_updated,
                },
                "glossary": {
                    "total": entry_count,
                    "by_status": entry_by_status,
                },
                "snapshots": {
                    "total": snapshot_count,
                    "by_type": snapshot_by_type,
                },
            }
            print(json.dumps(data, indent=2))
        else:
            print("Run State:")
            print(f"  Last post:       {run_state.last_post_id or 'N/A'}")
            print(f"  Last thread:     {run_state.last_thread_id or 'N/A'}")
            print(f"  Started:         {run_state.run_started_at or 'N/A'}")
            print(f"  Updated:         {run_state.run_updated_at or 'N/A'}")
            print()
            print("Stats:")
            print(f"  Posts processed: {run_state.total_posts_processed}")
            print(f"  Entries created: {run_state.total_entries_created}")
            print(f"  Entries updated: {run_state.total_entries_updated}")
            print()
            print("Glossary:")
            print(f"  Total entries:   {entry_count}")
            print(f"  Confirmed:       {entry_by_status.get('confirmed', 0)}")
            print(f"  Tentative:       {entry_by_status.get('tentative', 0)}")
            print()
            print("Snapshots:")
            print(f"  Total:           {snapshot_count}")
            print(f"  Checkpoints:     {snapshot_by_type.get('checkpoint', 0)}")
            print(f"  Curator forks:   {snapshot_by_type.get('curator_fork', 0)}")
            print(f"  Manual:          {snapshot_by_type.get('manual', 0)}")

    finally:
        tracker.close()
        glossary.close()
        snapshots.close()


def inspect(args: argparse.Namespace) -> None:
    """Dispatch to inspect sub-handlers."""
    db_path = Path(args.annotator_db)
    if not db_path.exists():
        print(f"Error: Database not found: {db_path}")
        return

    target = args.target
    if target == "snapshots":
        inspect_snapshots(args, db_path)
    elif target == "snapshot":
        inspect_snapshot(args, db_path)
    elif target == "entries":
        inspect_entries(args, db_path)
    elif target == "entry":
        inspect_entry(args, db_path)
    elif target == "thread":
        inspect_thread(args, db_path)


def inspect_snapshots(args: argparse.Namespace, db_path: Path) -> None:
    """List recent snapshots."""
    store = SnapshotStore(db_path)
    try:
        snapshot_type = getattr(args, "type", None)
        snapshots_list = store.list_recent(
            limit=args.limit, snapshot_type=snapshot_type
        )

        if args.format == "json":
            data = [
                {
                    "id": s.id,
                    "type": s.snapshot_type,
                    "created_at": s.created_at,
                    "last_post_id": s.last_post_id,
                    "last_thread_id": s.last_thread_id,
                    "thread_position": s.thread_position,
                    "glossary_entry_count": s.glossary_entry_count,
                    "context_token_count": s.context_token_count,
                }
                for s in snapshots_list
            ]
            print(json.dumps(data, indent=2))
        else:
            if not snapshots_list:
                print("No snapshots found.")
                return
            print(
                f"{'ID':>5}  {'Type':<14}  {'Thread':>6}  {'Entries':>7}  {'Created At'}"
            )
            print("-" * 70)
            for s in snapshots_list:
                print(
                    f"{s.id:>5}  {s.snapshot_type:<14}  {s.last_thread_id:>6}  "
                    f"{s.glossary_entry_count:>7}  {s.created_at}"
                )
    finally:
        store.close()


def inspect_snapshot(args: argparse.Namespace, db_path: Path) -> None:
    """Show snapshot details."""
    store = SnapshotStore(db_path)
    glossary = GlossaryStore(db_path)
    try:
        snapshot = store.get(args.id)
        if snapshot is None:
            print(f"Error: Snapshot {args.id} not found")
            return

        context = store.get_context(args.id)
        entries = store.get_entries(args.id)

        if args.format == "json":
            data = {
                "snapshot": {
                    "id": snapshot.id,
                    "type": snapshot.snapshot_type,
                    "created_at": snapshot.created_at,
                    "last_post_id": snapshot.last_post_id,
                    "last_thread_id": snapshot.last_thread_id,
                    "thread_position": snapshot.thread_position,
                    "glossary_entry_count": snapshot.glossary_entry_count,
                    "context_token_count": snapshot.context_token_count,
                    "metadata": snapshot.metadata,
                },
                "context": {
                    "system_prompt_length": len(context.system_prompt)
                    if context
                    else 0,
                    "cumulative_summary_length": len(context.cumulative_summary or "")
                    if context
                    else 0,
                    "thread_summaries_count": len(context.thread_summaries)
                    if context
                    else 0,
                    "conversation_history_length": len(context.conversation_history)
                    if context
                    else 0,
                    "current_thread_id": context.current_thread_id if context else None,
                },
                "entries_count": len(entries),
            }
            print(json.dumps(data, indent=2))
        else:
            print(f"Snapshot #{snapshot.id}")
            print("=" * 40)
            print(f"Type:            {snapshot.snapshot_type}")
            print(f"Created:         {snapshot.created_at}")
            print(f"Last post:       {snapshot.last_post_id}")
            print(f"Last thread:     {snapshot.last_thread_id}")
            print(f"Thread position: {snapshot.thread_position}")
            print(f"Entry count:     {snapshot.glossary_entry_count}")
            print(f"Token count:     {snapshot.context_token_count or 'N/A'}")
            if snapshot.metadata:
                print(f"Metadata:        {snapshot.metadata}")
            print()
            if context:
                print("Context:")
                print(f"  System prompt:        {len(context.system_prompt)} chars")
                print(
                    f"  Cumulative summary:   {len(context.cumulative_summary or '')} chars"
                )
                print(f"  Thread summaries:     {len(context.thread_summaries)}")
                print(
                    f"  Conversation history: {len(context.conversation_history)} messages"
                )
                print(f"  Current thread:       {context.current_thread_id}")
            print()
            print(f"Captured {len(entries)} entry states")
    finally:
        store.close()
        glossary.close()


def inspect_entries(args: argparse.Namespace, db_path: Path) -> None:
    """List recent entries."""
    glossary = GlossaryStore(db_path)
    try:
        all_entries = list(glossary.all_entries())
        # Sort by ID descending (most recent first) and apply filters
        all_entries.sort(key=lambda e: e.id or 0, reverse=True)

        if args.status:
            all_entries = [e for e in all_entries if e.status == args.status]

        entries_list = all_entries[: args.limit]

        if args.format == "json":
            data = [
                {
                    "id": e.id,
                    "term": e.term,
                    "status": e.status,
                    "tags": e.tags,
                    "first_seen_post_id": e.first_seen_post_id,
                    "first_seen_thread_id": e.first_seen_thread_id,
                    "created_at": e.created_at,
                }
                for e in entries_list
            ]
            print(json.dumps(data, indent=2))
        else:
            if not entries_list:
                print("No entries found.")
                return
            print(f"{'ID':>5}  {'Term':<25}  {'Status':<10}  {'Tags'}")
            print("-" * 70)
            for e in entries_list:
                term_display = e.term[:25] if len(e.term) <= 25 else e.term[:22] + "..."
                tags_display = ", ".join(e.tags[:3])
                if len(e.tags) > 3:
                    tags_display += f" (+{len(e.tags) - 3})"
                print(f"{e.id:>5}  {term_display:<25}  {e.status:<10}  {tags_display}")
    finally:
        glossary.close()


def inspect_entry(args: argparse.Namespace, db_path: Path) -> None:
    """Show entry details."""
    glossary = GlossaryStore(db_path)
    try:
        entry = glossary.get(args.id)
        if entry is None:
            print(f"Error: Entry {args.id} not found")
            return

        if args.format == "json":
            data = {
                "id": entry.id,
                "term": entry.term,
                "definition": entry.definition,
                "status": entry.status,
                "tags": entry.tags,
                "first_seen_post_id": entry.first_seen_post_id,
                "first_seen_thread_id": entry.first_seen_thread_id,
                "last_updated_post_id": entry.last_updated_post_id,
                "last_updated_thread_id": entry.last_updated_thread_id,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
            }
            print(json.dumps(data, indent=2))
        else:
            print(f"Entry #{entry.id}: {entry.term}")
            print("=" * 40)
            print(f"Status:          {entry.status}")
            print(f"Tags:            {', '.join(entry.tags) if entry.tags else 'none'}")
            print(
                f"First seen:      post {entry.first_seen_post_id}, thread {entry.first_seen_thread_id}"
            )
            print(
                f"Last updated:    post {entry.last_updated_post_id}, thread {entry.last_updated_thread_id}"
            )
            print(f"Created:         {entry.created_at}")
            print(f"Updated:         {entry.updated_at}")
            print()
            print("Definition:")
            print("-" * 40)
            print(entry.definition)
    finally:
        glossary.close()


def inspect_thread(args: argparse.Namespace, db_path: Path) -> None:
    """Show thread state and related data."""
    tracker = ProgressTracker(db_path)
    glossary = GlossaryStore(db_path)
    snapshots = SnapshotStore(db_path)
    try:
        thread_state = tracker.get_thread_state(args.id)
        thread_entries = glossary.get_by_thread(args.id)
        thread_snapshots = snapshots.list_by_thread(args.id)

        if args.format == "json":
            data = {
                "thread_id": args.id,
                "state": {
                    "status": thread_state.status if thread_state else None,
                    "summary": thread_state.summary if thread_state else None,
                    "posts_processed": thread_state.posts_processed
                    if thread_state
                    else 0,
                    "entries_created": thread_state.entries_created
                    if thread_state
                    else 0,
                    "entries_updated": thread_state.entries_updated
                    if thread_state
                    else 0,
                    "started_at": thread_state.started_at if thread_state else None,
                    "completed_at": thread_state.completed_at if thread_state else None,
                }
                if thread_state
                else None,
                "entries_count": len(thread_entries),
                "snapshots_count": len(thread_snapshots),
            }
            print(json.dumps(data, indent=2))
        else:
            print(f"Thread #{args.id}")
            print("=" * 40)
            if thread_state:
                print(f"Status:          {thread_state.status}")
                print(f"Posts processed: {thread_state.posts_processed}")
                print(f"Entries created: {thread_state.entries_created}")
                print(f"Entries updated: {thread_state.entries_updated}")
                print(f"Started:         {thread_state.started_at or 'N/A'}")
                print(f"Completed:       {thread_state.completed_at or 'N/A'}")
                if thread_state.summary:
                    print()
                    print("Summary:")
                    print("-" * 40)
                    print(thread_state.summary)
            else:
                print("No state recorded for this thread.")
            print()
            print(f"Entries first seen in this thread: {len(thread_entries)}")
            print(f"Snapshots for this thread: {len(thread_snapshots)}")
            if thread_snapshots:
                print()
                print("Snapshots:")
                for s in thread_snapshots[:5]:
                    print(f"  #{s.id} ({s.snapshot_type}) - {s.created_at}")
                if len(thread_snapshots) > 5:
                    print(f"  ... and {len(thread_snapshots) - 5} more")
    finally:
        tracker.close()
        glossary.close()
        snapshots.close()


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
        context_budget=args.context_budget,
    )
    runner = AnnotationRunner(config)
    try:
        result = runner.run(limit=args.limit)
        print(
            f"Processed {result.scenes_processed} scenes, {result.posts_processed} posts"
        )
        print(
            f"Created {result.entries_created} entries, updated {result.entries_updated}"
        )
        print(f"Total tool calls: {result.tool_calls_total}")
        print(f"Duration: {result.run_duration_seconds:.1f}s")
    finally:
        runner.close()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Configure logging
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    # Add file handler if --log-file specified (only for run command)
    if args.command == "run" and getattr(args, "log_file", None):
        file_handler = logging.FileHandler(args.log_file)
        file_handler.setFormatter(logging.Formatter(log_format))
        handlers.append(file_handler)

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers,
    )

    if args.command == "run":
        run(args)
    elif args.command == "export":
        export(args)
    elif args.command == "status":
        status(args)
    elif args.command == "inspect":
        inspect(args)
    else:
        parser.error(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
