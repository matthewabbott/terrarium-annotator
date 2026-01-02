"""OpenAI function calling schema definitions."""

from __future__ import annotations

GLOSSARY_SEARCH_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "glossary_search",
        "description": "Search the glossary for entries matching a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Full-text search query for terms and definitions",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (all must match)",
                },
                "status": {
                    "type": "string",
                    "enum": ["confirmed", "tentative", "all"],
                    "description": "Filter by status (default: all)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 10)",
                },
            },
            "required": ["query"],
        },
    },
}

GLOSSARY_CREATE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "glossary_create",
        "description": "Create a new glossary entry.",
        "parameters": {
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": "The term to define",
                },
                "definition": {
                    "type": "string",
                    "description": "The definition of the term",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization (e.g., character, location, faction)",
                },
                "status": {
                    "type": "string",
                    "enum": ["tentative", "confirmed"],
                    "description": "Entry status (default: tentative)",
                },
            },
            "required": ["term", "definition", "tags"],
        },
    },
}

GLOSSARY_UPDATE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "glossary_update",
        "description": "Update an existing glossary entry.",
        "parameters": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "integer",
                    "description": "ID of the entry to update",
                },
                "term": {
                    "type": "string",
                    "description": "New term (optional)",
                },
                "definition": {
                    "type": "string",
                    "description": "New definition (optional)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New tags (optional)",
                },
                "status": {
                    "type": "string",
                    "enum": ["tentative", "confirmed"],
                    "description": "New status (optional)",
                },
            },
            "required": ["entry_id"],
        },
    },
}

GLOSSARY_DELETE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "glossary_delete",
        "description": "Delete a glossary entry.",
        "parameters": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "integer",
                    "description": "ID of the entry to delete",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for deletion (for audit trail)",
                },
            },
            "required": ["entry_id", "reason"],
        },
    },
}

CORPUS_READ_POST_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "read_post",
        "description": "Read a specific post from the corpus.",
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "integer",
                    "description": "ID of the post to read",
                },
            },
            "required": ["post_id"],
        },
    },
}

CORPUS_READ_THREAD_RANGE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "read_thread_range",
        "description": "Read a range of posts from a thread.",
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "integer",
                    "description": "ID of the thread to read from",
                },
                "start_post_id": {
                    "type": "integer",
                    "description": "Start of post range (optional)",
                },
                "end_post_id": {
                    "type": "integer",
                    "description": "End of post range (optional)",
                },
                "tag_filter": {
                    "type": "string",
                    "description": "Filter posts by tag (optional)",
                },
            },
            "required": ["thread_id"],
        },
    },
}

# All tool schemas
ALL_TOOL_SCHEMAS: list[dict] = [
    GLOSSARY_SEARCH_SCHEMA,
    GLOSSARY_CREATE_SCHEMA,
    GLOSSARY_UPDATE_SCHEMA,
    GLOSSARY_DELETE_SCHEMA,
    CORPUS_READ_POST_SCHEMA,
    CORPUS_READ_THREAD_RANGE_SCHEMA,
]


def get_all_tool_schemas(include_snapshot_tools: bool = False) -> list[dict]:
    """Return all tool schemas in OpenAI function calling format.

    Args:
        include_snapshot_tools: Include snapshot tools (F7). Currently no-op.

    Returns:
        List of tool definitions, each with 'type': 'function' and 'function'
        containing 'name', 'description', and 'parameters'.

    Available tools:
        - glossary_search: Search glossary by query, tags, status
        - glossary_create: Create new glossary entry
        - glossary_update: Update existing entry
        - glossary_delete: Delete entry with reason
        - read_post: Read single post by ID
        - read_thread_range: Read posts in thread range
    """
    # Snapshot tools will be added in F7
    return list(ALL_TOOL_SCHEMAS)
