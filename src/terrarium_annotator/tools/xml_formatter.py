"""XML formatting utilities for tool responses."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terrarium_annotator.corpus import StoryPost
    from terrarium_annotator.storage import GlossaryEntry


def format_glossary_entry(entry: GlossaryEntry) -> str:
    """Format a glossary entry as XML element."""
    tags_attr = f' tags="{escape(",".join(entry.tags))}"' if entry.tags else ""
    status_attr = f' status="{escape(entry.status)}"'
    return (
        f'<entry id="{entry.id}" term="{escape(entry.term)}"{status_attr}{tags_attr}>'
        f"{escape(entry.definition)}</entry>"
    )


def format_post(post: StoryPost) -> str:
    """Format a post as XML element."""
    attrs = [f'id="{post.post_id}"', f'thread_id="{post.thread_id}"']
    if post.author:
        attrs.append(f'author="{escape(post.author)}"')
    if post.created_at:
        attrs.append(f'ts="{post.created_at.isoformat()}"')
    if post.tags:
        attrs.append(f'tags="{escape(",".join(post.tags))}"')

    attr_str = " ".join(attrs)
    body = escape(post.body or "").strip()
    return f"<post {attr_str}>{body}</post>"


def format_search_results(entries: list[GlossaryEntry], query: str) -> str:
    """Format search results as XML."""
    lines = [f'<search_results query="{escape(query)}" count="{len(entries)}">']
    for entry in entries:
        lines.append(f"  {format_glossary_entry(entry)}")
    lines.append("</search_results>")
    return "\n".join(lines)


def format_posts(posts: list[StoryPost], thread_id: int) -> str:
    """Format multiple posts as XML."""
    lines = [f'<posts thread_id="{thread_id}" count="{len(posts)}">']
    for post in posts:
        lines.append(f"  {format_post(post)}")
    lines.append("</posts>")
    return "\n".join(lines)


def format_error(message: str, code: str | None = None) -> str:
    """Format error as XML."""
    code_attr = f' code="{escape(code)}"' if code else ""
    return f"<error{code_attr}>{escape(message)}</error>"


def format_success(message: str, entry_id: int | None = None) -> str:
    """Format success response as XML."""
    id_attr = f' entry_id="{entry_id}"' if entry_id is not None else ""
    return f"<success{id_attr}>{escape(message)}</success>"
