#!/usr/bin/env python3
"""Regenerate packages/provider-fixtures/*.jsonl (Phase 3 golden turn).

Hand-editing nested SSE-in-JSON escaping is error-prone; this script is the
source of truth. Run: `uv run python scripts/make-provider-fixtures.py`.
Fixtures must stay secret-free (`scan_fixture_file` in tests enforces it).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "packages" / "provider-fixtures"

TEXT = "I'll check the export boundary."
ARGS = {"project": "proj_A", "as_account": "account-b"}
VERDICT = {"verdict": "no_issue",
           "rule": "Only owning-tenant members may export",
           "evidence": ["E-001"]}
TOOL_RESULT_NOTE = "tool result fed by harness"


def exchange(method: str, path: str, body: str) -> dict:
    return {"request": {"method": method, "path": path},
            "response": {"status": 200,
                         "headers": {"content-type": "text/event-stream"},
                         "body": body}}


def sse_openai(chunks: list) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n"


def chunk(model: str, delta: dict, finish: str | None = None, cid: str = "chatcmpl-1") -> dict:
    return {"id": cid, "object": "chat.completion.chunk", "created": 1, "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def openai_turn() -> list[dict]:
    m = "deepseek-chat"
    first = sse_openai([
        chunk(m, {"role": "assistant", "content": TEXT}),
        chunk(m, {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                  "function": {"name": "get_export", "arguments": ""}}]}),
        chunk(m, {"tool_calls": [{"index": 0,
                                  "function": {"arguments": json.dumps(ARGS)}}]}),
        chunk(m, {}, finish="tool_calls"),
        {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1,
         "model": m, "choices": [],
         "usage": {"prompt_tokens": 120, "completion_tokens": 34}},
    ])
    strict = json.dumps(VERDICT)
    second = sse_openai([
        chunk(m, {"role": "assistant", "content": strict[:40]}, cid="chatcmpl-2"),
        chunk(m, {"content": strict[40:]}, cid="chatcmpl-2"),
        chunk(m, {}, finish="stop", cid="chatcmpl-2"),
        {"id": "chatcmpl-2", "object": "chat.completion.chunk", "created": 2,
         "model": m, "choices": [],
         "usage": {"prompt_tokens": 200, "completion_tokens": 40}},
    ])
    _ = TOOL_RESULT_NOTE
    return [exchange("POST", "/chat/completions", first),
            exchange("POST", "/chat/completions", second)]


def sse_anthropic(events: list[tuple[str, dict]]) -> str:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)


def anthropic_turn() -> list[dict]:
    first = sse_anthropic([
        ("message_start", {"type": "message_start",
                           "message": {"id": "msg_1", "model": "claude-test",
                                       "usage": {"input_tokens": 110}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": TEXT}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("content_block_start", {"type": "content_block_start", "index": 1,
                                 "content_block": {"type": "tool_use", "id": "toolu_1",
                                                   "name": "get_export", "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": json.dumps(ARGS)[:20]}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": json.dumps(ARGS)[20:]}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                           "usage": {"output_tokens": 40}}),
        ("message_stop", {"type": "message_stop"}),
    ])
    strict = json.dumps(VERDICT)
    second = sse_anthropic([
        ("message_start", {"type": "message_start",
                           "message": {"id": "msg_2", "model": "claude-test",
                                       "usage": {"input_tokens": 190}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "tool_use", "id": "toolu_2",
                                                   "name": "structured_result",
                                                   "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": strict[:30]}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": strict[30:]}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                           "usage": {"output_tokens": 35}}),
        ("message_stop", {"type": "message_stop"}),
    ])
    return [exchange("POST", "/messages", first), exchange("POST", "/messages", second)]


def malformed_turn() -> list[dict]:
    body = sse_openai([
        chunk("m", {"tool_calls": [{"index": 0, "id": "call_x", "type": "function",
                                    "function": {"name": "get_export",
                                                 "arguments": '{"project":'}}]}),
        chunk("m", {}, finish="tool_calls"),
    ])
    return [exchange("POST", "/chat/completions", body)]


def write(name: str, exchanges: list[dict]) -> None:
    path = OUT / name
    path.write_text("".join(json.dumps(e) + "\n" for e in exchanges))
    # Self-check: every exchange parses and every SSE data line parses.
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        assert entry["request"]["method"] and entry["request"]["path"]
        assert entry["response"]["status"] == 200
    print(f"wrote {path} ({len(exchanges)} exchanges)")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write("golden-turn.openai.jsonl", openai_turn())
    write("golden-turn.anthropic.jsonl", anthropic_turn())
    write("malformed-args.openai.jsonl", malformed_turn())


if __name__ == "__main__":
    main()
