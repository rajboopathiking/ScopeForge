"""Local lab lifecycle + semantics (stdlib-only; runs on bare interpreters)."""
import urllib.request
import urllib.error
import json

from scopeforge_engine.labs import LabManager, find_labs_dir, list_labs

LABS = find_labs_dir()


def test_list_and_lifecycle():
    labs = list_labs(LABS)
    assert any(l["name"] == "tenancy-demo" for l in labs)
    mgr = LabManager()
    try:
        info = mgr.launch("tenancy-demo", LABS)
        assert info["url"].startswith("http://127.0.0.1:")
        assert info["manifest"]["name"] == "tenancy-demo"
        try:
            mgr.launch("tenancy-demo", LABS)
            raise AssertionError("double launch should fail")
        except Exception:
            pass
        assert mgr.reset(info["url"]) == {"reset": True}
    finally:
        assert mgr.stop("tenancy-demo") is True
        assert mgr.stop("tenancy-demo") is False


def get(url, actor, path="/export?project=proj_A"):
    req = urllib.request.Request(f"{url}{path}", headers={"X-Actor": actor})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def post(url, actor, path, body):
    req = urllib.request.Request(
        f"{url}{path}", data=json.dumps(body).encode(),
        headers={"X-Actor": actor, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_tenancy_semantics_and_reset():
    mgr = LabManager()
    try:
        url = mgr.launch("tenancy-demo", LABS)["url"]
        status, body = get(url, "account-a")
        assert status == 200 and body["files"] == ["plan.pdf"]
        status, body = get(url, "account-b")
        assert status == 403 and body == {"error": "forbidden", "code": "NOT_MEMBER"}
        assert post(url, "account-b", "/export", {"project": "proj_A"})[0] == 403
        status, job = post(url, "account-a", "/export", {"project": "proj_A"})
        assert status == 201 and job["job"] == "job_1"
        assert post(url, "account-a", "/flaky-post", {})[0] == 500
        assert post(url, "account-a", "/flaky-post", {})[1] == {"n": 2}
        mgr.reset(url)
        status, job = post(url, "account-a", "/export", {"project": "proj_A"})
        assert job == {"job": "job_1"}  # fixtures reset: numbering restarts
    finally:
        mgr.stop_all()
