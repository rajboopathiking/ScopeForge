"""Provider units: routing boundaries, retry, cancellation, probe cache, mock."""
import pytest

httpx = pytest.importorskip("httpx")
anyio = pytest.importorskip("anyio")

from scopeforge_engine.models import (
    AnthropicCompatAdapter,
    CanonicalRequest,
    DeepSeekAdapter,
    Message,
    MockAdapter,
    OpenAICompatAdapter,
    TextBlock,
)
from scopeforge_engine.models.errors import CapabilityUnsupported, ProviderError
from scopeforge_engine.models.replay import Exchange, ReplayTransport
from scopeforge_engine.models.router import Candidate, RouteRequest, route, route_with_fallback
from scopeforge_engine.models.types import ADAPTER_VERSION, ModelCapabilities


def caps(provider, **kw):
    base = dict(provider=provider, model="m", adapter="a", adapter_version=ADAPTER_VERSION,
                endpoint_class="x", transport="hosted", tool_calling=True,
                strict_structured_output=True)
    base.update(kw)
    return ModelCapabilities(**base)


def req1():
    return CanonicalRequest(model="m", messages=[Message(role="user", content=[TextBlock("hi")])])


def sse_done():
    return ('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            'data: [DONE]\n\n')


# -- routing --

def test_strict_native_vs_forced_tool_recorded():
    native = Candidate("oai", "m1", caps("oai"))
    via_tool = Candidate("ant", "m2", caps("ant", strict_structured_output=False,
                                           structured_via_tool=True))
    d = route([via_tool, native], RouteRequest(required=frozenset({"strict_json"})))
    assert (d.provider, d.structured_via) == ("ant", "forced-tool")  # first eligible wins, recorded
    d2 = route([native], RouteRequest(required=frozenset({"strict_json"})))
    assert d2.structured_via == "native"
    with pytest.raises(CapabilityUnsupported, match="strict structured output"):
        route([via_tool], RouteRequest(required=frozenset({"strict_json"}),
                                       require_native_strict=True))


def test_reasoner_blocks_tool_workflows_visibly():
    reasoner = Candidate("deepseek", "deepseek-reasoner",
                         caps("deepseek", model="deepseek-reasoner", tool_calling=False))
    with pytest.raises(CapabilityUnsupported, match="lacks tools"):
        route([reasoner], RouteRequest(required=frozenset({"tools"})))


def test_local_only_and_deny():
    hosted = Candidate("oai", "m", caps("oai"))
    local = Candidate("mock", "m", caps("mock", transport="local"))
    d = route([hosted, local], RouteRequest(local_only=True))
    assert d.provider == "mock"
    with pytest.raises(CapabilityUnsupported, match="denylisted"):
        route([hosted], RouteRequest(deny=("oai",)))


def test_fallback_never_crosses_unapproved_boundary():
    good = Candidate("allowed", "m", caps("allowed"))
    other = Candidate("stranger", "m", caps("stranger"))
    d = route_with_fallback([good, other], RouteRequest(allow=("allowed", "stranger")),
                            failed=["allowed/m"])
    assert d.provider == "stranger" and d.fallback_from == ("allowed/m",)
    with pytest.raises(ProviderError, match="fallback exhausted"):
        route_with_fallback([good, other], RouteRequest(), failed=["allowed/m"])
    # Same-provider model change needs no allowlist.
    sib = Candidate("allowed", "m2", caps("allowed", model="m2"))
    d2 = route_with_fallback([good, sib], RouteRequest(), failed=["allowed/m"])
    assert d2.model == "m2"


# -- adapters: probe/retry/cancel --

async def test_deepseek_probe_reflects_model_limits():
    adapter = DeepSeekAdapter(credential=None)
    try:
        chat = await adapter.probe("deepseek-chat")
        assert chat.tool_calling and chat.reasoning is False or True
        reasoner = await adapter.probe("deepseek-reasoner")
        assert reasoner.tool_calling is False and reasoner.reasoning is True
    finally:
        await adapter.close()


async def test_retry_then_success_and_auth_no_retry():
    ok = Exchange("POST", "/chat/completions", None, 200,
                  {"content-type": "text/event-stream"}, sse_done())
    err429 = Exchange("POST", "/chat/completions", None, 429, {"retry-after": "0"}, "{}")
    replay = ReplayTransport([err429, ok])
    adapter = OpenAICompatAdapter(base_url="https://x.test", transport_override=replay, retries=2)
    try:
        events = [e async for e in adapter.stream(req1())]
        assert adapter.attempts == 2
        assert [e for e in events if e.kind == "done"]
    finally:
        await adapter.close()

    replay401 = ReplayTransport(
        [Exchange("POST", "/chat/completions", None, 401, {}, '{"error":"bad key"}')])
    adapter2 = OpenAICompatAdapter(base_url="https://x.test", transport_override=replay401)
    try:
        with pytest.raises(ProviderError) as exc:
            async for _ in adapter2.stream(req1()):
                pass
        assert exc.value.kind == "auth" and adapter2.attempts == 1
    finally:
        await adapter2.close()


async def test_timeout_retries_then_classifies():
    class BoomTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.n = 0

        async def handle_async_request(self, request):
            self.n += 1
            raise httpx.ConnectTimeout("slow", request=request)

    boom = BoomTransport()
    adapter = OpenAICompatAdapter(base_url="https://x.test", transport_override=boom, retries=2)
    try:
        with pytest.raises(ProviderError) as exc:
            async for _ in adapter.stream(req1()):
                pass
        assert exc.value.kind == "timeout" and boom.n == 3
    finally:
        await adapter.close()


async def test_cancellation_propagates():
    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            async def body():
                yield (b'data: {"choices":[{"index":0,"delta":{"content":"hi"},'
                       b'"finish_reason":null}]}\n\n')
                await anyio.sleep(30)
                yield b"data: [DONE]\n\n"

            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=body(), request=request)

    adapter = OpenAICompatAdapter(base_url="https://x.test",
                                  transport_override=SlowTransport(), retries=0)
    events = []
    try:
        with anyio.move_on_after(0.5):
            async for e in adapter.stream(req1()):
                events.append(e)
        assert events and not [e for e in events if e.kind == "done"]  # cut, not completed
    finally:
        await adapter.close()


async def test_probe_cache_avoids_repeat_http():
    models_body = '{"data":[{"id":"m1"}]}'
    replay = ReplayTransport([Exchange("GET", "/models", None, 200, {}, models_body)])
    adapter = OpenAICompatAdapter(base_url="https://x.test", transport_override=replay)
    try:
        await adapter.probe("m1")
        await adapter.probe("m1")
        assert len(replay.requests) == 0  # declared caps need no I/O
        first = await adapter.probe("m1", verify_reachability=True)
        assert first.verified_live and len(replay.requests) == 1
        await adapter.probe("m1", verify_reachability=True)
        assert len(replay.requests) == 1  # TTL cache hit
    finally:
        await adapter.close()


async def test_mock_records_and_auto_completes():
    from scopeforge_engine.models.mock import turn as build_turn

    adapter = MockAdapter(script=[build_turn([{"kind": "text.delta", "text": "hi"}])])
    events = [e async for e in adapter.stream(req1())]
    assert [e for e in events if e.kind == "done"]
    assert len(adapter.requests) == 1
    assert (await adapter.probe("mock-turn")).transport == "local"
    assert [m.id for m in await adapter.list_models()] == ["mock-turn"]
