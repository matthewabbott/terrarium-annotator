"""Agent tools: OpenAI-style schemas + dispatch with guards.

Design: docs/design/v2-architecture.md §5. Write-path tools go through the
glossary quote gate; failures return an error payload to the model (the
loop continues) rather than raising. Read-only tools never mutate state.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import (
    Evidence,
    GlossaryError,
    GlossaryStore,
    Provenance,
)
from terrarium_annotator.llm import ToolCall
from terrarium_annotator.memory import StoryLog

RECALL_LIMIT = 20


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_EVIDENCE_PROP = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "post_id": {"type": "integer"},
            "quote": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["narrated", "claimed", "inferred"],
                "description": "epistemic status: text states it / a "
                "character claims it / you infer it",
            },
        },
        "required": ["post_id", "quote"],
        "additionalProperties": False,
    },
    "minItems": 1,
}

TOOL_SCHEMAS = [
    _schema(
        "propose_entry",
        "Create a glossary entry for a setting-specific term. Requires "
        "verbatim evidence quotes from the current batch.",
        {
            "term": {"type": "string"},
            "gloss": {"type": "string"},
            "evidence": _EVIDENCE_PROP,
            "tags": {"type": "array", "items": {"type": "string"}},
            "keys": {"type": "array", "items": {"type": "string"}},
        },
        ["term", "gloss", "evidence"],
    ),
    _schema(
        "update_entry",
        "Append a revised definition to an existing entry. Requires new "
        "verbatim evidence quotes.",
        {
            "term": {"type": "string"},
            "gloss": {"type": "string"},
            "evidence": _EVIDENCE_PROP,
        },
        ["term", "gloss", "evidence"],
    ),
    _schema(
        "add_alias",
        "Register an alternate surface form for an entry. Requires a "
        "verbatim quote containing the alias.",
        {
            "term": {"type": "string"},
            "alias": {"type": "string"},
            "evidence": {
                "type": "object",
                "properties": {
                    "post_id": {"type": "integer"},
                    "quote": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["narrated", "claimed", "inferred"],
                    },
                },
                "required": ["post_id", "quote"],
                "additionalProperties": False,
            },
        },
        ["term", "alias", "evidence"],
    ),
    _schema(
        "fetch_entry",
        "Fetch the full page for a term: gloss, revision history, sources.",
        {"term": {"type": "string"}},
        ["term"],
    ),
    _schema(
        "fetch_post",
        "Fetch the body of one corpus post by id.",
        {"post_id": {"type": "integer"}},
        ["post_id"],
    ),
    _schema(
        "fetch_thread_range",
        "Fetch story posts of a thread: `start` (0-based) and `limit`.",
        {
            "thread_id": {"type": "integer"},
            "start": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        ["thread_id"],
    ),
    _schema(
        "recall_story",
        "Regex-search the story log (everything read so far).",
        {"pattern": {"type": "string"}},
        ["pattern"],
    ),
    _schema(
        "search_glossary",
        "Full-text search over glossary terms and definitions.",
        {"query": {"type": "string"}, "limit": {"type": "integer"}},
        ["query"],
    ),
    _schema(
        "search_corpus",
        "Substring-search every post body in the corpus (any thread, "
        "including unread ones). Researcher tool for finding evidence.",
        {"needle": {"type": "string"}, "limit": {"type": "integer"}},
        ["needle"],
    ),
    _schema(
        "rename_entry",
        "Retitle an entry to its best-fit wiki name; the old title becomes "
        "an alias. For consolidating to canonical naming.",
        {"term": {"type": "string"}, "new_term": {"type": "string"}},
        ["term", "new_term"],
    ),
    _schema(
        "propose_merge",
        "Propose merging two entries that share a referent. Queued for "
        "human review; NEVER merges directly. Requires a rationale and a "
        "verbatim quote mentioning one of the terms.",
        {
            "term_a": {"type": "string"},
            "term_b": {"type": "string"},
            "rationale": {"type": "string"},
            "evidence": {
                "type": "object",
                "properties": {
                    "post_id": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["post_id", "quote"],
                "additionalProperties": False,
            },
        },
        ["term_a", "term_b", "rationale", "evidence"],
    ),
]

# The annotator (serial reader) gets exactly these — no corpus lookahead,
# no retitle, no merge queue. The researcher gets ANNOTATOR_TOOLS plus its
# own. Chat surfaces use chat.READONLY_TOOLS.
ANNOTATOR_TOOLS = {
    "propose_entry",
    "update_entry",
    "add_alias",
    "fetch_entry",
    "fetch_post",
    "fetch_thread_range",
    "recall_story",
    "search_glossary",
}
RESEARCHER_TOOLS = ANNOTATOR_TOOLS | {
    "search_corpus",
    "rename_entry",
    "propose_merge",
}


class ToolDispatcher:
    """Routes model tool calls to stores; catches domain errors into payloads."""

    def __init__(
        self,
        glossary: GlossaryStore,
        corpus: CorpusReader,
        memory: StoryLog,
        provenance: Callable[[], Provenance],
        allowed: set[str] | None = None,
    ) -> None:
        self._glossary = glossary
        self._corpus = corpus
        self._memory = memory
        self._provenance = provenance
        # None = all tools (annotator). A set restricts both the advertised
        # schemas and dispatch itself (chat surfaces are read-only).
        self._allowed = allowed

    @property
    def schemas(self) -> list[dict]:
        if self._allowed is None:
            return TOOL_SCHEMAS
        return [s for s in TOOL_SCHEMAS if s["function"]["name"] in self._allowed]

    def dispatch(self, call: ToolCall) -> str:
        """Execute one tool call; always returns a JSON string result."""
        if self._allowed is not None and call.name not in self._allowed:
            return json.dumps(
                {"ok": False, "error": f"tool {call.name!r} not available here"}
            )
        try:
            result = self._route(call)
            return json.dumps({"ok": True, "result": result})
        except (
            GlossaryError,
            ValueError,
            KeyError,
            TypeError,
            sqlite3.IntegrityError,
        ) as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    def _provenance_for(self, post_ids: list[int]) -> Provenance:
        """Blame follows the evidence: thread_id comes from the first cited
        post (so researcher writes are never attributed to a synthetic
        thread 0); batch/pass fields come from the ambient provenance."""
        base = self._provenance()
        thread_id = base.thread_id
        if post_ids:
            looked_up = self._corpus.post_thread(post_ids[0])
            if looked_up is not None:
                thread_id = looked_up
        return Provenance(
            thread_id=thread_id,
            pass_id=base.pass_id,
            batch_lo=base.batch_lo,
            batch_hi=base.batch_hi,
            log_seq=base.log_seq,
            tree_version=base.tree_version,
        )

    def _route(self, call: ToolCall) -> object:
        a = call.arguments
        if call.name == "propose_entry":
            entry = self._glossary.propose_entry(
                term=a["term"],
                gloss=a["gloss"],
                evidence=self._evidence_list(a["evidence"]),
                provenance=self._provenance_for(
                    [e.post_id for e in self._evidence_list(a["evidence"])]
                ),
                tags=tuple(a.get("tags", ())),
                keys=tuple(a.get("keys", ())),
            )
            return {"entry_id": entry.id, "status": entry.status}
        if call.name == "update_entry":
            revision = self._glossary.update_entry(
                a["term"],
                gloss=a["gloss"],
                evidence=self._evidence_list(a["evidence"]),
                provenance=self._provenance_for(
                    [e.post_id for e in self._evidence_list(a["evidence"])]
                ),
            )
            return {"revision_id": revision.id}
        if call.name == "add_alias":
            ev = a["evidence"]
            post_id = int(ev["post_id"])
            self._glossary.add_alias(
                a["term"],
                a["alias"],
                evidence=Evidence(
                    post_id=post_id,
                    quote=ev["quote"],
                    mode=ev.get("mode", "narrated"),
                ),
                # Blame follows the cited post directly (None if unknown —
                # the store's quote gate rejects unknown posts anyway).
                thread_id=self._corpus.post_thread(post_id),
            )
            return {"alias": a["alias"]}
        if call.name == "fetch_entry":
            return self._fetch_entry(a["term"])
        if call.name == "fetch_post":
            body = self._corpus.post_body(int(a["post_id"]))
            if body is None:
                raise ValueError(f"post {a['post_id']} not in corpus")
            return {"post_id": a["post_id"], "body": body}
        if call.name == "search_glossary":
            # Search is the one place model-supplied text meets the DB
            # parser (FTS). A syntax-level OperationalError here becomes an
            # error payload; corrupt/missing tables elsewhere still abort.
            try:
                hits = self._glossary.search(a["query"], int(a.get("limit", 10)))
            except sqlite3.OperationalError as exc:
                raise ValueError(f"search failed: {exc}") from exc
            return {
                "entries": [
                    {"term": e.term, "gloss": e.gloss, "status": e.status} for e in hits
                ]
            }
        if call.name == "fetch_thread_range":
            posts = list(self._corpus.story_posts(int(a["thread_id"])))
            start = int(a.get("start", 0))
            limit = int(a.get("limit", 10))
            return {
                "posts": [
                    {"id": p.id, "time": p.time, "body": p.body}
                    for p in posts[start : start + limit]
                ],
                "total": len(posts),
            }
        if call.name == "recall_story":
            return self._recall(a["pattern"])
        if call.name == "search_corpus":
            posts = self._corpus.search_posts(a["needle"], int(a.get("limit", 20)))
            return {
                "posts": [
                    {
                        "id": p.id,
                        "thread_id": p.thread_id,
                        "time": p.time,
                        "body": p.body[:600],
                    }
                    for p in posts
                ],
                "total": len(posts),
            }
        if call.name == "rename_entry":
            target = self._glossary.get(a["term"])
            anchor = self._glossary.latest_source_post(target.id)
            entry = self._glossary.rename_entry(
                a["term"],
                a["new_term"],
                # Retitles carry no new evidence; blame anchors to the
                # entry's most recent cited post (never a synthetic thread).
                provenance=self._provenance_for([anchor] if anchor else []),
            )
            return {"term": entry.term, "aliases": list(entry.aliases)}
        if call.name == "propose_merge":
            ev = a["evidence"]
            qid = self._glossary.propose_merge(
                a["term_a"],
                a["term_b"],
                a["rationale"],
                Evidence(post_id=int(ev["post_id"]), quote=ev["quote"]),
            )
            return {"merge_queue_id": qid, "status": "pending"}
        raise ValueError(f"unknown tool {call.name!r}")

    @staticmethod
    def _evidence_list(raw: list[dict]) -> list[Evidence]:
        return [
            Evidence(
                post_id=int(e["post_id"]),
                quote=e["quote"],
                mode=e.get("mode", "narrated"),
            )
            for e in raw
        ]

    def _fetch_entry(self, term: str) -> dict:
        entry = self._glossary.find(term)
        if entry is None:
            raise ValueError(f"no entry for {term!r}")
        revisions = self._glossary.revisions(entry.id)
        sources = self._glossary._conn.execute(
            "SELECT thread_id, post_id, quote, revision_id FROM entry_source "
            "WHERE entry_id = ? ORDER BY id",
            (entry.id,),
        ).fetchall()
        return {
            "term": entry.term,
            "gloss": entry.gloss,
            "status": entry.status,
            "tags": list(entry.tags),
            "aliases": list(entry.aliases),
            "revisions": [
                {
                    "gloss": r.gloss,
                    "thread_id": r.provenance.thread_id,
                    "log_seq": r.provenance.log_seq,
                    "created_at": r.created_at,
                }
                for r in revisions
            ],
            "sources": [
                {"thread_id": s[0], "post_id": s[1], "quote": s[2]} for s in sources
            ],
        }

    def _recall(self, pattern: str) -> dict:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"bad regex: {exc}") from exc
        hits = [
            {"seq": e.seq, "thread_id": e.thread_id, "gist": e.gist}
            for e in self._memory.slice(0, self._memory.log_len())
            if rx.search(e.gist)
        ]
        return {"matches": hits[-RECALL_LIMIT:], "total": len(hits)}
