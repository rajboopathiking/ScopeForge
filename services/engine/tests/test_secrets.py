"""Secrets + sanitization (Phase 3 exit criterion 4).

Credential VALUES must never appear in events, logs, crashes, or fixtures.
Only references (`env:VAR`) and presence labels (`set`/`missing`) are visible.
"""
import json
import sys
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

from scopeforge_engine.models.errors import CredentialMissing
from scopeforge_engine.models.replay import Exchange, ReplayTransport, scan_fixture_file
from scopeforge_engine.models.sanitize import (
    assert_no_secrets,
    find_secret_rule,
    sanitize_headers,
    sanitize_json,
    sanitize_text,
)
from scopeforge_engine.models.secrets import parse_credential_reference, resolve

ROOT = Path(__file__).resolve().parents[3]


def test_env_resolution_and_missing_names_var_only(monkeypatch):
    monkeypatch.setenv("SF_TEST_KEY", "sk-test-should-never-appear-0123456789abcd")
    ref = parse_credential_reference("env:SF_TEST_KEY")
    assert resolve(ref) == "sk-test-should-never-appear-0123456789abcd"
    assert "SF_TEST_KEY" in ref.describe() and "should-never-appear" not in ref.describe()
    monkeypatch.delenv("SF_TEST_KEY")
    with pytest.raises(CredentialMissing) as exc:
        resolve(ref, provider="p")
    assert "SF_TEST_KEY" in str(exc.value)
    assert "should-never-appear" not in str(exc.value)


def test_malformed_references_rejected():
    for bad in ("", "nope", "vault:abc", "env:"):
        with pytest.raises(CredentialMissing):
            parse_credential_reference(bad)


def test_cmd_reference_portable_and_hides_stderr(monkeypatch):
    ref = parse_credential_reference(f"cmd:{sys.executable} -c \"print('cmd-secret-9')\"")
    assert resolve(ref) == "cmd-secret-9"
    ref2 = parse_credential_reference(
        f"cmd:{sys.executable} -c \"import sys; sys.stderr.write('LEAKVALUE'); sys.exit(1)\"")
    with pytest.raises(CredentialMissing) as exc:
        resolve(ref2)
    assert "LEAKVALUE" not in str(exc.value)


def test_keychain_without_backend_gives_guidance(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)
    with pytest.raises(CredentialMissing, match="keyring backend not installed"):
        resolve(parse_credential_reference("keychain:svc/acct"))


def test_auth_failure_carries_no_secret(monkeypatch):
    monkeypatch.setenv("SF_TEST_LIVE", "sk-test-FAKEVALUE-0123456789abcdefghij")
    import anyio

    from scopeforge_engine.models import CanonicalRequest, OpenAICompatAdapter
    from scopeforge_engine.models.secrets import parse_credential_reference as pcr

    replay = ReplayTransport(
        [Exchange("POST", "/chat/completions", None, 401, {}, '{"error":"bad key"}')])
    adapter = OpenAICompatAdapter(base_url="https://x.test",
                                  credential=pcr("env:SF_TEST_LIVE"),
                                  transport_override=replay)

    async def go():
        async for _ in adapter.stream(CanonicalRequest(model="m", messages=[])):
            pass

    try:
        with pytest.raises(Exception) as exc:
            anyio.run(go)
        assert "FAKEVALUE" not in str(exc.value)
        sent = replay.requests[0].headers
        assert sent.get("authorization", "").startswith("Bearer sk-test-FAKEVALUE")
    finally:
        anyio.run(adapter.close)


def test_secrets_fixture_detected_and_sanitized():
    text = (ROOT / "tests" / "fixtures" / "secrets.txt").read_text()
    lines = text.splitlines()
    pem = {i for i, line in enumerate(lines) if "PRIVATE KEY" in line or "SEEDPRIVATEKEY" in line}
    for i, line in enumerate(lines):
        if not line.strip() or i in pem:
            continue
        assert find_secret_rule(line), f"missed: {line[:30]}"
    clean = sanitize_text(text)
    assert_no_secrets(clean, where="sanitized fixture")
    assert "SEEDPRIVATEKEY" not in clean


def test_sanitize_headers_and_json():
    headers = sanitize_headers({"Authorization": "Bearer abcdef0123456789",
                                "X-Api-Key": "k", "Content-Type": "application/json",
                                "X-Custom-Token": "t"})
    assert headers["Authorization"] == "[redacted]"
    assert headers["X-Api-Key"] == "[redacted]"
    assert headers["X-Custom-Token"] == "[redacted]"
    assert headers["Content-Type"] == "application/json"
    doc = sanitize_json({"api_key": "whatever", "nested": {"token": "x", "ok": 1},
                         "note": "Bearer abcdef0123456789zz"})
    assert doc["api_key"] == "[redacted]" and doc["nested"]["token"] == "[redacted]"
    assert doc["nested"]["ok"] == 1 and "[redacted]" in doc["note"]
    assert_no_secrets(doc, where="sanitized doc")


def test_all_checked_in_fixtures_are_secret_free():
    for path in (ROOT / "packages" / "provider-fixtures").glob("*.jsonl"):
        scan_fixture_file(path)
    scan_fixture_file(ROOT / "tests" / "contract" / "fixtures" / "envelope.json")
    assert_no_secrets(json.loads((ROOT / "packages" / "protocol-schema" / "protocol.json").read_text()))
