"""Fake `omp --mode rpc` server for OmpRpcClient tests (L2-style).

Script via env: FAKE_OMP_SCRIPT = path to JSON list of {"text": ...} or
{"error": ...} entries, one per prompt. FAKE_OMP_TOOLS = JSON list for the
get_state dumpTools field. FAKE_OMP_FAIL_SET_MODEL / FAKE_OMP_HANG trigger
failure modes. FAKE_OMP_COUNTER = path to a counter file: because each
OmpRpcClient chat() spawns a fresh process, the counter persists prompt
position across processes (retries see the NEXT script entry).
Emits unsolicited noise frames like the real server.
"""

import json
import os
import sys
import time


def respond(cid, command, data):
    sys.stdout.write(
        json.dumps(
            {
                "id": cid,
                "type": "response",
                "command": command,
                "success": True,
                "data": data,
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def fail(cid, command, error):
    sys.stdout.write(
        json.dumps(
            {
                "id": cid,
                "type": "response",
                "command": command,
                "success": False,
                "error": error,
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def main():
    sys.stdout.write(
        json.dumps(
            {"type": "ready", "protocolVersion": 1, "supportedProtocolVersions": [1, 2]}
        )
        + "\n"
    )
    sys.stdout.flush()
    with open(os.environ["FAKE_OMP_SCRIPT"]) as f:
        script = json.loads(f.read())
    counter_path = os.environ.get("FAKE_OMP_COUNTER")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        cmd = json.loads(line)
        ctype = cmd["type"]
        if ctype == "negotiate_protocol":
            respond(cmd.get("id"), "negotiate_protocol", {"protocolVersion": 2})
        elif ctype == "set_model":
            if os.environ.get("FAKE_OMP_FAIL_SET_MODEL"):
                fail(cmd.get("id"), "set_model", "no such model")
            else:
                respond(cmd.get("id"), "set_model", {})
        elif ctype == "get_state":
            tools = json.loads(os.environ.get("FAKE_OMP_TOOLS", "[]"))
            respond(cmd.get("id"), "get_state", {"dumpTools": tools})
        elif ctype == "prompt":
            if os.environ.get("FAKE_OMP_HANG"):
                time.sleep(60)
                continue
            calls = 0
            if counter_path and os.path.exists(counter_path):
                with open(counter_path) as cf:
                    calls = int(cf.read().strip() or "0")
            entry = script[min(calls, len(script) - 1)]
            if counter_path:
                with open(counter_path, "w") as cf:
                    cf.write(str(calls + 1))
            # Unsolicited frames, as the real server emits.
            sys.stdout.write(json.dumps({"type": "agent_start"}) + "\n")
            sys.stdout.write(
                json.dumps(
                    {
                        "type": "extension_ui_request",
                        "id": "noise",
                        "method": "notify",
                        "message": "noise",
                    }
                )
                + "\n"
            )
            if "error" in entry:
                fail(cmd.get("id"), "prompt", entry["error"])
            else:
                sys.stdout.write(
                    json.dumps(
                        {
                            "type": "agent_end",
                            "isTerminal": True,
                            "messages": [
                                {
                                    "role": "assistant",
                                    "content": [
                                        {"type": "text", "text": entry["text"]}
                                    ],
                                }
                            ],
                        }
                    )
                    + "\n"
                )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
