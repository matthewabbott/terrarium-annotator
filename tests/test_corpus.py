"""Tests for the corpus access layer."""

from pathlib import Path

import pytest

from terrarium_annotator.corpus import (
    CorpusReader,
    Scene,
    SceneBatcher,
    StoryPost,
    Thread,
)

# Use the actual banished.db for testing (read-only)
CORPUS_DB = Path(__file__).parent.parent / "banished.db"


@pytest.fixture
def corpus() -> CorpusReader:
    """Create a corpus reader for tests."""
    reader = CorpusReader(CORPUS_DB)
    yield reader
    reader.close()


@pytest.fixture
def batcher(corpus: CorpusReader) -> SceneBatcher:
    """Create a scene batcher for tests."""
    return SceneBatcher(corpus)


class TestStoryPost:
    def test_has_tag_returns_true_when_present(self):
        post = StoryPost(
            post_id=1,
            thread_id=1,
            body="test",
            author=None,
            created_at=None,
            tags=["qm_post", "story_post"],
        )
        assert post.has_tag("qm_post") is True
        assert post.has_tag("story_post") is True

    def test_has_tag_returns_false_when_missing(self):
        post = StoryPost(
            post_id=1,
            thread_id=1,
            body="test",
            author=None,
            created_at=None,
            tags=["qm_post"],
        )
        assert post.has_tag("nonexistent") is False


class TestScene:
    def test_first_post_id(self):
        posts = [
            StoryPost(post_id=10, thread_id=1, body="", author=None, created_at=None, tags=[]),
            StoryPost(post_id=20, thread_id=1, body="", author=None, created_at=None, tags=[]),
        ]
        scene = Scene(thread_id=1, posts=posts, is_thread_start=True, is_thread_end=False, scene_index=0)
        assert scene.first_post_id == 10

    def test_last_post_id(self):
        posts = [
            StoryPost(post_id=10, thread_id=1, body="", author=None, created_at=None, tags=[]),
            StoryPost(post_id=20, thread_id=1, body="", author=None, created_at=None, tags=[]),
        ]
        scene = Scene(thread_id=1, posts=posts, is_thread_start=True, is_thread_end=False, scene_index=0)
        assert scene.last_post_id == 20

    def test_post_count(self):
        posts = [
            StoryPost(post_id=10, thread_id=1, body="", author=None, created_at=None, tags=[]),
            StoryPost(post_id=20, thread_id=1, body="", author=None, created_at=None, tags=[]),
            StoryPost(post_id=30, thread_id=1, body="", author=None, created_at=None, tags=[]),
        ]
        scene = Scene(thread_id=1, posts=posts, is_thread_start=True, is_thread_end=True, scene_index=0)
        assert scene.post_count == 3

    def test_empty_scene(self):
        scene = Scene(thread_id=1, posts=[], is_thread_start=True, is_thread_end=True, scene_index=0)
        assert scene.first_post_id is None
        assert scene.last_post_id is None
        assert scene.post_count == 0


class TestCorpusReader:
    def test_get_post_returns_post_with_all_tags(self, corpus: CorpusReader):
        # Get any post that exists
        post = corpus.get_post(1)
        # Post 1 might not exist, so just check the type if it does
        if post is not None:
            assert isinstance(post, StoryPost)
            assert post.post_id == 1
            assert isinstance(post.tags, list)
            assert post.thread_id is not None

    def test_get_post_not_found_returns_none(self, corpus: CorpusReader):
        post = corpus.get_post(999999999)
        assert post is None

    def test_iter_threads_yields_threads(self, corpus: CorpusReader):
        threads = list(corpus.iter_threads())
        assert len(threads) > 0
        assert all(isinstance(t, Thread) for t in threads)
        assert all(t.id is not None for t in threads)

    def test_get_posts_range_filters_by_thread(self, corpus: CorpusReader):
        # Get a thread ID from the database
        threads = list(corpus.iter_threads())
        if not threads:
            pytest.skip("No threads in database")

        thread_id = threads[0].id
        posts = corpus.get_posts_range(thread_id, tag_filter=None)

        # All posts should be from the same thread
        assert all(p.thread_id == thread_id for p in posts)

    def test_get_posts_range_with_tag_filter(self, corpus: CorpusReader):
        threads = list(corpus.iter_threads())
        if not threads:
            pytest.skip("No threads in database")

        thread_id = threads[0].id
        posts = corpus.get_posts_range(thread_id, tag_filter="qm_post")

        # All posts should have the qm_post tag
        for post in posts:
            assert post.has_tag("qm_post"), f"Post {post.post_id} missing qm_post tag"

    def test_get_adjacent_posts_returns_context(self, corpus: CorpusReader):
        # Find a post in the middle of a thread
        threads = list(corpus.iter_threads())
        if not threads:
            pytest.skip("No threads in database")

        # Get posts from first thread
        posts = corpus.get_posts_range(threads[0].id)
        if len(posts) < 5:
            pytest.skip("Not enough posts for adjacency test")

        middle_post = posts[len(posts) // 2]
        adjacent = corpus.get_adjacent_posts(middle_post.post_id, before=2, after=2)

        # Should have the middle post and some context
        assert len(adjacent) >= 1
        assert any(p.post_id == middle_post.post_id for p in adjacent)

    def test_iter_all_posts_with_tag_filter(self, corpus: CorpusReader):
        # Get first 10 qm_posts
        posts = []
        for post in corpus.iter_all_posts(tag_filter="qm_post"):
            posts.append(post)
            if len(posts) >= 10:
                break

        # All should have qm_post tag
        for post in posts:
            assert post.has_tag("qm_post")

    def test_iter_posts_backwards_compat(self, corpus: CorpusReader):
        # Create reader with old-style parameters
        reader = CorpusReader(CORPUS_DB, tag_filter="qm_post", chunk_size=5)

        batches = []
        for batch in reader.iter_posts(limit=15):
            batches.append(batch)
            assert isinstance(batch, list)
            assert all(isinstance(p, StoryPost) for p in batch)

        reader.close()

        # Should have gotten batches
        assert len(batches) > 0
        # Each batch should be <= chunk_size
        for batch in batches:
            assert len(batch) <= 5


class TestSceneBatcher:
    def test_iter_scenes_yields_scenes(self, batcher: SceneBatcher):
        scenes = []
        for scene in batcher.iter_scenes():
            scenes.append(scene)
            if len(scenes) >= 5:
                break

        assert len(scenes) > 0
        assert all(isinstance(s, Scene) for s in scenes)

    def test_scene_contains_only_qm_posts(self, batcher: SceneBatcher):
        for scene in batcher.iter_scenes():
            for post in scene.posts:
                assert post.has_tag("qm_post"), f"Post {post.post_id} in scene missing qm_post tag"
            # Only check a few scenes
            if scene.scene_index >= 2:
                break

    def test_scene_posts_are_contiguous_in_thread(self, batcher: SceneBatcher):
        for scene in batcher.iter_scenes():
            if len(scene.posts) < 2:
                continue

            # All posts in scene should be from same thread
            assert all(p.thread_id == scene.thread_id for p in scene.posts)

            # Check first scene is enough
            break

    def test_iter_scenes_resumes_from_post_id(self, batcher: SceneBatcher):
        # Get first scene
        first_scene = next(batcher.iter_scenes())
        if not first_scene.posts:
            pytest.skip("First scene is empty")

        last_post_id = first_scene.last_post_id

        # Resume from after that post
        resumed_scenes = list(batcher.iter_scenes(start_after_post_id=last_post_id))

        # Should not include the first scene's posts
        if resumed_scenes:
            for scene in resumed_scenes[:3]:
                for post in scene.posts:
                    assert post.post_id > last_post_id

    def test_iter_scenes_in_thread(self, batcher: SceneBatcher, corpus: CorpusReader):
        # Find a thread with qm_posts
        threads = list(corpus.iter_threads())
        if not threads:
            pytest.skip("No threads")

        for thread in threads[:5]:  # Check first 5 threads
            scenes = list(batcher.iter_scenes_in_thread(thread.id))
            if scenes:
                # All scenes should be from this thread
                for scene in scenes:
                    assert scene.thread_id == thread.id
                break
