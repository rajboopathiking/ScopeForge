"""Comprehensive test suite for the new ScopeForge LangGraph, Textual, RAG, Wiki, MCP, and A2A suite."""
import json
import pytest
from scopeforge_engine.a2a.bus import A2ABus
from scopeforge_engine.a2a.protocol import A2AIntent, A2AMessage
from scopeforge_engine.agents.custom_agent import CustomAgentConfig, CustomAgentLoader
from scopeforge_engine.agents.graph import MultiAgentSecOpsOrchestrator
from scopeforge_engine.llm_providers.manager import ProviderManager
from scopeforge_engine.llm_providers.models import ProviderType
from scopeforge_engine.middleware.audit import AuditLoggingMiddleware
from scopeforge_engine.middleware.pipeline import MiddlewarePipeline
from scopeforge_engine.middleware.redaction import RedactionMiddleware
from scopeforge_engine.middleware.scope_gate import ScopeGateMiddleware
from scopeforge_engine.rag.engine import LlamaSecRAG
from scopeforge_engine.sec_tools.tools import (
    cve_advisory_search,
    evidence_recorder,
    falsifiable_poc_runner,
    recon_port_scan,
    sast_code_audit,
    web_surface_probe,
)
from scopeforge_engine.wiki.store import SecWiki


def test_provider_manager():
    mgr = ProviderManager()
    providers = mgr.list_providers()
    assert len(providers) >= 6
    assert mgr.set_active_provider("mock-secops")
    assert mgr.active_provider_name == "mock-secops"
    chat_model = mgr.get_chat_model()
    assert chat_model is not None


def test_scopegate_middleware():
    gate = ScopeGateMiddleware(authorized_scopes=["authorized.example"], mode="plan")
    
    # In plan mode, network tools must be rejected
    proceed, reason, _ = gate.before_tool("recon_port_scan", {"target": "authorized.example"}, {})
    assert not proceed
    assert "PLAN" in reason

    # Switch to live mode
    gate.set_mode("live")
    proceed, _, _ = gate.before_tool("recon_port_scan", {"target": "authorized.example"}, {})
    assert proceed

    # Out of scope target must be rejected
    proceed, reason, _ = gate.before_tool("recon_port_scan", {"target": "unauthorized.badsite.com"}, {})
    assert not proceed
    assert "NOT in authorized scope" in reason


def test_redaction_middleware():
    redact = RedactionMiddleware()
    text = "Authorization: Bearer sk-1234567890abcdef1234567890 and secret='super_secret_password'"
    cleaned = redact._redact_text(text)
    assert "sk-12345" not in cleaned
    assert "[REDACTED" in cleaned


def test_sec_tools():
    # Recon tool
    res = recon_port_scan.invoke({"target": "authorized.example", "ports": "80,443"})
    data = json.loads(res)
    assert data["target"] == "authorized.example"

    # SAST tool
    code = "query = f'SELECT * FROM accounts WHERE id = {user_id}'"
    sast_res = sast_code_audit.invoke({"code_snippet_or_file": code})
    sast_data = json.loads(sast_res)
    assert sast_data["findings_count"] > 0
    assert "SQL Injection" in sast_data["findings"][0]["title"]

    # CVE tool
    cve_res = cve_advisory_search.invoke({"query": "CVE-2024-3400"})
    cve_data = json.loads(cve_res)
    assert cve_data["results_count"] > 0

    # PoC tool
    poc_res = falsifiable_poc_runner.invoke({
        "hypothesis": "Header reflection vulnerability",
        "target": "authorized.example",
        "payload_type": "header_reflection",
    })
    poc_data = json.loads(poc_res)
    assert "CONFIRMED" in poc_data["verdict"]


def test_llama_rag():
    rag = LlamaSecRAG()
    hits = rag.query("OWASP Injection SQLi", top_k=2)
    assert len(hits) > 0
    prompt_context = rag.get_context_for_prompt("SSRF vulnerability")
    assert "SSRF" in prompt_context


def test_sec_wiki():
    wiki = SecWiki()
    pages = wiki.list_pages()
    assert "preferences" in pages
    assert "targets" in pages
    prefs = wiki.get_user_preferences()
    assert "Preferences" in prefs


def test_a2a_bus_and_protocol():
    bus = A2ABus()
    received = []
    bus.subscribe(lambda m: received.append(m))

    msg = bus.send(
        sender="ReconAgent",
        recipient="AuditAgent",
        intent=A2AIntent.TASK_DELEGATION,
        payload={"target": "authorized.example"},
    )
    assert msg.verify()
    assert len(received) == 1
    assert received[0].sender == "ReconAgent"


@pytest.mark.asyncio
async def test_multiagent_orchestrator():
    orchestrator = MultiAgentSecOpsOrchestrator()
    state = await orchestrator.run("Perform a security audit and port scan on authorized.example", mode="live")
    assert len(state["messages"]) >= 2
    assert len(state["a2a_log"]) >= 1
    assert state["active_agent"] == "recon"


@pytest.mark.asyncio
async def test_tui_rendering_and_interaction():
    from scopeforge_engine.tui.app import ScopeForgeTUIApp
    app = ScopeForgeTUIApp()
    async with app.run_test(size=(120, 36)) as pilot:
        # Verify focused on prompt-input
        assert pilot.app.focused is not None
        # Verify header rendered
        header = pilot.app.query_one("#header-bar")
        assert header.region.height == 3
        # Verify chat and sidebar rendered
        chat = pilot.app.query_one("#chat-stream")
        assert chat.region.height > 10
        sidebar = pilot.app.query_one("#sidebar")
        assert sidebar.region.height > 10
        # Verify prompt bar rendered
        prompt = pilot.app.query_one("#prompt-bar")
        assert prompt.region.height == 4
        # Test keyboard cycle F3
        await pilot.press("f3")
        await pilot.pause()
        assert pilot.app.sec_mode == "artifacts"

        # Test slash commands
        pilot.app.handle_user_input("/skill list")
        await pilot.pause()
        pilot.app.handle_user_input("/config")
        await pilot.pause()
        pilot.app.handle_user_input("/cost")
        await pilot.pause()
        pilot.app.handle_user_input("/status")
        await pilot.pause()


def test_skills_manager():
    from scopeforge_engine.skills.manager import SkillManager
    mgr = SkillManager()
    skills = mgr.list_skills()
    assert len(skills) >= 4
    names = [s.name for s in skills]
    assert "subdomain-recon" in names
    assert "api-idor-audit" in names

    # Test auto matching
    matches = mgr.auto_match_skills("Let us test subdomain enumeration and dns")
    assert len(matches) > 0
    assert matches[0].name == "subdomain-recon"

    # Test prompt augmentation
    instructions = mgr.get_prompt_instructions("idor on api")
    assert "BOLA" in instructions or "IDOR" in instructions


def test_mcp_customization(tmp_path):
    from scopeforge_engine.mcp_bridge import MCPBridge
    mcp = MCPBridge(home=tmp_path)
    servers = mcp.list_servers()
    assert len(servers) >= 2

    # Add a new custom MCP server
    mcp.registry.add_server("test-custom-mcp", "python -m custom_server")
    mcp.enable_server("test-custom-mcp")
    enabled = mcp.registry.get_enabled()
    assert any(s.name == "test-custom-mcp" for s in enabled)

    tools = mcp.get_langchain_tools()
    assert len(tools) >= 1

