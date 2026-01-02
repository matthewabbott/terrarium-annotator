"""Tests for the tool layer."""

import tempfile
from pathlib import Path

import pytest

from terrarium_annotator.corpus import CorpusReader, StoryPost
from terrarium_annotator.storage import GlossaryEntry, GlossaryStore, RevisionHistory
from terrarium_annotator.tools import (
    ToolDispatcher,
    ToolResult,
    get_all_tool_schemas,
)
from terrarium_annotator.tools.xml_formatter import (
    format_error,
    format_glossary_entry,
    format_post,
    format_posts,
    format_search_results,
    format_success,
)

# Use the actual banished.db for corpus testing (read-only)
CORPUS_DB = Path(__file__).parent.parent / "banished.db"


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def glossary(temp_db: Path) -> GlossaryStore:
    """Create a glossary store for tests."""
    store = GlossaryStore(temp_db)
    yield store
    store.close()


@pytest.fixture
def revisions(temp_db: Path) -> RevisionHistory:
    """Create a revision history for tests."""
    history = RevisionHistory(temp_db)
    yield history
    history.close()


@pytest.fixture
def corpus() -> CorpusReader:
    """Create a corpus reader for tests."""
    reader = CorpusReader(CORPUS_DB)
    yield reader
    reader.close()


@pytest.fixture
def dispatcher(
    glossary: GlossaryStore,
    corpus: CorpusReader,
    revisions: RevisionHistory,
) -> ToolDispatcher:
    """Create a tool dispatcher for tests."""
    return ToolDispatcher(glossary, corpus, revisions)


class TestXmlFormatter:
    def test_format_glossary_entry(self):
        entry = GlossaryEntry(
            id=1,
            term="Soma",
            term_normalized="soma",
            definition="The questmaster.",
            tags=["character", "qm"],
            status="confirmed",
            first_seen_post_id=100,
            first_seen_thread_id=1,
            last_updated_post_id=100,
            last_updated_thread_id=1,
            created_at="2024-01-01T12:00:00",
            updated_at="2024-01-01T12:00:00",
        )
        xml = format_glossary_entry(entry)
        assert '<entry id="1"' in xml
        assert 'term="Soma"' in xml
        assert 'status="confirmed"' in xml
        assert 'tags="character,qm"' in xml
        assert ">The questmaster.</entry>" in xml

    def test_format_glossary_entry_no_tags(self):
        entry = GlossaryEntry(
            id=1,
            term="Test",
            term_normalized="test",
            definition="A test entry.",
            tags=[],
            status="tentative",
            first_seen_post_id=1,
            first_seen_thread_id=1,
            last_updated_post_id=1,
            last_updated_thread_id=1,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        xml = format_glossary_entry(entry)
        assert 'tags="' not in xml  # No tags attribute when empty

    def test_format_post(self):
        from datetime import datetime
        post = StoryPost(
            post_id=42,
            thread_id=1,
            body="Hello, world!",
            author="TestAuthor",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            tags=["story_post", "qm_post"],
        )
        xml = format_post(post)
        assert '<post id="42"' in xml
        assert 'thread_id="1"' in xml
        assert 'author="TestAuthor"' in xml
        assert 'tags="story_post,qm_post"' in xml
        assert ">Hello, world!</post>" in xml

    def test_format_post_no_author(self):
        post = StoryPost(
            post_id=1,
            thread_id=1,
            body="Body text",
            author=None,
            created_at=None,
            tags=[],
        )
        xml = format_post(post)
        assert 'author="' not in xml
        assert 'ts="' not in xml

    def test_format_search_results(self):
        entries = [
            GlossaryEntry(
                id=1,
                term="Soma",
                term_normalized="soma",
                definition="QM",
                tags=[],
                status="confirmed",
                first_seen_post_id=1,
                first_seen_thread_id=1,
                last_updated_post_id=1,
                last_updated_thread_id=1,
                created_at="2024-01-01T00:00:00",
                updated_at="2024-01-01T00:00:00",
            ),
            GlossaryEntry(
                id=2,
                term="Neph",
                term_normalized="neph",
                definition="Dragon wife",
                tags=["character"],
                status="tentative",
                first_seen_post_id=1,
                first_seen_thread_id=1,
                last_updated_post_id=1,
                last_updated_thread_id=1,
                created_at="2024-01-01T00:00:00",
                updated_at="2024-01-01T00:00:00",
            ),
        ]
        xml = format_search_results(entries, "test query")
        assert '<search_results query="test query" count="2">' in xml
        assert 'term="Soma"' in xml
        assert 'term="Neph"' in xml
        assert "</search_results>" in xml

    def test_format_posts(self):
        posts = [
            StoryPost(post_id=1, thread_id=5, body="First", author=None, created_at=None, tags=[]),
            StoryPost(post_id=2, thread_id=5, body="Second", author=None, created_at=None, tags=[]),
        ]
        xml = format_posts(posts, thread_id=5)
        assert '<posts thread_id="5" count="2">' in xml
        assert "</posts>" in xml

    def test_format_error(self):
        xml = format_error("Something went wrong", code="TEST_ERROR")
        assert '<error code="TEST_ERROR">Something went wrong</error>' == xml

    def test_format_error_no_code(self):
        xml = format_error("Generic error")
        assert "<error>Generic error</error>" == xml

    def test_format_success(self):
        xml = format_success("Entry created", entry_id=42)
        assert '<success entry_id="42">Entry created</success>' == xml

    def test_format_success_no_entry_id(self):
        xml = format_success("Operation complete")
        assert "<success>Operation complete</success>" == xml

    def test_format_escapes_special_chars(self):
        entry = GlossaryEntry(
            id=1,
            term="<Test>",
            term_normalized="<test>",
            definition="Contains & special <chars>",
            tags=["tag&1"],
            status="confirmed",
            first_seen_post_id=1,
            first_seen_thread_id=1,
            last_updated_post_id=1,
            last_updated_thread_id=1,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        xml = format_glossary_entry(entry)
        assert "&lt;Test&gt;" in xml
        assert "&amp;" in xml


class TestToolSchemas:
    def test_get_all_tool_schemas_returns_list(self):
        schemas = get_all_tool_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) == 6  # 4 glossary + 2 corpus

    def test_all_schemas_have_required_structure(self):
        schemas = get_all_tool_schemas()
        for schema in schemas:
            assert schema["type"] == "function"
            assert "function" in schema
            func = schema["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_schema_names(self):
        schemas = get_all_tool_schemas()
        names = {s["function"]["name"] for s in schemas}
        expected = {
            "glossary_search",
            "glossary_create",
            "glossary_update",
            "glossary_delete",
            "read_post",
            "read_thread_range",
        }
        assert names == expected

    def test_include_snapshot_tools_false(self):
        # Snapshot tools not yet implemented
        schemas = get_all_tool_schemas(include_snapshot_tools=False)
        assert len(schemas) == 6

    def test_include_snapshot_tools_true_currently_same(self):
        # Until F7, this should return same result
        schemas = get_all_tool_schemas(include_snapshot_tools=True)
        assert len(schemas) == 6


class TestToolDispatcher:
    def test_dispatch_unknown_tool(self, dispatcher: ToolDispatcher):
        tool_call = {
            "id": "call_123",
            "function": {
                "name": "nonexistent_tool",
                "arguments": "{}",
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is False
        assert "UNKNOWN_TOOL" in result.result
        assert result.tool_name == "nonexistent_tool"

    def test_dispatch_invalid_json(self, dispatcher: ToolDispatcher):
        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_search",
                "arguments": "not valid json",
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is False
        assert "INVALID_JSON" in result.result

    def test_dispatch_glossary_search(self, dispatcher: ToolDispatcher, glossary: GlossaryStore):
        # Create an entry first
        glossary.create(
            term="TestTerm",
            definition="A test definition",
            tags=["test"],
            post_id=1,
            thread_id=1,
        )

        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_search",
                "arguments": '{"query": "TestTerm"}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is True
        assert "TestTerm" in result.result
        assert "<search_results" in result.result

    def test_dispatch_glossary_create(self, dispatcher: ToolDispatcher):
        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_create",
                "arguments": '{"term": "NewTerm", "definition": "New definition", "tags": ["new"]}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=10, current_thread_id=1)
        assert result.success is True
        assert "<success" in result.result
        assert "entry_id=" in result.result

    def test_dispatch_glossary_create_duplicate(self, dispatcher: ToolDispatcher, glossary: GlossaryStore):
        # Create first entry
        glossary.create(term="DupeTerm", definition="First", tags=[], post_id=1, thread_id=1)

        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_create",
                "arguments": '{"term": "DupeTerm", "definition": "Second", "tags": []}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is False
        assert "DUPLICATE" in result.result

    def test_dispatch_glossary_update(self, dispatcher: ToolDispatcher, glossary: GlossaryStore):
        entry_id = glossary.create(
            term="UpdateMe",
            definition="Original",
            tags=["original"],
            post_id=1,
            thread_id=1,
        )

        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_update",
                "arguments": f'{{"entry_id": {entry_id}, "definition": "Updated definition"}}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=5, current_thread_id=1)
        assert result.success is True
        assert "Updated definition" in result.result

    def test_dispatch_glossary_update_not_found(self, dispatcher: ToolDispatcher):
        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_update",
                "arguments": '{"entry_id": 99999, "definition": "New"}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is False
        assert "NOT_FOUND" in result.result

    def test_dispatch_glossary_delete(self, dispatcher: ToolDispatcher, glossary: GlossaryStore):
        entry_id = glossary.create(
            term="DeleteMe",
            definition="To be deleted",
            tags=[],
            post_id=1,
            thread_id=1,
        )

        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_delete",
                "arguments": f'{{"entry_id": {entry_id}, "reason": "Testing deletion"}}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is True
        assert "<success" in result.result

    def test_dispatch_glossary_delete_not_found(self, dispatcher: ToolDispatcher):
        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_delete",
                "arguments": '{"entry_id": 99999, "reason": "No such entry"}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is False
        assert "NOT_FOUND" in result.result

    def test_dispatch_read_post(self, dispatcher: ToolDispatcher, corpus: CorpusReader):
        # Get a valid post ID from corpus
        threads = list(corpus.iter_threads())
        if not threads:
            pytest.skip("No threads in corpus")

        posts = corpus.get_posts_range(threads[0].id)
        if not posts:
            pytest.skip("No posts in corpus")

        post_id = posts[0].post_id

        tool_call = {
            "id": "call_123",
            "function": {
                "name": "read_post",
                "arguments": f'{{"post_id": {post_id}}}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is True
        assert "<post" in result.result

    def test_dispatch_read_post_not_found(self, dispatcher: ToolDispatcher):
        tool_call = {
            "id": "call_123",
            "function": {
                "name": "read_post",
                "arguments": '{"post_id": 999999999}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is False
        assert "NOT_FOUND" in result.result

    def test_dispatch_read_thread_range(self, dispatcher: ToolDispatcher, corpus: CorpusReader):
        # Get a valid thread ID from corpus
        threads = list(corpus.iter_threads())
        if not threads:
            pytest.skip("No threads in corpus")

        thread_id = threads[0].id

        tool_call = {
            "id": "call_123",
            "function": {
                "name": "read_thread_range",
                "arguments": f'{{"thread_id": {thread_id}}}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is True
        assert "<posts" in result.result

    def test_dispatch_read_thread_range_empty(self, dispatcher: ToolDispatcher):
        tool_call = {
            "id": "call_123",
            "function": {
                "name": "read_thread_range",
                "arguments": '{"thread_id": 999999999}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=1, current_thread_id=1)
        assert result.success is False
        assert "EMPTY_RANGE" in result.result

    def test_get_tool_definitions(self, dispatcher: ToolDispatcher):
        definitions = dispatcher.get_tool_definitions()
        assert len(definitions) == 6
        names = {d["function"]["name"] for d in definitions}
        assert "glossary_search" in names
        assert "read_post" in names

    def test_has_active_summon_returns_false(self, dispatcher: ToolDispatcher):
        # Until F7, this should always return False
        assert dispatcher.has_active_summon is False


class TestToolResult:
    def test_tool_result_creation(self):
        result = ToolResult(
            tool_name="test_tool",
            call_id="call_123",
            success=True,
            result="<success>OK</success>",
            error=None,
        )
        assert result.tool_name == "test_tool"
        assert result.call_id == "call_123"
        assert result.success is True
        assert result.error is None

    def test_tool_result_with_error(self):
        result = ToolResult(
            tool_name="test_tool",
            call_id="call_123",
            success=False,
            result="<error>Failed</error>",
            error="Something went wrong",
        )
        assert result.success is False
        assert result.error == "Something went wrong"


class TestRevisionLogging:
    def test_create_logs_revision(
        self,
        dispatcher: ToolDispatcher,
        glossary: GlossaryStore,
        revisions: RevisionHistory,
    ):
        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_create",
                "arguments": '{"term": "LoggedTerm", "definition": "Logged def", "tags": ["test"]}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=42, current_thread_id=1)
        assert result.success is True

        # Extract entry_id from success message
        # Result format: <success entry_id="N">Created entry 'LoggedTerm'</success>
        import re
        match = re.search(r'entry_id="(\d+)"', result.result)
        assert match is not None
        entry_id = int(match.group(1))

        # Check revision was logged
        entry_revisions = revisions.get_history(entry_id)
        assert len(entry_revisions) > 0
        # Find the term creation revision
        term_creations = [r for r in entry_revisions if r.field_name == "term"]
        assert len(term_creations) == 1
        assert term_creations[0].source_post_id == 42
        assert term_creations[0].new_value == "LoggedTerm"

    def test_update_logs_changes(
        self,
        dispatcher: ToolDispatcher,
        glossary: GlossaryStore,
        revisions: RevisionHistory,
    ):
        entry_id = glossary.create(
            term="TrackChanges",
            definition="Original",
            tags=[],
            post_id=1,
            thread_id=1,
        )

        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_update",
                "arguments": f'{{"entry_id": {entry_id}, "definition": "Modified"}}',
            },
        }
        dispatcher.dispatch(tool_call, current_post_id=50, current_thread_id=1)

        # Check revision was logged
        entry_revisions = revisions.get_history(entry_id)
        changes = [r for r in entry_revisions if r.field_name == "definition"]
        assert len(changes) == 1
        assert changes[0].old_value == "Original"
        assert changes[0].new_value == "Modified"
        assert changes[0].source_post_id == 50

    def test_delete_succeeds(
        self,
        dispatcher: ToolDispatcher,
        glossary: GlossaryStore,
        revisions: RevisionHistory,
    ):
        """Test that delete tool succeeds and entry is removed.

        Note: The revision table has ON DELETE CASCADE, so when the entry is
        deleted, its revisions are also deleted. This means we can't verify
        the deletion log after the entry is gone. The deletion IS logged
        (in GlossaryTools.delete) but immediately cascade-deleted.
        """
        entry_id = glossary.create(
            term="ToDelete",
            definition="Will be deleted",
            tags=[],
            post_id=1,
            thread_id=1,
        )

        tool_call = {
            "id": "call_123",
            "function": {
                "name": "glossary_delete",
                "arguments": f'{{"entry_id": {entry_id}, "reason": "Not needed"}}',
            },
        }
        result = dispatcher.dispatch(tool_call, current_post_id=60, current_thread_id=1)
        assert result.success is True
        assert "<success>" in result.result
        assert "ToDelete" in result.result

        # Verify entry is actually deleted
        assert glossary.get(entry_id) is None
