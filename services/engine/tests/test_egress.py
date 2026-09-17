"""Guarded egress invariants against the local lab (needs httpx; uv env)."""
import pytest

httpx = pytest.importorskip("httpx")
anyio = pytest.importorskip("anyio")

from scopeforge_engine.egress import Egress
from scopeforge_engine.labs import LabManager, find_labs_dir
from scopeforge_engine.policy import (
    Action,
    ApprovalLedger,
    AssetRule,
    BudgetTracker,
    CapabilityMint,
    Policy,
)

LABS = find_labs_dir()
BASE_HEADERS = {"X-Actor": "account-a"}


def lab_policy(port, **kw):
    doc = {"mode": "live", "policy_source": "test", "include": [
        {"host": "127.0.0.1", "port": port, "allow_private": True}],
        "methods_allow": ["GET", "HEAD", "POST"],
        "budgets": {"requests_total": 50, "max_concurrency": 1},
        "required_headers": dict(BASE_HEADERS)}
    doc.update(kw)
    return Policy.load(doc)


@pytest.fixture()
def lab():
    mgr = LabManager()
    info = mgr.launch("tenancy-demo", LABS)
    yield info["url"]
    mgr.stop_all()


def rig(policy):
    budgets, approvals, mint = BudgetTracker(), ApprovalLedger(), CapabilityMint()
    return Egress(budgets=budgets, approvals=approvals, mint=mint), budgets, approvals


def get(url, **kw):
    base = dict(kind="http.request", method="GET", url=url, impact="R3",
                actor_ref="account-a", fixture_owned=True)
    base.update(kw)
    return Action(**base)


async def test_target_and_evidence_chain(lab):
    port = int(lab.rsplit(":", 1)[1])
    egress, budgets, _ = rig(lab_policy(port))
    try:
        denied = await egress.execute(
            get(f"{lab}/export?project=proj_A", actor_ref="account-b"), lab_policy(port))
        assert denied.sent and denied.status == 403  # LAB denies; gate allowed
        assert len(denied.chain) == 1 and budgets.requests_used == 1
        out = await egress.execute(
            get("https://example.invalid/x"), lab_policy(port))
        assert out.sent is False and "not in included scope" in " ".join(out.reasons)
        assert all(a["url"].startswith(lab) for a in egress.attempt_log)  # no packet out
    finally:
        await egress.client.aclose() if egress.client else None


async def test_redirect_in_scope_followed_out_of_scope_denied(lab):
    port = int(lab.rsplit(":", 1)[1])
    egress, _, _ = rig(lab_policy(port))
    try:
        following = await egress.execute(get(f"{lab}/go-in"), lab_policy(port))
        assert following.status == 200 and following.requests_made == 2
        assert len(following.chain) == 2
        blocked = await egress.execute(get(f"{lab}/go-out"), lab_policy(port))
        assert blocked.sent and blocked.allowed is False
        assert "redirect hop denied" in " ".join(blocked.reasons)
        assert all(a["url"].startswith(lab) for a in egress.attempt_log)
    finally:
        await egress.client.aclose() if egress.client else None


async def test_auth_stripped_cross_origin_only(lab):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    lab2 = LabManager()
    try:
        second = lab2.launch("tenancy-demo", LABS)["url"]
        port = int(lab.rsplit(":", 1)[1])

        class Redirector(BaseHTTPRequestHandler):
            def do_GET(self):
                target = f"{second}/echo-headers".encode()
                self.send_response(302)
                self.send_header("Location", target.decode())
                self.end_headers()

            def log_message(self, *args):
                pass

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        hop_port = proxy.server_address[1]
        policy = lab_policy(port)
        policy.include.append(AssetRule.parse({"host": "127.0.0.1", "port": hop_port,
                                               "allow_private": True}))
        egress, _, _ = rig(policy)
        try:
            lab2_port = int(second.rsplit(":", 1)[1])
            headers_policy = Policy.load({
                "mode": "live", "policy_source": "t",
                "include": [{"host": "127.0.0.1", "port": port, "allow_private": True},
                            {"host": "127.0.0.1", "port": hop_port, "allow_private": True},
                            {"host": "127.0.0.1", "port": lab2_port, "allow_private": True}],
                "methods_allow": ["GET", "HEAD"],
                "budgets": {"requests_total": 50},
                "required_headers": {"Authorization": "Bearer lab-token",
                                     "X-Actor": "account-a"}})
            out = await egress.execute(
                get(f"http://127.0.0.1:{hop_port}/start"), headers_policy)
            assert out.status == 200
            import json as _json
            echoed = _json.loads(out.body_preview)
            assert echoed["authorization_present"] is False  # stripped cross-origin
            assert echoed["actor"] == "account-a"  # non-secret headers preserved
            same = await egress.execute(get(f"{second}/echo-headers"), headers_policy)
            assert _json.loads(same.body_preview)["authorization_present"] is True
        finally:
            await egress.client.aclose() if egress.client else None
            proxy.shutdown()
    finally:
        lab2.stop_all()


async def test_rate_stop_and_no_idempotent_retry(lab):
    port = int(lab.rsplit(":", 1)[1])
    policy = lab_policy(port, budgets={"requests_total": 50, "per_host_rps": 1,
                                       "per_host_burst": 1})
    egress, budgets, _ = rig(policy)
    try:
        first = await egress.execute(get(f"{lab}/export?project=proj_A"), policy)
        assert first.status == 200
        limited = await egress.execute(get(f"{lab}/export?project=proj_A"), policy)
        assert limited.sent is False and "rate limit" in " ".join(limited.reasons)
        budgets.stop("user stop word matched")
        assert "stop signal" in " ".join(
            (await egress.execute(get(f"{lab}/export?project=proj_A"), policy)).reasons)
    finally:
        await egress.client.aclose() if egress.client else None

    policy2 = lab_policy(port, methods_allow=["GET", "HEAD", "POST"])
    egress2, _, approvals = rig(policy2)
    flaky = Action(kind="http.request", method="POST",
                   url=f"{lab}/flaky-post", impact="R4",
                   actor_ref="account-a", fixture_owned=True)
    try:
        preview = await egress2.execute(flaky, policy2)
        assert preview.approval_required is True and not preview.sent
        assert len(egress2.attempt_log) == 0
        approvals.grant("http.request:POST:" + flaky.url, "one-shot")
        first_post = await egress2.execute(flaky, policy2)
        assert first_post.status == 500 and len(egress2.attempt_log) == 1  # no retry
        second = await egress2.execute(flaky, policy2)
        assert second.approval_required is True  # one-shot was consumed
        approvals.grant("http.request:POST:" + flaky.url, "one-shot")
        assert (await egress2.execute(flaky, policy2)).status == 200
    finally:
        await egress2.client.aclose() if egress2.client else None


async def test_send_once_retry_rules():
    from scopeforge_engine.egress import Egress as E

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        status = 500 if calls["n"] == 1 else 200
        return httpx.Response(status, content=b"{}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        egress = E(budgets=BudgetTracker(), approvals=ApprovalLedger(),
                   mint=CapabilityMint(), client=client)
        ok = await egress._send_once(client, "GET", "http://x/", {}, b"")
        assert ok["status"] == 200 and calls["n"] == 2  # idempotent retried
        calls["n"] = 0
        bad = await egress._send_once(client, "POST", "http://x/", {}, b"{}")
        assert bad["status"] == 500 and calls["n"] == 1  # never retried


async def test_capability_refused_before_io(lab):
    port = int(lab.rsplit(":", 1)[1])
    egress, _, _ = rig(lab_policy(port))
    try:
        out = await egress._send_with_capability(
            get(f"{lab}/export?project=proj_A"), lab_policy(port), "garbage.token")
        assert out.sent is False and "capability refused" in " ".join(out.reasons)
        assert egress.attempt_log == []
    finally:
        await egress.client.aclose() if egress.client else None
