"""Tests for CLI inspect and status commands."""

from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from terrarium_annotator.storage import GlossaryStore, ProgressTracker, SnapshotStore


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def populated_db(temp_db: Path) -> Path:
    """Create a database with test data."""
    # Initialize all stores
    glossary = GlossaryStore(temp_db)
    tracker = ProgressTracker(temp_db)
    snapshots = SnapshotStore(temp_db)

    # Add glossary entries
    glossary.create(
        term="Soma",
        definition="The questmaster.",
        tags=["character", "qm"],
        post_id=100,
        thread_id=1,
        status="confirmed",
    )
    glossary.create(
        term="Dawn",
        definition="A warrior.",
        tags=["character"],
        post_id=150,
        thread_id=1,
        status="tentative",
    )
    glossary.create(
        term="The Citadel",
        definition="A fortress.",
        tags=["location"],
        post_id=200,
        thread_id=2,
        status="confirmed",
    )

    # Update run state
    tracker.start_run()
    tracker.update(
        last_post_id=200,
        last_thread_id=2,
        posts_processed_delta=50,
        entries_created_delta=3,
    )

    # Update thread state
    tracker.update_thread_state(
        thread_id=1,
        status="completed",
        posts_processed_delta=30,
        entries_created_delta=2,
    )

    glossary.close()
    tracker.close()
    snapshots.close()

    return temp_db


class TestStatusCommand:
    """Tests for the status command."""

    def test_status_text_output(self, populated_db: Path):
        """Status command shows run state in text format."""
        from terrarium_annotator.cli import status
        import argparse

        args = argparse.Namespace(annotator_db=str(populated_db), format="text")

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            status(args)
            output = mock_stdout.getvalue()

        assert "Run State:" in output
        assert "Last post:" in output
        assert "200" in output
        assert "Stats:" in output
        assert "Posts processed:" in output
        assert "50" in output
        assert "Glossary:" in output
        assert "Total entries:" in output
        assert "3" in output
        assert "Confirmed:" in output
        assert "2" in output
        assert "Tentative:" in output
        assert "1" in output

    def test_status_json_output(self, populated_db: Path):
        """Status command outputs valid JSON."""
        from terrarium_annotator.cli import status
        import argparse

        args = argparse.Namespace(annotator_db=str(populated_db), format="json")

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            status(args)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        assert "run_state" in data
        assert data["run_state"]["last_post_id"] == 200
        assert data["run_state"]["last_thread_id"] == 2
        assert "stats" in data
        assert data["stats"]["posts_processed"] == 50
        assert data["stats"]["entries_created"] == 3
        assert "glossary" in data
        assert data["glossary"]["total"] == 3
        assert "snapshots" in data

    def test_status_db_not_found(self, temp_db: Path):
        """Status command handles missing database."""
        from terrarium_annotator.cli import status
        import argparse

        args = argparse.Namespace(annotator_db="/nonexistent/path.db", format="text")

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            status(args)
            output = mock_stdout.getvalue()

        assert "Error: Database not found" in output


class TestInspectEntries:
    """Tests for inspect entries command."""

    def test_inspect_entries_text(self, populated_db: Path):
        """List entries in text format."""
        from terrarium_annotator.cli import inspect_entries
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="text",
            limit=10,
            status=None,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_entries(args, populated_db)
            output = mock_stdout.getvalue()

        assert "ID" in output
        assert "Term" in output
        assert "Soma" in output
        assert "Dawn" in output
        assert "The Citadel" in output

    def test_inspect_entries_json(self, populated_db: Path):
        """List entries in JSON format."""
        from terrarium_annotator.cli import inspect_entries
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="json",
            limit=10,
            status=None,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_entries(args, populated_db)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        assert len(data) == 3
        terms = {e["term"] for e in data}
        assert terms == {"Soma", "Dawn", "The Citadel"}

    def test_inspect_entries_filter_status(self, populated_db: Path):
        """Filter entries by status."""
        from terrarium_annotator.cli import inspect_entries
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="json",
            limit=10,
            status="tentative",
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_entries(args, populated_db)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        assert len(data) == 1
        assert data[0]["term"] == "Dawn"

    def test_inspect_entries_limit(self, populated_db: Path):
        """Limit number of entries returned."""
        from terrarium_annotator.cli import inspect_entries
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="json",
            limit=2,
            status=None,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_entries(args, populated_db)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        assert len(data) == 2


class TestInspectEntry:
    """Tests for inspect entry command."""

    def test_inspect_entry_text(self, populated_db: Path):
        """Show entry details in text format."""
        from terrarium_annotator.cli import inspect_entry
        import argparse

        # Get entry ID first
        glossary = GlossaryStore(populated_db)
        entries = list(glossary.all_entries())
        soma_entry = next(e for e in entries if e.term == "Soma")
        glossary.close()

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="text",
            id=soma_entry.id,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_entry(args, populated_db)
            output = mock_stdout.getvalue()

        assert "Soma" in output
        assert "confirmed" in output
        assert "character" in output
        assert "The questmaster" in output

    def test_inspect_entry_json(self, populated_db: Path):
        """Show entry details in JSON format."""
        from terrarium_annotator.cli import inspect_entry
        import argparse

        glossary = GlossaryStore(populated_db)
        entries = list(glossary.all_entries())
        soma_entry = next(e for e in entries if e.term == "Soma")
        glossary.close()

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="json",
            id=soma_entry.id,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_entry(args, populated_db)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        assert data["term"] == "Soma"
        assert data["status"] == "confirmed"
        assert "character" in data["tags"]
        assert "qm" in data["tags"]

    def test_inspect_entry_not_found(self, populated_db: Path):
        """Handle missing entry gracefully."""
        from terrarium_annotator.cli import inspect_entry
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="text",
            id=9999,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_entry(args, populated_db)
            output = mock_stdout.getvalue()

        assert "Error: Entry 9999 not found" in output


class TestInspectSnapshots:
    """Tests for inspect snapshots command."""

    def test_inspect_snapshots_empty(self, populated_db: Path):
        """Handle empty snapshots list."""
        from terrarium_annotator.cli import inspect_snapshots
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="text",
            limit=10,
            type=None,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_snapshots(args, populated_db)
            output = mock_stdout.getvalue()

        assert "No snapshots found" in output

    def test_inspect_snapshots_json_empty(self, populated_db: Path):
        """Empty snapshots list in JSON format."""
        from terrarium_annotator.cli import inspect_snapshots
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="json",
            limit=10,
            type=None,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_snapshots(args, populated_db)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        assert data == []


class TestInspectThread:
    """Tests for inspect thread command."""

    def test_inspect_thread_text(self, populated_db: Path):
        """Show thread state in text format."""
        from terrarium_annotator.cli import inspect_thread
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="text",
            id=1,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_thread(args, populated_db)
            output = mock_stdout.getvalue()

        assert "Thread #1" in output
        assert "Status:" in output
        assert "completed" in output
        assert "Posts processed:" in output
        assert "30" in output

    def test_inspect_thread_json(self, populated_db: Path):
        """Show thread state in JSON format."""
        from terrarium_annotator.cli import inspect_thread
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="json",
            id=1,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_thread(args, populated_db)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        assert data["thread_id"] == 1
        assert data["state"]["status"] == "completed"
        assert data["state"]["posts_processed"] == 30

    def test_inspect_thread_no_state(self, populated_db: Path):
        """Handle thread with no state."""
        from terrarium_annotator.cli import inspect_thread
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="text",
            id=999,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_thread(args, populated_db)
            output = mock_stdout.getvalue()

        assert "No state recorded" in output


class TestCountByStatus:
    """Tests for count_by_status method."""

    def test_count_by_status(self, populated_db: Path):
        """Count entries by status."""
        glossary = GlossaryStore(populated_db)
        counts = glossary.count_by_status()
        glossary.close()

        assert counts["confirmed"] == 2
        assert counts["tentative"] == 1


class TestCountByType:
    """Tests for count_by_type method."""

    def test_count_by_type_empty(self, populated_db: Path):
        """Count snapshots by type when empty."""
        snapshots = SnapshotStore(populated_db)
        counts = snapshots.count_by_type()
        snapshots.close()

        assert counts == {}


class TestInspectSnapshot:
    """Tests for inspect snapshot command with verbose flag."""

    @pytest.fixture
    def db_with_snapshot(self, populated_db: Path) -> tuple[Path, int]:
        """Create a database with a snapshot containing summaries."""
        from terrarium_annotator.context import (
            AnnotationContext,
            CompactionState,
            ThreadSummary,
        )

        snapshots = SnapshotStore(populated_db)
        glossary = GlossaryStore(populated_db)

        # Create context with cumulative summary
        context = AnnotationContext(system_prompt="Test system prompt")

        # Create compaction state with summaries
        compaction_state = CompactionState(
            cumulative_summary="The party journeyed through the forest and met the wizard.",
            thread_summaries=[
                ThreadSummary(
                    thread_id=1,
                    position=0,
                    summary_text="Party formed in the tavern.",
                    entries_created=[1, 2],
                    entries_updated=[],
                )
            ],
        )

        # Create snapshot
        snapshot_id = snapshots.create(
            snapshot_type="checkpoint",
            last_post_id=200,
            last_thread_id=2,
            thread_position=1,
            context=context,
            compaction_state=compaction_state,
            glossary=glossary,
            token_count=5000,
        )

        snapshots.close()
        glossary.close()

        return populated_db, snapshot_id

    def test_inspect_snapshot_text(self, db_with_snapshot: tuple[Path, int]):
        """Show snapshot details in text format."""
        from terrarium_annotator.cli import inspect_snapshot
        import argparse

        db_path, snapshot_id = db_with_snapshot

        args = argparse.Namespace(
            annotator_db=str(db_path),
            format="text",
            id=snapshot_id,
            verbose=False,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_snapshot(args, db_path)
            output = mock_stdout.getvalue()

        assert f"Snapshot #{snapshot_id}" in output
        assert "checkpoint" in output
        assert "Token count:" in output
        # Without verbose, should show char count, not content
        assert "Cumulative summary:" in output
        assert "The party journeyed" not in output  # Content not shown

    def test_inspect_snapshot_verbose_text(self, db_with_snapshot: tuple[Path, int]):
        """Show full summaries with --verbose flag in text format."""
        from terrarium_annotator.cli import inspect_snapshot
        import argparse

        db_path, snapshot_id = db_with_snapshot

        args = argparse.Namespace(
            annotator_db=str(db_path),
            format="text",
            id=snapshot_id,
            verbose=True,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_snapshot(args, db_path)
            output = mock_stdout.getvalue()

        assert f"Snapshot #{snapshot_id}" in output
        # Verbose should show actual summary content
        assert "--- Cumulative Summary ---" in output
        assert "The party journeyed through the forest" in output
        assert "--- Thread Summaries" in output
        assert "Party formed in the tavern" in output

    def test_inspect_snapshot_verbose_json(self, db_with_snapshot: tuple[Path, int]):
        """Show full summaries with --verbose flag in JSON format."""
        from terrarium_annotator.cli import inspect_snapshot
        import argparse

        db_path, snapshot_id = db_with_snapshot

        args = argparse.Namespace(
            annotator_db=str(db_path),
            format="json",
            id=snapshot_id,
            verbose=True,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_snapshot(args, db_path)
            output = mock_stdout.getvalue()

        data = json.loads(output)
        assert data["snapshot"]["id"] == snapshot_id
        # Verbose JSON includes full content
        assert "cumulative_summary" in data["context"]
        assert "The party journeyed" in data["context"]["cumulative_summary"]
        assert "thread_summaries" in data["context"]
        assert len(data["context"]["thread_summaries"]) == 1

    def test_inspect_snapshot_not_found(self, populated_db: Path):
        """Handle snapshot not found."""
        from terrarium_annotator.cli import inspect_snapshot
        import argparse

        args = argparse.Namespace(
            annotator_db=str(populated_db),
            format="text",
            id=9999,
            verbose=False,
        )

        with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            inspect_snapshot(args, populated_db)
            output = mock_stdout.getvalue()

        assert "not found" in output
