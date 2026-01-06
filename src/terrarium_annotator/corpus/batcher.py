"""Scene batching for corpus posts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from terrarium_annotator.corpus.models import Scene, StoryPost, ThreadContent

if TYPE_CHECKING:
    from terrarium_annotator.corpus.reader import CorpusReader


class SceneBatcher:
    """Group posts into scenes (contiguous qm_post runs)."""

    QM_POST_TAG = "qm_post"

    def __init__(self, corpus: CorpusReader) -> None:
        """Initialize with corpus reader."""
        self._corpus = corpus

    def iter_scenes(
        self,
        start_after_post_id: int | None = None,
    ) -> Iterator[Scene]:
        """
        Yield scenes starting after given post.

        A scene is a contiguous run of qm_post-tagged posts within a single thread.
        Scene ends when:
        - Next post lacks qm_post tag
        - Thread boundary reached
        """
        current_scene_posts: list[StoryPost] = []
        current_thread_id: int | None = None
        scene_index = 0
        is_first_scene_in_thread = True

        # Track if we've seen the first qm_post in the current thread
        for post in self._corpus.iter_all_posts(
            start_after_post_id=start_after_post_id,
            tag_filter=None,  # We need all posts to detect scene boundaries
        ):
            is_qm_post = post.has_tag(self.QM_POST_TAG)

            # Thread boundary check
            if current_thread_id is not None and post.thread_id != current_thread_id:
                # Emit current scene if any
                if current_scene_posts:
                    yield Scene(
                        thread_id=current_thread_id,
                        posts=current_scene_posts,
                        is_thread_start=is_first_scene_in_thread,
                        is_thread_end=True,  # Thread is ending
                        scene_index=scene_index,
                    )
                    current_scene_posts = []
                    scene_index = 0

                # Reset for new thread
                current_thread_id = post.thread_id
                is_first_scene_in_thread = True

            # Initialize thread tracking
            if current_thread_id is None:
                current_thread_id = post.thread_id

            if is_qm_post:
                # Add to current scene
                current_scene_posts.append(post)
            else:
                # Non-qm_post breaks the scene
                if current_scene_posts:
                    yield Scene(
                        thread_id=current_thread_id,
                        posts=current_scene_posts,
                        is_thread_start=is_first_scene_in_thread,
                        is_thread_end=False,  # More posts may come in this thread
                        scene_index=scene_index,
                    )
                    current_scene_posts = []
                    scene_index += 1
                    is_first_scene_in_thread = False

        # Emit final scene if any
        if current_scene_posts and current_thread_id is not None:
            yield Scene(
                thread_id=current_thread_id,
                posts=current_scene_posts,
                is_thread_start=is_first_scene_in_thread,
                is_thread_end=True,  # End of corpus = end of thread
                scene_index=scene_index,
            )

    def iter_scenes_in_thread(
        self,
        thread_id: int,
        *,
        start_after_post_id: int | None = None,
    ) -> Iterator[Scene]:
        """
        Yield scenes within a specific thread.

        Useful for focused processing of a single thread.
        """
        current_scene_posts: list[StoryPost] = []
        scene_index = 0
        is_first_scene = True

        for post in self._corpus.iter_posts_by_thread(
            thread_id,
            start_after_post_id=start_after_post_id,
        ):
            is_qm_post = post.has_tag(self.QM_POST_TAG)

            if is_qm_post:
                current_scene_posts.append(post)
            else:
                if current_scene_posts:
                    yield Scene(
                        thread_id=thread_id,
                        posts=current_scene_posts,
                        is_thread_start=is_first_scene,
                        is_thread_end=False,
                        scene_index=scene_index,
                    )
                    current_scene_posts = []
                    scene_index += 1
                    is_first_scene = False

        # Emit final scene
        if current_scene_posts:
            yield Scene(
                thread_id=thread_id,
                posts=current_scene_posts,
                is_thread_start=is_first_scene,
                is_thread_end=True,
                scene_index=scene_index,
            )


class ThreadIterator:
    """Iterate through threads, yielding all QM posts per thread (F11).

    Unlike SceneBatcher which yields scene-by-scene within threads,
    ThreadIterator yields entire threads at once for batch processing.
    """

    QM_POST_TAG = "qm_post"

    def __init__(self, corpus: CorpusReader) -> None:
        """Initialize with corpus reader."""
        self._corpus = corpus

    def iter_threads(
        self,
        start_after_thread_id: int | None = None,
        start_after_post_id: int | None = None,
    ) -> Iterator[ThreadContent]:
        """
        Yield ThreadContent for each thread in chronological order.

        Args:
            start_after_thread_id: Skip threads until after this thread ID.
            start_after_post_id: If provided along with start_after_thread_id,
                resume within a thread from this post position.

        Yields:
            ThreadContent with all QM posts for each thread.
        """
        thread_position = 0
        skip_until_thread = start_after_thread_id
        resume_post_id = start_after_post_id

        for thread in self._corpus.iter_threads():
            # Skip threads until we find the resume point
            if skip_until_thread is not None:
                if thread.id != skip_until_thread:
                    thread_position += 1
                    continue
                # Found the thread - clear skip flag
                skip_until_thread = None
                # If we have a post resume point, this is the thread to resume in
                # Otherwise skip this completed thread entirely
                if resume_post_id is None:
                    thread_position += 1
                    continue

            thread_position += 1

            # Collect all QM posts for this thread
            qm_posts: list[StoryPost] = []
            for post in self._corpus.iter_posts_by_thread(
                thread.id,
                start_after_post_id=resume_post_id if thread.id == start_after_thread_id else None,
            ):
                if post.has_tag(self.QM_POST_TAG):
                    qm_posts.append(post)

            # Clear resume point after first thread
            resume_post_id = None

            # Skip threads with no QM content
            if not qm_posts:
                continue

            yield ThreadContent(
                thread_id=thread.id,
                thread_title=thread.title,
                qm_posts=qm_posts,
                thread_position=thread_position,
            )

    def get_thread_qm_posts(self, thread_id: int) -> list[StoryPost]:
        """Get all QM posts for a specific thread.

        Useful for loading previous thread context.
        """
        qm_posts: list[StoryPost] = []
        for post in self._corpus.iter_posts_by_thread(thread_id):
            if post.has_tag(self.QM_POST_TAG):
                qm_posts.append(post)
        return qm_posts
