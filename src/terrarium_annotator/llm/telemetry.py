"""Usage telemetry: InstrumentedClient + per-run JSONL usage records.

Design: docs/plan/aspirations.md §"NEXT: usage telemetry". One JSONL record
per chat() call, written to data/recordings/usage/<run_id>.jsonl:

- run ID + call-type label on every record (annotation / merge-settle /
  chat / researcher) so per-call-type aggregation is possible.
- prompt/completion CHAR counts + tool-call count, always present.
- context-component char sizes (cards/digest/scene/system), reported by the
  prompt assembler via set_context(); "unknown" when not reported — no
  silent gaps.
- provider `usage` fields ONLY when the provider reported them (null
  otherwise, never invented): OpenAI-style `raw["usage"]`, or whatever the
  omp RPC agent_end frame carries (field names undocumented — see
  docs/worklog/2026-09-13-telemetry.md Q1).
- every ATTEMPT is a first-class sub-record (attempt number, status, error
  type): clients that retry internally emit AttemptEvents via their
  attempt_observer seam; clients without the seam get a single synthetic
  attempt reflecting the outer outcome. The retry tax is a cost component.

Zero behavior change: pure wrapper; the annotator/researcher/chat are
constructible with or without it, and defaults are unchanged.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from terrarium_annotator.llm.base import (
    AttemptEvent,
    ChatClient,
    ChatResponse,
)

COMPONENT_KEYS = ("system", "cards", "digest", "scene")
UNKNOWN = "unknown"
CALL_TYPES = ("annotation", "merge-settle", "chat", "researcher")


def make_run_id(pass_id: str) -> str:
    """Stable run identifier: pass ID + UTC start timestamp."""
    return f"{pass_id}-{datetime.now(UTC):%Y%m%dT%H%M%S}"


def usage_log_path(usage_dir: str | Path, run_id: str) -> Path:
    """Per-run records file; run_id is sanitized for the filesystem only."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", run_id)
    return Path(usage_dir) / f"{safe}.jsonl"


def _usage_from_raw(raw: dict) -> dict | None:
    """Provider-reported usage, when present. Never invented."""
    agent_end = raw.get("agent_end")
    for candidate in (raw.get("usage"), (agent_end or {}).get("usage")):
        if isinstance(candidate, dict):
            return dict(candidate)
    return None


def _prompt_chars(messages: list[dict], tools: list[dict] | None) -> int:
    total = 0
    for m in messages:
        total += len(m.get("content") or "")
        tool_calls = m.get("tool_calls")
        if tool_calls:
            total += len(json.dumps(tool_calls))
    if tools:
        total += len(json.dumps(tools))
    return total


def _completion_chars(resp: ChatResponse) -> int:
    total = len(resp.content or "")
    for tc in resp.tool_calls:
        total += len(tc.name) + len(json.dumps(tc.arguments))
    return total


class UsageLog:
    """Append-only JSONL sink for per-call usage records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def record(self, rec: dict) -> None:
        self._fh.write(json.dumps(rec, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class InstrumentedClient:
    """ChatClient wrapper recording per-call usage to a UsageLog."""

    def __init__(
        self,
        inner: ChatClient,
        log: UsageLog,
        *,
        run_id: str,
        call_type: str = "unknown",
    ) -> None:
        self._inner = inner
        self._log = log
        self.run_id = run_id
        self._call_type = call_type
        self._context: dict[str, int | str] = {k: UNKNOWN for k in COMPONENT_KEYS}
        self._seq = 0

    def set_call_type(self, label: str) -> None:
        """Label subsequent calls (annotation / merge-settle / chat / ...)."""
        self._call_type = label

    def set_context(self, components: dict[str, int | str]) -> None:
        """Report context-component char sizes computed at assembly.

        Applies to subsequent calls until changed; keys outside
        COMPONENT_KEYS are rejected to keep the schema stable.
        """
        bad = set(components) - set(COMPONENT_KEYS)
        if bad:
            raise ValueError(f"unknown context components: {sorted(bad)}")
        self._context.update(components)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        self._seq += 1
        attempts: list[AttemptEvent] = []
        inner = self._inner
        # Wire the inner client's internal-retry seam if it has one,
        # composing with any pre-existing observer; restore afterwards.
        prev = getattr(inner, "attempt_observer", None)
        hooked = hasattr(inner, "attempt_observer")
        if hooked:

            def observe(ev: AttemptEvent) -> None:
                attempts.append(ev)
                if prev is not None:
                    prev(ev)

            inner.attempt_observer = observe  # type: ignore[attr-defined]
        t0 = time.monotonic()
        resp: ChatResponse | None = None
        status, error_type = "success", None
        try:
            resp = inner.chat(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens
            )
            return resp
        except Exception as exc:
            status, error_type = "error", type(exc).__name__
            raise
        finally:
            duration = time.monotonic() - t0
            if hooked:
                inner.attempt_observer = prev  # type: ignore[attr-defined]
            if not attempts:
                # Client without an internal-retry seam: the outer call IS
                # the only attempt.
                attempts.append(
                    AttemptEvent(
                        attempt=1,
                        status=status,
                        error_type=error_type,
                        duration_s=duration,
                    )
                )
            prompt_chars = _prompt_chars(messages, tools)
            completion_chars = _completion_chars(resp) if resp is not None else None
            self._log.record(
                {
                    "run_id": self.run_id,
                    "call_type": self._call_type,
                    "seq": self._seq,
                    "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                    "duration_s": round(duration, 3),
                    "status": status,
                    "error_type": error_type,
                    "attempts": [
                        {
                            "attempt": e.attempt,
                            "status": e.status,
                            "error_type": e.error_type,
                            "duration_s": round(e.duration_s, 3),
                        }
                        for e in attempts
                    ],
                    "prompt_chars": prompt_chars,
                    "completion_chars": completion_chars,
                    "tool_calls": len(resp.tool_calls) if resp is not None else 0,
                    "context": dict(self._context),
                    "usage": _usage_from_raw(resp.raw) if resp is not None else None,
                    # chars/4 heuristic — estimates, NOT provider numbers.
                    "est_prompt_tokens": max(1, prompt_chars // 4),
                    "est_completion_tokens": (
                        max(1, completion_chars // 4) if completion_chars else None
                    ),
                }
            )


def iter_records(path: str | Path) -> Iterator[dict]:
    """Yield records from one JSONL file or every *.jsonl under a dir."""
    p = Path(path)
    files = sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    for f in files:
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _new_bucket() -> dict:
    return {
        "calls": 0,
        "errors": 0,
        "prompt_chars": 0,
        "completion_chars": 0,
        "tool_calls": 0,
        "est_prompt_tokens": 0,
        "est_completion_tokens": 0,
        "provider_usage_records": 0,
        "attempts": {"success": 0, "error": 0},
        "error_types": {},
    }


def _add(bucket: dict, rec: dict) -> None:
    bucket["calls"] += 1
    if rec.get("status") == "error":
        bucket["errors"] += 1
        et = rec.get("error_type") or "unknown"
        bucket["error_types"][et] = bucket["error_types"].get(et, 0) + 1
    bucket["prompt_chars"] += rec.get("prompt_chars") or 0
    bucket["completion_chars"] += rec.get("completion_chars") or 0
    bucket["tool_calls"] += rec.get("tool_calls") or 0
    bucket["est_prompt_tokens"] += rec.get("est_prompt_tokens") or 0
    bucket["est_completion_tokens"] += rec.get("est_completion_tokens") or 0
    if rec.get("usage") is not None:
        bucket["provider_usage_records"] += 1
    for a in rec.get("attempts") or []:
        status = a.get("status")
        if status in bucket["attempts"]:
            bucket["attempts"][status] += 1


def summarize(path: str | Path) -> dict:
    """Aggregate usage records per run and per call type."""
    runs: dict[str, dict] = {}
    call_types: dict[str, dict] = {}
    for rec in iter_records(path):
        run_bucket = runs.setdefault(rec.get("run_id", "unknown"), _new_bucket())
        _add(run_bucket, rec)
        ct_bucket = call_types.setdefault(
            rec.get("call_type", "unknown"), _new_bucket()
        )
        _add(ct_bucket, rec)
    return {"runs": runs, "call_types": call_types}


def _format_bucket(name: str, b: dict) -> str:
    attempts = ", ".join(f"{k}={v}" for k, v in sorted(b["attempts"].items()))
    line = (
        f"{name}: calls={b['calls']} errors={b['errors']} "
        f"chars_in={b['prompt_chars']} chars_out={b['completion_chars']} "
        f"tool_calls={b['tool_calls']} attempts[{attempts}] "
        f"est_tokens_in={b['est_prompt_tokens']} "
        f"est_tokens_out={b['est_completion_tokens']} "
        f"provider_usage={b['provider_usage_records']}"
    )
    if b["error_types"]:
        errs = ", ".join(f"{k}×{v}" for k, v in sorted(b["error_types"].items()))
        line += f"\n    error_types: {errs}"
    return line


def format_summary(summary: dict) -> str:
    lines = ["per run:"]
    for name, b in sorted(summary["runs"].items()):
        lines.append("  " + _format_bucket(name, b))
    lines.append("per call type:")
    for name, b in sorted(summary["call_types"].items()):
        lines.append("  " + _format_bucket(name, b))
    return "\n".join(lines)
