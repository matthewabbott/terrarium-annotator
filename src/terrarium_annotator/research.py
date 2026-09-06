"""The researcher: top-down glossary work (design: critic-salience-epistemics §1).

Reads the GLOSSARY, investigates the corpus for evidence (any thread,
including unread ones — search is corpus-wide by design), and revises
entries through the normal quote-gated write path. Owns: alias harvest,
best-fit retitles, merge proposals (human queue — never auto-merges),
reread linking. The annotator reads serially; the researcher searches.
"""

from __future__ import annotations

import json
import sqlite3

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import GlossaryStore, Provenance
from terrarium_annotator.llm import ChatClient, ChatResponse
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.tools import RESEARCHER_TOOLS, ToolDispatcher

RESEARCHER_SYSTEM_PROMPT = """You are the terrarium researcher. Unlike the annotator (which reads the story serially), you work top-down: you hold the glossary and investigate the whole corpus for evidence with your tools, then revise entries.

Your charter, in priority order:
1. ALIASES: entries whose canonical term misses the forms the text actually uses (short names, surnames, colloquialisms). search_corpus for the surface forms; add_alias with a verbatim quote containing the alias.
2. TITLES: entries whose title is a bad wiki name (bare descriptors like 'old fort'). rename_entry to the best-fit name (the old title becomes an alias automatically).
3. DEDUPE: pairs of entries that share a referent (variant spellings, same thing twice). propose_merge with a rationale and a verbatim quote showing the shared referent. You NEVER merge directly — a human reviews the queue. Beware: this story reuses names for different things; only propose when the evidence is solid.
4. REVISIONS: entries whose gloss is wrong or thin given what you find. update_entry RESTATES the full corrected definition with fresh verbatim quotes.

Rules:
- Every write requires verbatim evidence quotes from corpus posts. No quote, no write.
- Mark epistemic mode on evidence: narrated / claimed / inferred.
- Characters in this story are renamed and re-revealed; entries may legitimately share surnames or titles without being the same person.
- When done, write a one-paragraph report of what you changed and what you left for humans."""


class Researcher:
    """Runs one top-down research session over the glossary + corpus."""

    def __init__(
        self,
        corpus: CorpusReader,
        glossary: GlossaryStore,
        memory: StoryLog,
        client: ChatClient,
        conn: sqlite3.Connection,
        pass_id: str = "research",
        max_rounds: int = 40,
    ) -> None:
        self.corpus = corpus
        self.glossary = glossary
        self.client = client
        self.conn = conn
        self.pass_id = pass_id
        self.max_rounds = max_rounds
        self._prov = Provenance(thread_id=0, pass_id=pass_id)
        self.dispatcher = ToolDispatcher(
            glossary,
            corpus,
            memory,
            provenance=lambda: self._prov,
            allowed=RESEARCHER_TOOLS,
        )

    def _glossary_overview(self) -> str:
        conn = self.glossary._conn
        rows = conn.execute("SELECT term, gloss FROM entry ORDER BY term").fetchall()
        deferred = self.glossary.deferred_candidates()
        lines = [f"- {t}: {g[:120]}" for t, g in rows]
        out = f"<glossary count={len(rows)}>\n" + "\n".join(lines) + "\n</glossary>"
        if deferred:
            out += (
                f"\n<deferred_candidates count={len(deferred)}>\n"
                + "\n".join(f"- {r[1]}" for r in deferred)
                + "\n</deferred_candidates>"
            )
        return out

    def research(self, focus: str | None = None) -> str:
        """One research session. Returns the researcher's final report."""
        focus_text = focus or "Work the charter in priority order."
        messages: list[dict] = [
            {"role": "system", "content": RESEARCHER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": self._glossary_overview()
                + f"\n\nFocus for this session: {focus_text}",
            },
        ]
        report = ""
        for _ in range(self.max_rounds):
            response: ChatResponse = self.client.chat(
                messages, tools=self.dispatcher.schemas
            )
            if not response.tool_calls:
                report = response.content or ""
                messages.append({"role": "assistant", "content": report})
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [self._tc_json(c) for c in response.tool_calls],
                }
            )
            for call in response.tool_calls:
                result = self.dispatcher.dispatch(call)
                messages.append(
                    {
                        "role": "tool",
                        "name": call.name,
                        "content": result,
                        "tool_call_id": call.id,
                    }
                )
        return report

    @staticmethod
    def _tc_json(call) -> dict:
        return {
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
        }
