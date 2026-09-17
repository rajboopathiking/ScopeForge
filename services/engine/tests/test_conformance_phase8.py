"""Phase 8 conformance: native adapters (Gemini, Ollama), presets, LiteLLM bridge scaffolding.

Documents feature differences across all adapters (exit criterion) via
probing + golden-turn replay where applicable.
"""
import json
import pytest

pytest.importorskip("httpx")
pytest.importorskip("anyio")

from scopeforge_engine.models import GeminiAdapter, OllamaAdapter
from scopeforge_engine.models.errors import CapabilityUnsupported
from scopeforge_engine.models.presets import PRESETS, build_preset_adapter, get_preset, preset_names
from scopeforge_engine.models.replay import Exchange, ReplayTransport
from scopeforge_engine.models.types import CanonicalRequest, Message, TextBlock


def test_adapter_capability_matrix_documents_differences():
    """Exit criterion: adapter conformance suite documents feature differences."""
    # Drive probe on each declared provider type and collect caps
    import asyncio
    from scopeforge_engine.providers import PROVIDER_TYPES

    # Expected differences per spec
    assert PROVIDER_TYPES["gemini"]["capabilities"]["strict_structured_output"] is False
    assert PROVIDER_TYPES["gemini"]["capabilities"]["structured_via_tool"] is True
    assert PROVIDER_TYPES["ollama"]["capabilities"]["strict_structured_output"] is False
    assert PROVIDER_TYPES["ollama"]["transport"] == "local"
    assert PROVIDER_TYPES["openai-compat"]["capabilities"]["strict_structured_output"] is True
    assert PROVIDER_TYPES["deepseek"]["capabilities"]["strict_structured_output"] is True
    # All adapters report prompt_caching/parallel etc differently — probe validates
    async def probe_all():
        for name in ("gemini", "ollama", "openai-compat", "deepseek"):
            # Use factory to ensure wiring is identical to production
            from scopeforge_engine.models.factory import build_adapter
            base = "https://example.invalid"
            if name in ("ollama",):
                base = "http://localhost:11434"
            try:
                adapter = build_adapter(name, base_url=base)
            except CapabilityUnsupported:
                continue
            caps = await adapter.probe("test-model")
            assert caps.adapter is not None
            await adapter.close()
    import anyio
    anyio.run(probe_all)
    # Presets that are not yet native are flagged unavailable
    assert get_preset("bedrock")["available"] is False
    assert "vllm" in preset_names()
    assert get_preset("nope") is None


async def test_gemini_adapter_sse_and_strict_via_tool():
    # Minimal Gemini SSE body: two text parts + functionCall
    body = json.dumps({
        "candidates": [{
            "content": {"parts": [{"text": "I'll check."}, {"functionCall": {"name": "get_export", "args": {"project": "proj_A"}}}]},
            "finishReason": "STOP"
        }],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}
    })
    # Gemini streams as line-delimited JSON objects
    sse = body + "\n"
    replay = ReplayTransport([Exchange("POST", "/models/gemini-2.0-flash:streamGenerateContent", None, 200,
                                      {"content-type": "application/json"}, sse)])
    adapter = GeminiAdapter(credential=None, base_url="https://generativelanguage.googleapis.com",
                            transport_override=replay)
    try:
        req = CanonicalRequest(model="gemini-2.0-flash",
                               messages=[Message(role="user", content=[TextBlock("hi")])])
        events = [e async for e in adapter.stream(req)]
        assert any(e.kind == "text.delta" and "I'll check" in e.text for e in events)
        assert any(e.kind == "tool.done" and e.tool_name == "get_export" for e in events)
        assert any(e.kind == "usage" and e.input_tokens == 10 for e in events)
    finally:
        await adapter.close()


async def test_gemini_strict_blocked_before_io():
    adapter = GeminiAdapter(credential=None, transport_override=ReplayTransport([]))
    try:
        from scopeforge_engine.models.types import ResponseFormat
        req = CanonicalRequest(model="gemini-2.0-flash",
                               messages=[Message(role="user", content=[TextBlock("hi")])],
                               response_format=ResponseFormat(kind="json_schema", schema={"type": "object"}, strict=True))
        with pytest.raises(CapabilityUnsupported, match="strict JSON is via forced"):
            async for _ in adapter.stream(req):
                pass
        assert adapter.attempts == 0
    finally:
        await adapter.close()


async def test_ollama_adapter_ndjson_and_strict_blocked():
    line1 = json.dumps({"message": {"role": "assistant", "content": "hello "}, "done": False})
    line2 = json.dumps({"message": {"role": "assistant", "content": "world", "tool_calls": [
        {"id": "call_1", "function": {"name": "get_export", "arguments": {"project": "proj_A"}}}]},
        "done": False})
    line3 = json.dumps({"done": True, "prompt_eval_count": 5, "eval_count": 7})
    ndjson = "\n".join([line1, line2, line3]) + "\n"
    replay = ReplayTransport([Exchange("POST", "/api/chat", None, 200,
                                      {"content-type": "application/x-ndjson"}, ndjson)])
    adapter = OllamaAdapter(base_url="http://localhost:11434", transport_override=replay)
    try:
        req = CanonicalRequest(model="llama3", messages=[Message(role="user", content=[TextBlock("hi")])])
        events = [e async for e in adapter.stream(req)]
        assert "".join(e.text for e in events if e.kind == "text.delta") == "hello world"
        assert any(e.kind == "tool.done" for e in events)
        assert any(e.kind == "usage" and e.input_tokens == 5 for e in events)
    finally:
        await adapter.close()

    replay2 = ReplayTransport([])
    adapter2 = OllamaAdapter(transport_override=replay2)
    try:
        from scopeforge_engine.models.types import ResponseFormat
        req2 = CanonicalRequest(model="llama3",
                                messages=[Message(role="user", content=[TextBlock("hi")])],
                                response_format=ResponseFormat(kind="json_schema", schema={"type": "object"}, strict=True))
        with pytest.raises(CapabilityUnsupported):
            async for _ in adapter2.stream(req2):
                pass
    finally:
        await adapter2.close()


def test_presets_build_and_reject_unavailable():
    # Available presets produce an adapter
    adapter = build_preset_adapter("vllm", base_url="http://localhost:8000/v1")
    assert adapter.provider_name == "vllm"
    # OpenRouter preset uses Bearer
    adapter2 = build_preset_adapter("openrouter", base_url="https://openrouter.ai/api/v1")
    assert adapter2.provider_name == "openrouter"
    # Unavailable presets raise visibly
    with pytest.raises(CapabilityUnsupported, match="requires a native adapter"):
        build_preset_adapter("bedrock")
    with pytest.raises(CapabilityUnsupported, match="unknown preset"):
        build_preset_adapter("nope")


def test_litellm_bridge_available_or_graceful():
    try:
        import litellm  # noqa: F401
        has_litellm = True
    except ImportError:
        has_litellm = False
    from scopeforge_engine.models.factory import build_adapter
    if has_litellm:
        adapter = build_adapter("litellm", base_url="openai/gpt-4o-mini")
        assert adapter.adapter_name == "litellm-bridge"
    else:
        # Construction succeeds (bridge is lazy), but stream would raise CapabilityUnsupported at init if litellm missing
        # Our factory still builds; the adapter's __init__ checks litellm
        with pytest.raises(CapabilityUnsupported, match="LiteLLM bridge not installed"):
            build_adapter("litellm", base_url="openai/gpt-4o-mini")
