"""Interactive discussion surface: `annotator chat`.

Matt (or a future human reviewer) talks to a Kimi-backed agent that can
read the glossary and story log but CANNOT write — the dispatcher is
constructed with the read-only allowlist, and the tool-call parsing is the
same text convention as the annotator path (OmpRpcClient).

The annotator's per-batch loop is stateless; chat is the opposite: history
accumulates across turns, because conversation is the point.
"""

from __future__ import annotations

import json

from terrarium_annotator.llm import ChatClient
from terrarium_annotator.tools import ToolDispatcher

READONLY_TOOLS = {"fetch_entry", "recall_story", "fetch_post", "search_glossary"}

CHAT_SYSTEM_PROMPT = """You are the terrarium archivist's assistant. You answer questions about the story and the glossary built from it, using the tools available (search_glossary, fetch_entry, recall_story, fetch_post). Ground every claim in what the tools return; quote evidence when it matters. If the glossary and story log don't know something, say so plainly.

To call a tool, emit: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
"""


def chat_turn(
    messages: list[dict],
    client: ChatClient,
    dispatcher: ToolDispatcher,
    max_rounds: int = 6,
) -> str:
    """One user turn: model reply + tool loop. Mutates `messages` in place.
    Returns the final assistant text."""
    response = client.chat(messages, tools=dispatcher.schemas)
    rounds = 0
    while response.tool_calls and rounds < max_rounds:
        rounds += 1
        messages.append(
            {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            "arguments": json.dumps(c.arguments),
                        },
                    }
                    for c in response.tool_calls
                ],
            }
        )
        for call in response.tool_calls:
            result = dispatcher.dispatch(call)
            messages.append(
                {
                    "role": "tool",
                    "name": call.name,
                    "content": result,
                    "tool_call_id": call.id,
                }
            )
        response = client.chat(messages, tools=dispatcher.schemas)
    text = response.content or ""
    messages.append({"role": "assistant", "content": text})
    return text


def repl(client: ChatClient, dispatcher: ToolDispatcher) -> None:
    """Interactive loop. Ctrl-D or empty line exits."""
    messages: list[dict] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    print("terrarium-annotator chat (read-only). Empty line or Ctrl-D to exit.")
    while True:
        try:
            question = input("you> ").strip()
        except EOFError:
            break
        if not question:
            break
        messages.append({"role": "user", "content": question})
        answer = chat_turn(messages, client, dispatcher)
        print(f"archivist> {answer}\n")
