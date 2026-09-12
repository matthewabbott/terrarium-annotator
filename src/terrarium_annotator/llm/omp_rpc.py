"""OmpRpcClient: ChatClient over `omp --mode rpc` (Kimi subscription path).

Design notes (goal boundaries):
- The annotator session is spawned with `--no-tools` — an INVARIANT, not a
  default: the model gets zero coding/filesystem tools, and the annotator's
  own dispatcher (parsing `<tool_call>` blocks from model text) is the only
  tool execution path. Any command lacking `--no-tools` is rejected at
  construction, and a preflight `get_state` on first use asserts the live
  session exposes no tools.
- Each chat() is a fresh process: our memory lives in the glossary and
  story log, so sessions stay stateless and contexts never accumulate.
- Tool convention (ours, not omp's): the model emits
  `<tool_call>{"name": ..., "arguments": {...}}</tool_call>` blocks in its
  text; results return as `<tool_result name="...">...</tool_result>`.
- Failure policy: each call gets `attempts` tries (default 2), each with a
  fresh process, covering transient empty responses (reasoning models) and
  RPC hangs (timeout). Persistent failure raises a distinct error class so
  the caller can halt WITHOUT advancing run state.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time

from terrarium_annotator.llm.base import (
    AttemptEvent,
    AttemptObserver,
    ChatClientError,
    ChatResponse,
    ToolCall,
)


class EmptyResponseError(ChatClientError):
    """Terminal response carried no assistant text (reasoning-model slip)."""


class RPCTimeoutError(ChatClientError):
    """No terminal frame within the per-attempt timeout."""


TOOL_CONVENTION = """To call a tool, emit one or more blocks in your reply, exactly:
<tool_call>{"name": "tool_name", "arguments": {"arg": "value"}}</tool_call>
Arguments must be a JSON object. After tool results arrive, continue.

Available tools:
{schemas}"""

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def serialize_messages(messages: list[dict], tools: list[dict] | None) -> str:
    """Flatten the chat history into one prompt for a stateless session."""
    parts: list[str] = []
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    if system:
        parts.append(f"<system>\n{system}\n</system>")
    if tools:
        schemas = json.dumps(
            [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "parameters": t["function"]["parameters"],
                }
                for t in tools
            ],
            indent=1,
        )
        parts.append(
            f"<instructions>\n{TOOL_CONVENTION.replace('{schemas}', schemas)}\n</instructions>"
        )
    for m in messages:
        if m["role"] == "user":
            parts.append(m["content"])
        elif m["role"] == "assistant":
            calls = m.get("tool_calls") or []
            call_text = "\n".join(
                "<tool_call>"
                + json.dumps(
                    {
                        "name": tc["function"]["name"],
                        "arguments": json.loads(tc["function"]["arguments"]),
                    }
                )
                + "</tool_call>"
                for tc in calls
            )
            body = m.get("content") or ""
            parts.append(
                f"<previous_assistant>\n{body}\n{call_text}\n</previous_assistant>"
            )
        elif m["role"] == "tool":
            parts.append(
                f'<tool_result name="{m.get("name", "tool")}">\n{m["content"]}\n</tool_result>'
            )
    return "\n\n".join(parts)


def parse_response_text(text: str) -> ChatResponse:
    """Split model text into content + tool calls (our convention)."""
    tool_calls: list[ToolCall] = []
    malformed: list[str] = []
    for match in TOOL_CALL_RE.finditer(text):
        try:
            payload = json.loads(match.group(1))
            tool_calls.append(
                ToolCall(
                    name=payload["name"],
                    arguments=payload.get("arguments", {}),
                    id=f"text_{len(tool_calls)}",
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            malformed.append(f"malformed tool_call ignored: {exc}")
    content = TOOL_CALL_RE.sub("", text).strip()
    if malformed:
        content = (content + "\n\n" + "\n".join(malformed)).strip()
    return ChatResponse(content=content or None, tool_calls=tuple(tool_calls))


def _reader_thread(proc: subprocess.Popen, lines: queue.Queue) -> None:
    """Drain stdout into a queue so timeouts work when the RPC is silent."""
    try:
        for line in proc.stdout:
            lines.put(line)
    except (ValueError, OSError):
        pass
    finally:
        lines.put(None)  # EOF sentinel


class OmpRpcClient:
    """ChatClient driving `omp --mode rpc --no-tools` (one process per call)."""

    def __init__(
        self,
        model: str = "kimi-k2.5",
        provider: str = "kimi-code",
        *,
        command: list[str] | None = None,
        timeout: float = 300.0,
        attempts: int = 2,
        preflight: bool = True,
        attempt_observer: AttemptObserver | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self._command = command or ["omp", "--mode", "rpc", "--no-tools"]
        # Invariant (goal boundary): no command may omit --no-tools. The
        # annotator session must expose zero coding/filesystem tools.
        if "--no-tools" not in self._command:
            raise ChatClientError("OmpRpcClient requires --no-tools in the command")
        self.timeout = timeout
        self.attempts = attempts
        self._preflight_done = not preflight
        # Read at call time: InstrumentedClient (re)wires it per call.
        self.attempt_observer = attempt_observer

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        """One prompt against a fresh stateless RPC process, retried up to
        `self.attempts` times (fresh process each) on EmptyResponseError /
        RPCTimeoutError. Retry budget is per call — no instance state. Every
        attempt (success or failure, retried or not) is reported to
        `self.attempt_observer` when one is wired."""
        prompt = serialize_messages(messages, tools)
        last_error: ChatClientError | None = None
        for attempt in range(1, self.attempts + 1):
            proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            t0 = time.monotonic()
            try:
                response = self._exchange(proc, prompt)
            except (EmptyResponseError, RPCTimeoutError) as exc:
                self._observe(attempt, "error", type(exc).__name__, t0)
                last_error = exc
            except ChatClientError as exc:
                # Not retried (protocol/launch failure) — still observed.
                self._observe(attempt, "error", type(exc).__name__, t0)
                raise
            else:
                self._observe(attempt, "success", None, t0)
                return response
            finally:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass  # process already gone
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        assert last_error is not None
        raise last_error

    def _observe(
        self, attempt: int, status: str, error_type: str | None, t0: float
    ) -> None:
        if self.attempt_observer is not None:
            self.attempt_observer(
                AttemptEvent(
                    attempt=attempt,
                    status=status,
                    error_type=error_type,
                    duration_s=time.monotonic() - t0,
                )
            )

    def _send(self, proc: subprocess.Popen, obj: dict) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def _exchange(self, proc: subprocess.Popen, prompt: str) -> ChatResponse:
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=_reader_thread, args=(proc, lines), daemon=True).start()

        self._send(
            proc, {"id": "0", "type": "negotiate_protocol", "protocolVersion": 2}
        )
        self._send(
            proc,
            {
                "id": "1",
                "type": "set_model",
                "provider": self.provider,
                "modelId": self.model,
            },
        )
        if not self._preflight_done:
            self._send(proc, {"id": "2", "type": "get_state"})
        self._send(proc, {"id": "3", "type": "prompt", "message": prompt})

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RPCTimeoutError(f"omp RPC timed out after {self.timeout}s")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                raise RPCTimeoutError(f"omp RPC timed out after {self.timeout}s")
            if line is None:
                raise ChatClientError("omp RPC closed without completing")
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            ftype = frame.get("type")
            if ftype == "response":
                if not frame.get("success", True):
                    raise ChatClientError(
                        f"{frame.get('command')} failed: {frame.get('error')}"
                    )
                if frame.get("command") == "get_state":
                    exposed = frame.get("data", {}).get("dumpTools", [])
                    if exposed:
                        raise ChatClientError(
                            f"preflight failed: session exposes tools {exposed}"
                        )
                    self._preflight_done = True
            elif ftype == "agent_end" and frame.get("isTerminal", True):
                text = self._assistant_text(frame.get("messages", []))
                if text is None:
                    raise EmptyResponseError("agent ended with no assistant text")
                resp = parse_response_text(text)
                # Preserve any optional telemetry fields the runtime emits
                # (omp rpc.md: agent_end may carry them; names undocumented)
                # so telemetry can record provider usage if it ever appears.
                extra = {
                    k: v for k, v in frame.items() if k not in ("type", "messages")
                }
                return ChatResponse(
                    content=resp.content,
                    tool_calls=resp.tool_calls,
                    raw={"agent_end": extra},
                )

    @staticmethod
    def _assistant_text(messages: list[dict]) -> str | None:
        for m in reversed(messages):
            if m.get("role") != "assistant":
                continue
            content = m.get("content")
            if isinstance(content, list):
                text = "".join(
                    c.get("text", "") for c in content if c.get("type") == "text"
                )
            else:
                text = str(content or "")
            if text.strip():
                return text
        return None
