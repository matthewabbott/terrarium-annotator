"""The annotation runner: batch loop, context assembly, tool loop, memory.

Design: docs/design/v2-architecture.md §2. Per batch of story posts:
assemble [system + digest + injected cards + batch], let the model read
and call tools (quote-gated writes), append one gist to the story log,
record the transcript, checkpoint run_state. At thread close, settle due
merge-tree blocks with model-written summaries (OptMem nap semantics:
blocks of <=16 entries compress from raw gists, larger blocks from their
two halves' summaries).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from math import log1p
from pathlib import Path

from terrarium_annotator.corpus import DEFAULT_BATCH_SIZE, Batch, CorpusReader, Thread
from terrarium_annotator.glossary import GlossaryStore, Provenance
from terrarium_annotator.inject import CardView, select_cards
from terrarium_annotator.llm import ChatClient, ChatClientError, ChatResponse
from terrarium_annotator.llm.telemetry import InstrumentedClient
from terrarium_annotator.memory import StoryLog
from terrarium_annotator.state import (
    load_run_state,
    record_transcript,
    save_run_meta,
    save_run_state,
)
from terrarium_annotator.tools import ANNOTATOR_TOOLS, ToolDispatcher

"""Prompts are data (docs/design/prompt-laddering.md): the annotator's
system prompt lives in prompts/<variant>.md and is loaded at Runner
construction. Default = the reader-v2 vintage (the 2026-09 full-run
prompt)."""
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "reader-v2.md"


def load_prompt(path: str | Path | None) -> str:
    """Load a prompt file; None → the default reader-v2 vintage."""
    p = Path(path) if path is not None else DEFAULT_PROMPT_PATH
    return p.read_text(encoding="utf-8").strip()


MERGE_PROMPT = """Compress the following into ONE line of at most 280 characters. Keep what has lasting effect (entities, reveals, state changes), drop the rest. Invent nothing.

{body}"""

MAX_GIST_CHARS = 280


def count_tokens(text: str) -> int:
    """Heuristic token estimate (chars/4). vLLM tokenize can replace later."""
    return max(1, len(text) // 4)


@dataclass
class RunnerConfig:
    batch_size: int = DEFAULT_BATCH_SIZE
    context_tokens: int = 262_144
    card_budget_fraction: float = 0.15
    digest_budget_lines: int = 96
    pass_id: str = "dev"
    max_tool_rounds: int = 8
    max_response_tokens: int = 2048
    tag_priors: dict[str, float] | None = None  # salience weight per tag
    quota_threshold: float | None = 0.50  # weekly-window breaker; <=0 disables
    prompt_file: str | None = None  # prompts/<variant>.md; None = default


class Runner:
    """Drives one reading pass over the corpus. All state lives in the
    shared annotator DB (conn) plus the read-only corpus."""

    def __init__(
        self,
        corpus: CorpusReader,
        memory: StoryLog,
        glossary: GlossaryStore,
        llm: ChatClient,
        conn: sqlite3.Connection,
        config: RunnerConfig | None = None,
        telemetry: InstrumentedClient | None = None,
        quota_check: Callable[[], None] | None = None,
    ) -> None:
        self.corpus = corpus
        self.memory = memory
        self.glossary = glossary
        self.llm = llm
        self.conn = conn
        self.config = config or RunnerConfig()
        # Optional usage telemetry (default None = zero behavior change).
        # When wired, MUST be the same InstrumentedClient wrapping self.llm.
        self.telemetry = telemetry
        # Quota circuit breaker (default None = unchecked). Checked before
        # each batch; a halt leaves run_state at the next unprocessed batch,
        # so resume re-attempts it.
        self.quota_check = quota_check
        # Prompt vintage is part of pass identity (ladder provenance).
        self.system_prompt = load_prompt(self.config.prompt_file)
        self.prompt_name = (
            Path(self.config.prompt_file).stem
            if self.config.prompt_file
            else DEFAULT_PROMPT_PATH.stem
        )
        self._provenance: Provenance | None = None
        self.dispatcher = ToolDispatcher(
            glossary,
            corpus,
            memory,
            provenance=lambda: self._provenance_or_raise(),
            allowed=ANNOTATOR_TOOLS,
        )

    def _provenance_or_raise(self) -> Provenance:
        assert self._provenance is not None, "no batch in progress"
        return self._provenance

    def run(
        self,
        max_batches: int | None = None,
        only_threads: list[int] | None = None,
    ) -> None:
        """Read from the checkpoint (or the start) to the corpus end.

        run_state holds (thread_id, batch_index) of the NEXT unprocessed
        batch. Threads before it completed in a previous pass (their close
        state persisted); batches before it in the resume thread are
        skipped. If the checkpoint's thread is gone, everything is done.

        With `only_threads`, the pass covers exactly those thread IDs in
        chronological resolver order (input order is ignored). A checkpoint
        belonging to THIS pass is honored (supervisor resumes), unless the
        checkpoint's thread is outside the filter — then the filtered pass
        starts fresh from the filter's first thread.
        """
        threads = self.corpus.thread_order()
        save_run_meta(self.conn, "config", json.dumps(asdict(self.config)))
        save_run_meta(self.conn, "prompt", self.prompt_name)
        resume = load_run_state(self.conn, self.config.pass_id)
        if only_threads is not None:
            known = {t.id for t in threads}
            unknown = [t for t in only_threads if t not in known]
            if unknown:
                raise ValueError(f"unknown thread ids: {unknown}")
            wanted = set(only_threads)
            threads = [t for t in threads if t.id in wanted]
            # A checkpoint outside the filter doesn't constrain this pass.
            if resume is not None and resume[0] not in wanted:
                resume = None
        resume_idx, resume_batch = 0, 0
        if resume is not None:
            matches = [i for i, t in enumerate(threads) if t.id == resume[0]]
            if not matches:
                return  # checkpoint past the final thread: nothing to do
            resume_idx, resume_batch = matches[0], resume[1]

        processed = 0
        for ti, thread in enumerate(threads):
            if ti < resume_idx:
                continue
            for batch in self.corpus.batches(thread.id, self.config.batch_size):
                if ti == resume_idx and batch.index < resume_batch:
                    continue
                if self.quota_check is not None:
                    self.quota_check()
                self._process_batch(thread, batch)
                save_run_state(
                    self.conn, self.config.pass_id, thread.id, batch.index + 1
                )
                processed += 1
                if max_batches is not None and processed >= max_batches:
                    return
            self.memory.close_thread(thread.id)
            self._settle_merges()

    def _process_batch(self, thread: Thread, batch: Batch) -> None:
        cfg = self.config
        seq = self.memory.log_len()  # the gist this batch will become
        self._provenance = Provenance(
            thread_id=thread.id,
            batch_lo=batch.index,
            batch_hi=batch.index,
            log_seq=seq,
            pass_id=cfg.pass_id,
            tree_version=self.memory.tree_version,
        )

        tag_priors = cfg.tag_priors or {
            "mechanic": 1.5,
            "character": 1.5,
            "faction": 1.3,
            "location": 1.3,
        }
        mentions = self.glossary.mention_counts()
        cards = select_cards(
            batch.text,
            [
                CardView(
                    term=e.term,
                    keys=e.aliases,
                    gloss=e.gloss,
                    updated_at=e.updated_at,
                    salience=log1p(mentions.get(e.id, 0))
                    * max((tag_priors.get(t, 1.0) for t in e.tags), default=1.0),
                )
                for e in self._all_entries()
            ],
            budget_tokens=int(cfg.context_tokens * cfg.card_budget_fraction),
            count_tokens=count_tokens,
        )
        digest = "\n".join(
            f"#{i.lo}-{i.hi - 1} {i.text}"
            for i in self.memory.cover(cfg.digest_budget_lines)
        )

        cards_text = "\n".join(f"{c.term}: {c.gloss}" for c in cards)
        scene_text = "\n\n".join(f"[post {p.id}]\n{p.body}" for p in batch.posts)
        if self.telemetry is not None:
            self.telemetry.set_call_type("annotation")
            # Component char sizes computed here, at assembly.
            self.telemetry.set_context(
                {
                    "system": len(self.system_prompt),
                    "cards": len(cards_text),
                    "digest": len(digest),
                    "scene": len(scene_text),
                }
            )

        user = (
            f"<story_so_far>\n{digest}\n</story_so_far>\n\n"
            f"<known_glossary>\n" + cards_text + "\n</known_glossary>\n\n"
            f"<batch thread={thread.id} index={batch.index}>\n"
            + scene_text
            + "\n</batch>"
        )
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user},
        ]
        record_transcript(
            self.conn,
            pass_id=cfg.pass_id,
            thread_id=thread.id,
            batch_index=batch.index,
            log_seq=seq,
            role="user",
            content=user,
        )

        response = self._chat(messages)
        rounds = 0
        while response.tool_calls and rounds < cfg.max_tool_rounds:
            rounds += 1
            self._record(thread, batch, seq, "assistant", response)
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [self._tc_json(c) for c in response.tool_calls],
                }
            )
            for call in response.tool_calls:
                result = self.dispatcher.dispatch(call)
                record_transcript(
                    self.conn,
                    pass_id=cfg.pass_id,
                    thread_id=thread.id,
                    batch_index=batch.index,
                    log_seq=seq,
                    role="tool",
                    content=result,
                    tool_calls=None,
                )
                messages.append(
                    {
                        "role": "tool",
                        "name": call.name,
                        "content": result,
                        "tool_call_id": call.id,
                    }
                )
            response = self._chat(messages)

        gist = self._gist_from(response, thread, batch)
        self.memory.append(thread.id, gist, batch_lo=batch.index, batch_hi=batch.index)
        self._record(thread, batch, seq, "assistant", response)

    def _chat(self, messages: list[dict]) -> ChatResponse:
        try:
            return self.llm.chat(
                messages,
                tools=self.dispatcher.schemas,
                temperature=0.4,
                max_tokens=self.config.max_response_tokens,
            )
        except ChatClientError as exc:
            # Record the failure distinctly before halting; run_state is
            # untouched, so a later resume re-attempts this batch.
            prov = self._provenance
            record_transcript(
                self.conn,
                pass_id=self.config.pass_id,
                thread_id=prov.thread_id if prov else -1,
                batch_index=prov.batch_lo if prov and prov.batch_lo is not None else -1,
                log_seq=None,
                role="system",
                content=f"LLM call failed: {type(exc).__name__}: {exc}",
            )
            raise

    @staticmethod
    def _tc_json(call) -> dict:
        return {
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
        }

    def _record(
        self,
        thread: Thread,
        batch: Batch,
        seq: int,
        role: str,
        response: ChatResponse,
    ) -> None:
        record_transcript(
            self.conn,
            pass_id=self.config.pass_id,
            thread_id=thread.id,
            batch_index=batch.index,
            log_seq=seq,
            role=role,
            content=response.content,
            tool_calls=[self._tc_json(c) for c in response.tool_calls] or None,
        )

    @staticmethod
    def _gist_from(response: ChatResponse, thread: Thread, batch: Batch) -> str:
        content = (response.content or "").strip()
        if content:
            line = content.splitlines()[0].strip()
            if line:
                return line[:MAX_GIST_CHARS]
        return f"[batch {batch.index} of thread {thread.id}: no gist]"

    def _settle_merges(self) -> None:
        """OptMem nap: settle pending blocks in order, LLM-written summaries."""
        while self.memory.pending():
            lo, hi = self.memory.pending()[0]
            if hi - lo <= 16:
                body = "\n".join(e.gist for e in self.memory.slice(lo, hi))
            else:
                mid = (lo + hi) // 2
                body = "\n".join(
                    f"#{a}-{b - 1} {self.memory._settled(a, b)}"
                    for a, b in ((lo, mid), (mid, hi))
                )
            system = MERGE_PROMPT.format(body=body)
            if self.telemetry is not None:
                self.telemetry.set_call_type("merge-settle")
                self.telemetry.set_context(
                    {"system": len(system), "cards": 0, "digest": 0, "scene": 0}
                )
            response = self.llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": "Compress now."},
                ],
                max_tokens=256,
            )
            text = (response.content or "").strip().splitlines()[0][:MAX_GIST_CHARS]
            self.memory.settle(lo, hi, text)

    def _all_entries(self):
        conn = self.glossary._conn
        ids = [r[0] for r in conn.execute("SELECT id FROM entry ORDER BY updated_at")]
        return [self.glossary.get(i) for i in ids]
