"""Scene batching for corpus posts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from terrarium_annotator.corpus.models import Scene, StoryPost

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
