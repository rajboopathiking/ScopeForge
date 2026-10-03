"""Property tests: URL canonicalization + scope matching (Phase 5 exit corpus).

Skipped on bare interpreters (hypothesis is a uv dev dependency).
"""
import pytest

hypothesis = pytest.importorskip("hypothesis")
st = pytest.importorskip("hypothesis.strategies")

from scopeforge_engine.policy import AssetRule, canonicalize_url  # noqa: E402


@hypothesis.given(st.from_regex(r"[a-z0-9]([a-z0-9-]{0,18}[a-z0-9])?", fullmatch=True))
def test_host_case_and_dots_normalize(label):
    host = f"{label}.Example.COM."
    try:
        t = canonicalize_url(f"https://{host}/x")
    except ValueError:
        return  # odd labels may reject; must never crash
    assert t.host == t.host.lower() and not t.host.endswith(".")
    assert t.port == 443 and t.path == "/x"


@hypothesis.given(st.text(alphabet="/.abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=40))
def test_paths_never_escape(path):
    try:
        t = canonicalize_url(f"https://h.example/{path}")
    except ValueError:
        return
    assert ".." not in t.path.split("/") and t.path.startswith("/")


@hypothesis.given(
    sub=st.from_regex(r"[a-z0-9-]{1,15}", fullmatch=True),
    path=st.sampled_from(["/", "/api", "/api/x", "/apix", "/other"]),
)
def test_subdomain_suffix_rules_are_total(sub, path):
    rule = AssetRule.parse({"host": "*.example.com", "path_prefix": "/api"})
    t = canonicalize_url(f"https://{sub}.example.com{path}")
    assert isinstance(rule.matches(t), bool)
    assert rule.matches(t) == (
        (path == "/api" or path.startswith("/api/")) and sub != "")


@hypothesis.given(
    second=st.integers(min_value=0, max_value=255),
    third=st.integers(min_value=0, max_value=255),
)
def test_cidr_rules_pin_subnets(second, third):
    rule = AssetRule.parse({"host": "10.1.0.0/16"})
    inside = rule.matches(canonicalize_url(f"https://10.{second}.{third}.7/"))
    assert inside is (second == 1)  # only 10.1.x.x is inside
