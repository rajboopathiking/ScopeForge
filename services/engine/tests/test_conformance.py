"""Golden-turn conformance (Phase 3 exit criterion 1).

The SAME agent turn — text + tool call, then strict-JSON verdict — passes
against two hosted API shapes (OpenAI-compatible, Anthropic-compatible,
both via sanitized replay) and the local mock. No network, no credentials.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

from scopeforge_engine.models import (
    AnthropicCompatAdapter,
    CanonicalRequest,
    DeepSeekAdapter,
    Message,
    MockAdapter,
    OpenAICompatAdapter,
    ResponseFormat,
    TextBlock,
    ToolCallRequest,
    ToolSpec,
)
from scopeforge_engine.models.errors import CapabilityUnsupported
from scopeforge_engine.models.replay import ReplayTransport, load_exchanges, scan_fixture_file
from scopeforge_engine.models.sanitize import assert_no_secrets

FIX = Path(__file__).resolve().parents[3] / "packages" / "provider-fixtures"

TOOL = ToolSpec(
    name="get_export",
    description="Read a researcher-owned project export fixture.",
    parameters={"type": "object",
                "properties": {"project": {"type": "string"},
                               "as_account": {"type": "string"}},
                "required": ["project", "as_account"]},
)
STRICT_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string"}, "rule": {"type": "string"},
                   "evidence": {"type": "array", "items": {"type": "string"}}},
    "required": ["verdict", "rule", "evidence"],
}
USER_TEXT = "Check whether account-b can export proj_A. Fixtures only, no live traffic."
TOOL_RESULT = '{"status":403,"body":{"error":"forbidden"}}'


def turn1(model: str) -> CanonicalRequest:
    return CanonicalRequest(
        model=model,
        messages=[Message(role="user", content=[TextBlock(USER_TEXT)])],
        tools=[TOOL],
    )


def turn2(model: str, tool_id: str, tool_name: str, as_args) -> CanonicalRequest:
    prior = [
        Message(role="user", content=[TextBlock(USER_TEXT)]),
        Message(role="assistant", content=[TextBlock("I'll check the export boundary.")],
                tool_calls=[ToolCallRequest(id=tool_id, name=tool_name, arguments=as_args)]),
        Message(role="tool", content=[TextBlock(TOOL_RESULT)],
                tool_call_id=tool_id, name=tool_name),
    ]
    return CanonicalRequest(
        model=model, messages=prior, tools=[TOOL],
        response_format=ResponseFormat(kind="json_schema", schema=STRICT_SCHEMA, strict=True),
    )


async def collect(stream):
    return [e async for e in stream]


def test_fixtures_are_secret_free():
    for name in ("golden-turn.openai.jsonl", "golden-turn.anthropic.jsonl",
                 "golden-turn.mock.json", "malformed-args.openai.jsonl"):
        scan_fixture_file(FIX / name)


async def test_openai_shape_golden_turn():
    replay = ReplayTransport(load_exchanges(FIX / "golden-turn.openai.jsonl"))
    adapter = DeepSeekAdapter(credential=None, transport_override=replay)
    try:
        t1 = await collect(adapter.stream(turn1("deepseek-chat")))
        assert "export boundary" in "".join(e.text for e in t1 if e.kind == "text.delta")
        done = [e for e in t1 if e.kind == "tool.done"]
        assert len(done) == 1 and done[0].tool_name == "get_export"
        assert done[0].tool_arguments == {"project": "proj_A", "as_account": "account-b"}
        assert not [e for e in t1 if e.kind == "error"]
        assert any(e.kind == "usage" and e.input_tokens > 0 for e in t1)

        t2 = await collect(adapter.stream(
            turn2("deepseek-chat", done[0].tool_id, "get_export", done[0].tool_arguments)))
        finals = [e for e in t2 if e.kind == "done"]
        assert finals and finals[0].structured_via == "native"
        payload = json.loads("".join(e.text for e in t2 if e.kind == "text.delta"))
        assert payload["verdict"] == "no_issue" and payload["evidence"] == ["E-001"]
        assert replay.exhausted
        assert_no_secrets([dataclasses.asdict(e) for e in t1 + t2], where="openai events")
        sent_headers = [dict(r.headers) for r in replay.requests]
        assert all("authorization" not in h for h in sent_headers)  # no credential configured
    finally:
        await adapter.close()


async def test_anthropic_shape_golden_turn():
    replay = ReplayTransport(load_exchanges(FIX / "golden-turn.anthropic.jsonl"))
    adapter = AnthropicCompatAdapter(credential=None, transport_override=replay)
    try:
        t1 = await collect(adapter.stream(turn1("claude-test")))
        assert "export boundary" in "".join(e.text for e in t1 if e.kind == "text.delta")
        done = [e for e in t1 if e.kind == "tool.done"]
        assert len(done) == 1 and done[0].tool_name == "get_export"
        assert done[0].tool_arguments == {"project": "proj_A", "as_account": "account-b"}
        assert not [e for e in t1 if e.kind == "error"]

        t2 = await collect(adapter.stream(
            turn2("claude-test", done[0].tool_id, "get_export", done[0].tool_arguments)))
        forced = [e for e in t2 if e.kind == "tool.done"]
        assert len(forced) == 1 and forced[0].tool_name == "structured_result"
        assert forced[0].tool_arguments["verdict"] == "no_issue"
        finals = [e for e in t2 if e.kind == "done"]
        # No native strict mode here: the forced-tool mechanism is RECORDED, never silent.
        assert finals and finals[0].structured_via == "forced-tool"
        assert replay.exhausted
        assert_no_secrets([dataclasses.asdict(e) for e in t1 + t2], where="anthropic events")
    finally:
        await adapter.close()


async def test_mock_shape_golden_turn():
    import json as _json

    script = [[
        {"kind": "text.delta", "text": "I'll check the export boundary."},
        {"kind": "tool.begin", "tool_id": "call_1", "tool_name": "get_export"},
        {"kind": "tool.done", "tool_id": "call_1", "tool_name": "get_export",
         "tool_arguments": {"project": "proj_A", "as_account": "account-b"}},
        {"kind": "usage", "input_tokens": 120, "output_tokens": 34},
        {"kind": "done", "stop_reason": "tool_calls"},
    ], [
        {"kind": "text.delta",
         "text": _json.dumps({"verdict": "no_issue",
                              "rule": "Only owning-tenant members may export",
                              "evidence": ["E-001"]})},
        {"kind": "usage", "input_tokens": 200, "output_tokens": 40},
        {"kind": "done", "stop_reason": "stop", "structured_via": "native"},
    ]]
    from scopeforge_engine.models.mock import turn as build_turn

    adapter = MockAdapter(script=[build_turn(t) for t in script])
    t1 = await collect(adapter.stream(turn1("mock-turn")))
    assert "export boundary" in "".join(e.text for e in t1 if e.kind == "text.delta")
    done = [e for e in t1 if e.kind == "tool.done"]
    assert done and done[0].tool_arguments["project"] == "proj_A"
    t2 = await collect(adapter.stream(
        turn2("mock-turn", done[0].tool_id, "get_export", done[0].tool_arguments)))
    payload = json.loads("".join(e.text for e in t2 if e.kind == "text.delta"))
    assert payload["verdict"] == "no_issue"
    assert [e for e in t2 if e.kind == "done"][0].structured_via == "native"
    assert len(adapter.requests) == 2


async def test_malformed_tool_arguments_surface_as_error():
    replay = ReplayTransport(load_exchanges(FIX / "malformed-args.openai.jsonl"))
    adapter = OpenAICompatAdapter(base_url="https://api.test.invalid", transport_override=replay)
    try:
        events = await collect(adapter.stream(turn1("m")))
        errors = [e for e in events if e.kind == "error"]
        assert errors and errors[0].tool_name == "get_export"
        assert "malformed tool arguments" in errors[0].error
        assert not [e for e in events if e.kind == "tool.done"]
    finally:
        await adapter.close()


async def test_strict_unsupported_blocks_before_any_io():
    replay = ReplayTransport(load_exchanges(FIX / "golden-turn.openai.jsonl"))
    adapter = OpenAICompatAdapter(base_url="https://api.test.invalid", supports_strict=False,
                                  transport_override=replay)
    try:
        with pytest.raises(CapabilityUnsupported, match="strict structured output"):
            async for _ in adapter.stream(turn2("m", "call_1", "get_export", {})):
                pass
        assert adapter.attempts == 0 and not replay.served
    finally:
        await adapter.close()
