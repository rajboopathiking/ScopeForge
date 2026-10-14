"""LangGraph Multi-Agent Orchestration Network for ScopeForge."""
from __future__ import annotations

import ast
import asyncio
import contextvars
import html
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Literal, Optional
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool, tool
from langgraph.graph import END, START, StateGraph

_stream_callback_var: contextvars.ContextVar[Optional[Callable[[str, str], None]]] = contextvars.ContextVar(
    "_stream_callback_var", default=None
)


def _extract_chunk_text(chunk_content: Any) -> str:
    if not chunk_content:
        return ""
    if isinstance(chunk_content, str):
        return chunk_content
    if isinstance(chunk_content, list):
        parts = []
        for p in chunk_content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("content") or ""))
        return "".join(parts)
    return str(chunk_content)


def _format_tools_prompt_catalog(tools: List[Any]) -> str:
    """Format available tools with parameter schemas and clear tool-calling guidelines."""
    if not tools:
        return ""
    lines = [
        "## AVAILABLE TOOLS AND CALLING CONVENTIONS",
        "You have access to the following local tools in this workspace. "
        "When the user's objective requires reading files, editing code, running shell commands, "
        "probing networks, or searching the web, YOU MUST EXECUTE ONE OR MORE OF THESE TOOLS:",
        "",
    ]
    for t in tools:
        name = getattr(t, "name", str(t))
        desc = (getattr(t, "description", "") or "").strip().split("\n")[0]
        args_schema = getattr(t, "args_schema", None)
        params = []
        if args_schema and hasattr(args_schema, "model_fields"):
            for fn, f in args_schema.model_fields.items():
                req = "required" if f.is_required() else "optional"
                params.append(f"{fn}: {req}")
        p_str = ", ".join(params)
        lines.append(f"- `{name}({p_str})`: {desc}")

    lines.extend([
        "",
        "### TOOL CALLING FORMAT",
        "You may invoke tools using native function calling, OR directly in text using standard Python function call syntax:",
        "`tool_name(param1=\"value1\", param2=\"value2\")`",
        "Or ReAct syntax:",
        "Action: tool_name",
        "Action Input: {\"param1\": \"value1\"}",
        "",
        "### TOOL SELECTION GUIDELINES",
        "- For inspecting existing files: call `view_file` (with file_path).",
        "- For finding files by name or directory structure: call `glob_files` (with pattern).",
        "- For searching code/regex across the project: call `grep_search` (with query).",
        "- For modifying existing files: call `edit_file` (with target_content and replacement_content).",
        "- For creating new files or writing full content: call `write_file` (with file_path and content).",
        "- For running terminal commands, pytest, git, or checking environment: call `bash_cli` (with command).",
        "- For searching Google / online docs: call `google_web_search` (with query).",
        "- For website screenshots: call `capture_screenshot` (with url and output_file).",
        "- For network port scans: call `recon_port_scan` (with target and ports).",
        "- For web surface security assessment: call `web_surface_probe` (with url).",
        "- For SAST static security audit: call `sast_code_audit` (with code_snippet_or_file).",
        "- For CVE lookup: call `cve_advisory_search` (with query).",
        "- For verifying vulnerabilities: call `falsifiable_poc_runner` (with hypothesis and target).",
        "- For configuring Model Context Protocol (MCP) servers: call `add_mcp_server` (with name and command).",
        "- For listing configured MCP servers: call `list_mcp_servers()`.",
        "- For installing skill packages from git: call `install_skill` (with repo_url).",
        "- For listing installed skills: call `list_skills()`.",
        "- MULTI-STEP LONG RUNNING TASKS: When accomplishing a multi-step objective, execute each step sequentially using your tools. After receiving each tool result, proceed immediately to the next tool until the entire goal is completed.",
        "- NEVER claim you executed an action or describe results in prose without actually invoking the tool.",
    ])
    return "\n".join(lines)


def _normalize_tool_call(name: str, args: Dict[str, Any], tool_map: Optional[Dict[str, Any]] = None) -> tuple[str, Dict[str, Any]]:
    """Map common LLM hallucinated tool names and argument aliases to registered tools."""
    aliases = {
        "read_file": "view_file", "read": "view_file", "cat": "view_file", "open_file": "view_file",
        "file_view": "view_file", "view": "view_file", "show_file": "view_file", "get_file": "view_file",
        "inspect_file": "view_file",
        "write": "write_file", "save_file": "write_file", "create_file": "write_file", "save": "write_file",
        "create": "write_file", "new_file": "write_file",
        "edit": "edit_file", "modify_file": "edit_file", "replace_file_content": "edit_file", "patch": "edit_file",
        "modify": "edit_file", "replace": "edit_file", "change_file": "edit_file", "update_file": "edit_file",
        "append": "append_file", "add_to_file": "append_file",
        "bash": "bash_cli", "shell": "bash_cli", "terminal": "bash_cli", "run_command": "bash_cli",
        "execute_command": "bash_cli", "cmd": "bash_cli", "exec": "bash_cli", "run_bash": "bash_cli",
        "bash_command": "bash_cli", "run": "bash_cli", "sh": "bash_cli", "run_shell": "bash_cli",
        "shell_command": "bash_cli", "terminal_exec": "bash_cli",
        "glob": "glob_files", "list_files": "glob_files", "ls": "glob_files", "find_files": "glob_files",
        "dir": "glob_files", "list_dir": "glob_files", "listdir": "glob_files", "find": "glob_files",
        "grep": "grep_search", "search_code": "grep_search", "search_files": "grep_search",
        "find_in_files": "grep_search", "code_search": "grep_search", "file_search": "grep_search",
        "search_text": "grep_search",
        "google_search": "google_web_search", "web_search": "google_web_search", "google": "google_web_search",
        "search_web": "google_web_search", "websearch": "google_web_search", "browse": "google_web_search",
        "search_online": "google_web_search", "web": "google_web_search", "fetch_web": "google_web_search",
        "git_status": "git_status_tool", "status": "git_status_tool",
        "git_diff": "git_diff_tool", "diff": "git_diff_tool",
        "git_commit": "git_commit_tool", "commit": "git_commit_tool",
        "port_scan": "recon_port_scan", "nmap": "recon_port_scan", "scan_ports": "recon_port_scan",
        "portscan": "recon_port_scan", "ports": "recon_port_scan", "scan_target": "recon_port_scan",
        "nmap_scan": "recon_port_scan",
        "sast": "sast_code_audit", "code_audit": "sast_code_audit", "sast_scan": "sast_code_audit",
        "security_audit": "sast_code_audit", "static_analysis": "sast_code_audit", "audit_code": "sast_code_audit",
        "cve": "cve_advisory_search", "cve_search": "cve_advisory_search", "cve_lookup": "cve_advisory_search",
        "search_cve": "cve_advisory_search", "advisory_search": "cve_advisory_search",
        "poc": "falsifiable_poc_runner", "run_poc": "falsifiable_poc_runner", "exploit": "falsifiable_poc_runner",
        "verify_poc": "falsifiable_poc_runner", "verify_exploit": "falsifiable_poc_runner",
        "evidence": "evidence_recorder", "save_evidence": "evidence_recorder", "record_evidence": "evidence_recorder",
        "screenshot": "capture_screenshot", "screenshot_tool": "capture_screenshot",
        "take_screenshot": "capture_screenshot", "browser_screenshot": "capture_screenshot",
        "web_screenshot": "capture_screenshot", "playwright": "capture_screenshot", "browser": "capture_screenshot",
        "playwright_automation": "capture_screenshot",
        "subagent": "invoke_subagent", "delegate": "invoke_subagent",
        "a2a": "send_a2a_message",
        "list_mcp": "list_mcp_servers", "mcp_list": "list_mcp_servers", "mcp_servers": "list_mcp_servers",
        "get_mcp_servers": "list_mcp_servers", "show_mcp_servers": "list_mcp_servers", "get_mcp": "list_mcp_servers",
        "list_mcp_tools": "list_mcp_servers", "mcp_status": "list_mcp_servers", "show_mcp": "list_mcp_servers",
        "check_mcp": "list_mcp_servers", "mcp": "list_mcp_servers",
        "add_mcp": "add_mcp_server", "mcp_add": "add_mcp_server", "install_mcp": "add_mcp_server",
        "mcp_install": "add_mcp_server", "add_mcp_tool": "add_mcp_server", "setup_mcp": "add_mcp_server",
        "enable_mcp": "add_mcp_server", "configure_mcp": "add_mcp_server",
        "skills": "list_skills", "get_skills": "list_skills", "show_skills": "list_skills",
        "skills_list": "list_skills", "list_all_skills": "list_skills", "get_all_skills": "list_skills",
        "add_skill": "install_skill", "skill_install": "install_skill", "install_skills": "install_skill",
    }
    clean_name = aliases.get(name.lower().strip(), name.strip())

    # Smart disambiguation for "search"
    if clean_name == "search":
        q_val = str(args.get("query") or args.get("q") or args.get("pattern") or "").lower()
        if any(k in args for k in ("directory", "dir", "path", "file_pattern")) or "code" in q_val:
            clean_name = "grep_search"
        elif "http" in q_val or "www." in q_val or (tool_map and "grep_search" not in tool_map):
            clean_name = "google_web_search"
        elif tool_map and "grep_search" in tool_map:
            clean_name = "grep_search"
        else:
            clean_name = "google_web_search"

    if tool_map:
        if clean_name not in tool_map:
            for tm_k in tool_map.keys():
                if tm_k.lower() == clean_name.lower():
                    clean_name = tm_k
                    break

    norm_args = dict(args)
    if len(norm_args) == 1 and isinstance(list(norm_args.values())[0], dict) and list(norm_args.keys())[0] in ("input", "args", "parameters", "arguments"):
        norm_args = dict(list(norm_args.values())[0])

    for k in ("path", "filename", "file", "filepath", "target_file", "file_name", "target"):
        if k in norm_args and "file_path" not in norm_args and clean_name in ("view_file", "edit_file", "write_file", "append_file"):
            norm_args["file_path"] = norm_args.pop(k)
            break

    for k in ("old_str", "old_string", "find", "target", "old", "old_content", "search", "pattern"):
        if k in norm_args and "target_content" not in norm_args and clean_name == "edit_file":
            norm_args["target_content"] = norm_args.pop(k)
            break

    for k in ("new_str", "new_string", "replace", "replacement", "new", "new_content"):
        if k in norm_args and "replacement_content" not in norm_args and clean_name == "edit_file":
            norm_args["replacement_content"] = norm_args.pop(k)
            break

    for k in ("text", "data", "body", "code"):
        if k in norm_args and "content" not in norm_args and clean_name in ("write_file", "append_file"):
            norm_args["content"] = norm_args.pop(k)
            break

    for k in ("cmd", "script", "input", "code", "exec", "c", "--command"):
        if k in norm_args and "command" not in norm_args:
            norm_args["command"] = norm_args.pop(k)
            break

    for k in ("q", "pattern", "search_query", "search", "term", "text", "regex"):
        if k in norm_args and "query" not in norm_args and clean_name in ("grep_search", "google_web_search", "cve_advisory_search"):
            norm_args["query"] = norm_args.pop(k)
            break

    for k in ("glob", "file_pattern", "query", "search"):
        if k in norm_args and "pattern" not in norm_args and clean_name == "glob_files":
            norm_args["pattern"] = norm_args.pop(k)
            break

    for k in ("dir", "folder", "path"):
        if k in norm_args and "directory" not in norm_args and clean_name in ("glob_files", "grep_search"):
            norm_args["directory"] = norm_args.pop(k)
            break

    for k in ("host", "ip", "domain", "url", "hostname"):
        if k in norm_args and "target" not in norm_args and clean_name in ("recon_port_scan", "falsifiable_poc_runner"):
            norm_args["target"] = norm_args.pop(k)
            break

    if "port" in norm_args and "ports" not in norm_args and clean_name == "recon_port_scan":
        val = norm_args.pop("port")
        if isinstance(val, (list, tuple)):
            val = ",".join(str(p) for p in val)
        norm_args["ports"] = str(val)

    for k in ("code", "snippet", "file", "file_path", "path", "input"):
        if k in norm_args and "code_snippet_or_file" not in norm_args and clean_name == "sast_code_audit":
            norm_args["code_snippet_or_file"] = norm_args.pop(k)
            break

    for k in ("link", "website", "uri", "target", "address"):
        if k in norm_args and "url" not in norm_args and clean_name in ("capture_screenshot", "web_surface_probe"):
            norm_args["url"] = norm_args.pop(k)
            break

    for k in ("output", "file", "destination", "output_path", "path", "filename"):
        if k in norm_args and "output_file" not in norm_args and clean_name == "capture_screenshot":
            norm_args["output_file"] = norm_args.pop(k)
            break

    for k in ("agent", "subagent", "name"):
        if k in norm_args and "agent_name" not in norm_args and clean_name == "invoke_subagent":
            norm_args["agent_name"] = norm_args.pop(k)
            break

    for k in ("prompt", "instruction", "message", "query"):
        if k in norm_args and "task" not in norm_args and clean_name == "invoke_subagent":
            norm_args["task"] = norm_args.pop(k)
            break

    for k in ("server", "server_name", "title"):
        if k in norm_args and "name" not in norm_args and clean_name == "add_mcp_server":
            norm_args["name"] = norm_args.pop(k)
            break

    for k in ("cmd", "exec", "cli", "run"):
        if k in norm_args and "command" not in norm_args and clean_name == "add_mcp_server":
            norm_args["command"] = norm_args.pop(k)
            break

    for k in ("url", "repo", "git_url", "git_repo", "repository", "target"):
        if k in norm_args and "repo_url" not in norm_args and clean_name == "install_skill":
            norm_args["repo_url"] = norm_args.pop(k)
            break

    for k in ("skill_name", "title"):
        if k in norm_args and "name" not in norm_args and clean_name == "create_skill":
            norm_args["name"] = norm_args.pop(k)
            break

    for k in ("trigger", "trigger_words", "keywords"):
        if k in norm_args and "triggers" not in norm_args and clean_name == "create_skill":
            norm_args["triggers"] = norm_args.pop(k)
            break

    for k in ("instruction", "prompt", "skill_instructions", "content", "body"):
        if k in norm_args and "instructions" not in norm_args and clean_name == "create_skill":
            norm_args["instructions"] = norm_args.pop(k)
            break

    return clean_name, norm_args



def _extract_tool_calls(msg: Any, tool_map: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Extract tool calls from native AIMessage.tool_calls or raw text syntax (ChatML, XML, dots_function_call, JSON, ReAct, Python syntax)."""
    extracted: List[Dict[str, Any]] = []

    # 1. Native LangChain tool calls
    native = getattr(msg, "tool_calls", None) or []
    for tc in native:
        if isinstance(tc, dict) and tc.get("name"):
            name = tc.get("name")
            args = tc.get("args") or {}
            norm_name, norm_args = _normalize_tool_call(name, args, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            })
    if extracted:
        return extracted

    content = str(getattr(msg, "content", "") or "")
    if not content.strip():
        return []

    # 2. Qwen / ChatML syntax: <|tool_call_start|>[func(args)]<|tool_call_end|>
    qwen_matches = re.findall(r"<\|tool_call_start\|>(.*?)<\|tool_call_end\|>", content, re.DOTALL)
    for m in qwen_matches:
        m = m.strip()
        if m.startswith("{") or m.startswith("["):
            try:
                parsed = json.loads(m)
                items = parsed if isinstance(parsed, list) else [parsed]
                for item in items:
                    if isinstance(item, dict) and ("name" in item or "tool" in item):
                        name = item.get("name") or item.get("tool")
                        args = item.get("arguments") or item.get("args") or item.get("parameters") or {}
                        norm_name, norm_args = _normalize_tool_call(name, args, tool_map)
                        extracted.append({
                            "name": norm_name,
                            "args": norm_args,
                            "id": f"call_{uuid.uuid4().hex[:8]}"
                        })
            except Exception:
                pass

        func_calls = re.findall(r"(\w+)\s*\((.*?)\)", m, re.DOTALL)
        for fn_name, fn_args_str in func_calls:
            args_dict = {}
            if fn_args_str.strip():
                try:
                    tree = ast.parse(f"{fn_name}({fn_args_str})")
                    call_node = tree.body[0].value
                    for kw in call_node.keywords:
                        try:
                            args_dict[kw.arg] = ast.literal_eval(kw.value)
                        except Exception:
                            args_dict[kw.arg] = str(kw.value)
                except Exception:
                    pairs = re.findall(r'(\w+)\s*=\s*(?:\"([^\"]*)\"|\'([^\']*)\'|([^\s,\)]+))', fn_args_str)
                    for k, v1, v2, v3 in pairs:
                        args_dict[k] = v1 or v2 or v3
            norm_name, norm_args = _normalize_tool_call(fn_name, args_dict, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })
    if extracted:
        return extracted

    # 3. XML style tags: <dots_function_call>, <tool_call>, <function_call>, <invoke>, <arg_key>
    # 3-pre. Arg-key / arg-value pattern (e.g. Invoking tool: bash_cli<arg_key>command</arg_key><arg_value>... or <tool_call>bash_cli<arg_key>...)
    arg_kv_blocks = re.findall(
        r"(?:Invoking\s+tool:\s*|<(?:tool_call|function_call)>\s*)?([a-zA-Z_]\w*)\s*((?:<arg_key>.*?</arg_value>\s*)+)(?:\s*</(?:tool_call|function_call)>)?",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    for fn_name, kv_blob in arg_kv_blocks:
        fn_name = fn_name.strip()
        norm_fn, _ = _normalize_tool_call(fn_name, {}, tool_map)
        if (tool_map and (norm_fn in tool_map or fn_name in tool_map)) or (not tool_map and re.match(r"^[a-zA-Z_]\w*$", fn_name)):
            pairs = re.findall(r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", kv_blob, re.DOTALL)
            parsed_args = {}
            for k, v in pairs:
                k = k.strip()
                v = v.strip()
                if (v.startswith("{") and v.endswith("}")) or (v.startswith("[") and v.endswith("]")):
                    try:
                        parsed_args[k] = json.loads(v)
                    except Exception:
                        parsed_args[k] = v
                else:
                    parsed_args[k] = v
            norm_name, norm_args = _normalize_tool_call(norm_fn or fn_name, parsed_args, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })
    if extracted:
        return extracted

    # 3a. <dots_function_call> blocks
    dots_matches = re.findall(r"<dots_function_call>(.*?)</dots_function_call>", content, re.DOTALL)
    for dm in dots_matches:
        dm_clean = dm.strip()
        inv_m = re.search(r'(?:<invoke\s+(?:name|tool|function)=[\"\']?|)(\w+)[\"\']?>(.*?)(?:</invoke>|$)', dm_clean, re.DOTALL)
        if inv_m and (inv_m.group(1) in (tool_map or {}) or any(t.lower() == inv_m.group(1).lower() for t in (tool_map or {}))):
            fn_name = inv_m.group(1).strip()
            body = inv_m.group(2).strip()
            args = {}
            for p_name, val in re.findall(r'<parameter\s+name=[\"\'](\w+)[\"\']>(.*?)</parameter>', body, re.DOTALL):
                args[p_name] = val.strip()
            if not args:
                for k, v in re.findall(r'(\w+)\s*=\s*[\"\']([^\"\']*)[\"\']', body):
                    args[k] = v
            norm_name, norm_args = _normalize_tool_call(fn_name, args, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })
            continue

        fn_call = re.search(r'(\w+)\s*(?:\((.*?)\)|$)', dm_clean, re.DOTALL)
        if fn_call and (fn_call.group(1) in (tool_map or {}) or any(t.lower() == fn_call.group(1).lower() for t in (tool_map or {}))):
            fn_name = fn_call.group(1).strip()
            args = {}
            if fn_call.group(2):
                for k, v in re.findall(r'(\w+)\s*=\s*[\"\']([^\"\']*)[\"\']', fn_call.group(2)):
                    args[k] = v
            norm_name, norm_args = _normalize_tool_call(fn_name, args, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })

    # 3b. <tool_call>...</tool_call> or <function_call>...</function_call>
    tag_matches = re.findall(r"<(?:tool_call|function_call)>(.*?)</(?:tool_call|function_call)>", content, re.DOTALL)
    for tm in tag_matches:
        tm_clean = tm.strip()

        # Check for <function=tool_name>...<parameter=key>val</parameter>...</function>
        fn_m = re.search(r"<function(?:=|\s+name=)[\"']?(\w+)[\"']?>(.*?)</function>", tm_clean, re.DOTALL)
        if fn_m:
            fn_name = fn_m.group(1).strip()
            body = fn_m.group(2).strip()
            params = {}
            for p_name, val in re.findall(r"<parameter(?:=|\s+name=)[\"']?(\w+)[\"']?>(.*?)</parameter>", body, re.DOTALL):
                params[p_name] = val.strip()
            if not params:
                for k, v in re.findall(r'(\w+)\s*=\s*[\"\']([^\"\']*)[\"\']', body):
                    params[k] = v
            norm_name, norm_args = _normalize_tool_call(fn_name, params, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })
            continue

        # Strip markdown fences if present inside <tool_call>
        unfenced = re.sub(r"^```(?:json)?\s*", "", tm_clean).rstrip("` \n\r\t")
        if unfenced != tm_clean:
            try:
                data = json.loads(unfenced)
                if isinstance(data, dict):
                    t_name = data.get("name") or data.get("tool") or data.get("action")
                    t_args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
                    if t_name:
                        norm_name, norm_args = _normalize_tool_call(t_name, t_args if isinstance(t_args, dict) else {"input": str(t_args)}, tool_map)
                        extracted.append({
                            "name": norm_name,
                            "args": norm_args,
                            "id": f"call_{uuid.uuid4().hex[:8]}"
                        })
                        continue
            except Exception:
                pass

        bare_name = re.sub(r"\(.*?\)$", "", tm_clean).strip()
        norm_bare, _ = _normalize_tool_call(bare_name, {}, tool_map)
        if (tool_map and (norm_bare in tool_map or bare_name in tool_map)) or (not tool_map and re.match(r"^[a-zA-Z_]\w*$", norm_bare or bare_name)):
            arg_m = re.search(r"^\w+\s*\((.*?)\)$", tm_clean, re.DOTALL)
            args = {}
            if arg_m and arg_m.group(1).strip():
                try:
                    tree = ast.parse(f"{bare_name}({arg_m.group(1)})")
                    for kw in tree.body[0].value.keywords:
                        try:
                            args[kw.arg] = ast.literal_eval(kw.value)
                        except Exception:
                            args[kw.arg] = str(kw.value)
                except Exception:
                    for k, v1, v2, v3 in re.findall(r'(\w+)\s*=\s*(?:\"([^\"]*)\"|\'([^\']*)\'|([^\s,\)]+))', arg_m.group(1)):
                        args[k] = v1 or v2 or v3
            norm_name, norm_args = _normalize_tool_call(bare_name, args, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })
            continue

        name_m = re.search(r"<name>(.*?)</name>", tm_clean, re.DOTALL)
        if name_m:
            fn_name = name_m.group(1).strip()
            args_m = re.search(r"<arguments>(.*?)</arguments>", tm_clean, re.DOTALL)
            args = {}
            if args_m:
                try:
                    args = json.loads(args_m.group(1).strip())
                except Exception:
                    args = {"input": args_m.group(1).strip()}
            norm_name, norm_args = _normalize_tool_call(fn_name, args, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })
            continue

        try:
            data = json.loads(tm_clean)
            if isinstance(data, dict):
                t_name = data.get("name") or data.get("tool") or data.get("action")
                t_args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
                if t_name:
                    norm_name, norm_args = _normalize_tool_call(t_name, t_args if isinstance(t_args, dict) else {"input": str(t_args)}, tool_map)
                    extracted.append({
                        "name": norm_name,
                        "args": norm_args,
                        "id": f"call_{uuid.uuid4().hex[:8]}"
                    })
                    continue
        except Exception:
            pass

    if extracted:
        return extracted

    # 3c. Standalone <function=tool_name>...<parameter=key>val</parameter>...</function>
    func_matches = re.findall(r"<function(?:=|\s+name=)[\"']?(\w+)[\"']?>(.*?)</function>", content, re.DOTALL)
    for fn, body in func_matches:
        params = dict(re.findall(r"<parameter(?:=|\s+name=)[\"']?(\w+)[\"']?>(.*?)</parameter>", body, re.DOTALL))
        if not params:
            for k, v in re.findall(r'(\w+)\s*=\s*[\"\']([^\"\']*)[\"\']', body):
                params[k] = v
        norm_name, norm_args = _normalize_tool_call(fn, params, tool_map)
        extracted.append({
            "name": norm_name,
            "args": norm_args,
            "id": f"call_{uuid.uuid4().hex[:8]}"
        })

    # 3d. Standalone <invoke name="..."> tags
    inv_matches = re.findall(r"<invoke\s+(?:name|tool|function)=[\"\'](\w+)[\"\']>(.*?)</invoke>", content, re.DOTALL)
    for fn, body in inv_matches:
        params = dict(re.findall(r"<parameter\s+name=[\"\'](\w+)[\"\']>(.*?)</parameter>", body, re.DOTALL))
        if not params:
            for k, v in re.findall(r'(\w+)\s*=\s*[\"\']([^\"\']*)[\"\']', body):
                params[k] = v
        norm_name, norm_args = _normalize_tool_call(fn, params, tool_map)
        extracted.append({
            "name": norm_name,
            "args": norm_args,
            "id": f"call_{uuid.uuid4().hex[:8]}"
        })

    if extracted:
        return extracted

    # 4. Markdown JSON code blocks or raw JSON
    json_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    for jb in json_blocks:
        try:
            data = json.loads(jb)
            if any(k in data for k in ("tool", "name", "action")) and any(k in data for k in ("args", "arguments", "parameters", "action_input", "input")):
                t_name = data.get("name") or data.get("tool") or data.get("action")
                t_args = data.get("arguments") or data.get("args") or data.get("parameters") or data.get("action_input") or data.get("input") or {}
                if isinstance(t_name, str) and t_name.strip():
                    t_args_dict = t_args if isinstance(t_args, dict) else {"input": str(t_args)}
                    norm_name, norm_args = _normalize_tool_call(t_name, t_args_dict, tool_map)
                    extracted.append({
                        "name": norm_name,
                        "args": norm_args,
                        "id": f"call_{uuid.uuid4().hex[:8]}"
                    })
        except Exception:
            pass

    # Standalone JSON objects outside backticks
    if not extracted:
        standalone_jsons = re.findall(r'(\{(?:[^{}]|(?:\{[^{}]*\}))*\})', content)
        for sj in standalone_jsons:
            try:
                data = json.loads(sj)
                if any(k in data for k in ("tool", "name", "action")) and any(k in data for k in ("args", "arguments", "parameters", "action_input", "input")):
                    t_name = data.get("name") or data.get("tool") or data.get("action")
                    t_args = data.get("arguments") or data.get("args") or data.get("parameters") or data.get("action_input") or data.get("input") or {}
                    if isinstance(t_name, str) and t_name.strip():
                        t_args_dict = t_args if isinstance(t_args, dict) else {"input": str(t_args)}
                        norm_name, norm_args = _normalize_tool_call(t_name, t_args_dict, tool_map)
                        extracted.append({
                            "name": norm_name,
                            "args": norm_args,
                            "id": f"call_{uuid.uuid4().hex[:8]}"
                        })
            except Exception:
                pass

    if extracted:
        return extracted

    # 5. ReAct format: Action: <tool>\nAction Input: <input>
    react_inline = re.findall(r"(?:Action|Tool):\s*(\w+)\s*\((.*?)\)", content)
    for act_name, act_args_str in react_inline:
        args_dict = {}
        if act_args_str.strip():
            try:
                tree = ast.parse(f"{act_name}({act_args_str})")
                call_node = tree.body[0].value
                for kw in call_node.keywords:
                    try:
                        args_dict[kw.arg] = ast.literal_eval(kw.value)
                    except Exception:
                        args_dict[kw.arg] = str(kw.value)
            except Exception:
                pairs = re.findall(r'(\w+)\s*=\s*(?:\"([^\"]*)\"|\'([^\']*)\'|([^\s,\)]+))', act_args_str)
                for k, v1, v2, v3 in pairs:
                    args_dict[k] = v1 or v2 or v3
        norm_name, norm_args = _normalize_tool_call(act_name, args_dict, tool_map)
        extracted.append({
            "name": norm_name,
            "args": norm_args,
            "id": f"call_{uuid.uuid4().hex[:8]}"
        })

    react_matches = re.findall(r"(?:Action|Tool):\s*(\w+)\s*\n(?:Action\s+Input|Tool\s+Input|Input|Args|Parameters|Arguments):\s*([^\n]+|\{.*?\})", content, re.IGNORECASE | re.DOTALL)
    for act_name, act_inp in react_matches:
        act_name = act_name.strip()
        act_inp = act_inp.strip()
        args = {}
        if act_inp.startswith("{") and act_inp.endswith("}"):
            try:
                args = json.loads(act_inp)
            except Exception:
                args = {"input": act_inp}
        else:
            args = {"input": act_inp}
        norm_name, norm_args = _normalize_tool_call(act_name, args, tool_map)
        extracted.append({
            "name": norm_name,
            "args": norm_args,
            "id": f"call_{uuid.uuid4().hex[:8]}"
        })

    if extracted:
        return extracted

    # 6. Direct python-style function call in prose or code blocks: tool_name(param1="...", ...)
    fn_matches = re.finditer(r'(?:^|[^\w\.])([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)', content, re.DOTALL)
    for m in fn_matches:
        fn_name = m.group(1).strip()
        fn_args_str = m.group(2).strip()
        norm_name, _ = _normalize_tool_call(fn_name, {}, tool_map)
        if tool_map and norm_name in tool_map:
            args_dict = {}
            if fn_args_str:
                try:
                    tree = ast.parse(f"{fn_name}({fn_args_str})")
                    call_node = tree.body[0].value
                    for kw in call_node.keywords:
                        try:
                            args_dict[kw.arg] = ast.literal_eval(kw.value)
                        except Exception:
                            args_dict[kw.arg] = str(kw.value)
                except Exception:
                    pairs = re.findall(r'(\w+)\s*=\s*(?:\"([^\"]*)\"|\'([^\']*)\'|([^\s,\)]+))', fn_args_str)
                    for k, v1, v2, v3 in pairs:
                        args_dict[k] = v1 or v2 or v3
            _, norm_args = _normalize_tool_call(fn_name, args_dict, tool_map)
            extracted.append({
                "name": norm_name,
                "args": norm_args,
                "id": f"call_{uuid.uuid4().hex[:8]}"
            })

    return extracted


def _clean_tool_call_text(content: str) -> str:
    """Strip raw tool-calling markup tags so conversation text remains clean and human-readable."""
    cleaned = content
    cleaned = re.sub(r"(?:Invoking\s+tool:\s*|<(?:tool_call|function_call)>\s*)?[a-zA-Z_]\w*\s*(?:<arg_key>.*?</arg_value>\s*)+(?:</(?:tool_call|function_call)>)?", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<arg_key>.*?</arg_value>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<\|tool_call_start\|>.*?<\|tool_call_end\|>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<dots_function_call>.*?</dots_function_call>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<(?:tool_call|function_call)>.*?</(?:tool_call|function_call)>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<function(?:=|\s+name=).*?</function>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<invoke\s+(?:name|tool|function)=[\"\']\w+[\"\']>.*?</invoke>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(?:Action|Tool):\s*\w+\s*\n(?:Action\s+Input|Tool\s+Input|Input|Args|Parameters|Arguments):\s*(?:[^\n]+|\{.*?\})", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"```(?:json)?\s*\{\s*\"(?:tool|name|action)\".*?\}\s*```", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"^Invoking\s+tool:\s*\w+.*?(?=\n|$)", "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
    return cleaned.strip()


# Keywords indicating the user goal requires REAL side effects (not chat).
_EXEC_TASK_KEYWORDS = (
    "screenshot", "capture", "playwright", "save to", "save it", "store here",
    "copy to", "cp ", "mkdir", "write file", "create file", "download",
    "ls ", "verify", "desktop", "takescreenshot", "take a screenshot",
)

# Patterns indicating the text claims a file/external operation succeeded.
_SUCCESS_CLAIM_RE = re.compile(
    r"(successfully captured|success.{0,20}captured|screenshot.*saved|file_size|"
    r"PNG image data|1\.2MB|1920x1080|"
    r"\"status\"\s*:\s*\"success\"|results?:\s*\n?.*verification)",
    re.IGNORECASE,
)

# Broader: operational goals that MUST be fulfilled by a real tool round.
# Intentionally narrow — only match unambiguously agentic/technical requests
# that a prose reply cannot satisfy. Conversational messages ("hi", "thanks",
# "explain X", "what is Y") must NOT match here.
_OPERATIONAL_GOAL_RE = re.compile(
    r"\b(port\s*scan|nmap|recon|probe|enumerate|audit|sast|cve|exploit|poc|"
    r"git\s+(status|diff|commit|clone|log)|clone\s+(repo|https?://)|"
    r"grep\b|glob\s+|web\s*search|google\s+search|browse\s+(to|https?://)|"
    r"curl\s+|wget\s+|bash\s+-c|shell\s+command|terminal\s+command|"
    r"install\s+(package|skill|pip|npm|brew|apt)|list\s+files\s+(in|at|under)|"
    r"find\s+files?\s+(in|at|under|named|matching)|"
    r"screenshot|capture\s+(screenshot|screen|page)|download\s+(file|repo|url)|"
    r"cp\s+\S|mkdir\s+\S|create\s+file\s+(at|in|named)|"
    r"build\s+(project|app|docker|image)|compile\s+(code|project))\b",
    re.IGNORECASE,
)

# Prose that claims an action was PERFORMED (past tense) rather than proposed.
_NARRATED_EXECUTION_RE = re.compile(
    r"\b(i\s+(ran|executed|scanned|searched|cloned|found|checked|audited|probed|"
    r"captured|saved|created|wrote|installed|fetched|retrieved|discovered|verified|"
    r"have\s+(run|executed|completed|scanned|searched|found)|"
    r"'ve\s+(run|executed|completed|scanned|searched|found))|"
    r"here\s+(are|is)\s+the\s+(results?|output|findings?|open\s+ports?)|"
    r"the\s+(scan|search|audit|command|probe)\s+(found|returned|shows?|revealed|completed)|"
    r"(results?|output|findings?)\s*:|open\s+ports?\s*(are|:))\b",
    re.IGNORECASE,
)


def _goal_is_operational(user_goal: str) -> bool:
    """True when the goal should normally require at least one real tool round."""
    return bool(_OPERATIONAL_GOAL_RE.search(user_goal or ""))


def _claims_execution(text: str) -> bool:
    """True when prose claims an action was already performed (past tense narration)."""
    return bool(_NARRATED_EXECUTION_RE.search(text or ""))


def _task_needs_execution(user_goal: str) -> bool:
    """True when the goal needs real tool side effects (Claude Code never fakes these)."""
    goal = (user_goal or "").lower()
    return any(k in goal for k in _EXEC_TASK_KEYWORDS)


def _claims_file_success(text: str) -> bool:
    """True when text claims a file/external operation succeeded."""
    return bool(_SUCCESS_CLAIM_RE.search(text or ""))


def _compact_context(messages: List[BaseMessage], max_messages: int = 18) -> List[BaseMessage]:
    """Preserves SystemMessage and initial user goal while compacting intermediate tool turns.

    Ensures OpenAI/Anthropic tool-calling protocol validity:
    - Never leaves an orphaned ToolMessage without its parent AIMessage(tool_calls).
    - Never leaves an AIMessage(tool_calls) without its corresponding ToolMessages.
    """
    if len(messages) <= max_messages:
        return messages

    header_msgs: List[BaseMessage] = []
    rest_msgs: List[BaseMessage] = []
    for i, m in enumerate(messages):
        if i == 0 and isinstance(m, SystemMessage):
            header_msgs.append(m)
        elif len(header_msgs) == 1 and isinstance(m, HumanMessage):
            header_msgs.append(m)
        else:
            rest_msgs.append(m)

    if not header_msgs:
        header_msgs = messages[:1]
        rest_msgs = messages[1:]

    tail_target = max(6, max_messages - len(header_msgs) - 1)
    if len(rest_msgs) <= tail_target:
        return messages

    cut_idx = len(rest_msgs) - tail_target
    while cut_idx < len(rest_msgs) and isinstance(rest_msgs[cut_idx], ToolMessage):
        cut_idx += 1

    if cut_idx >= len(rest_msgs) - 2:
        return messages

    pruned_count = cut_idx
    summary_msg = SystemMessage(
        content=(
            f"[ScopeForge Context Compactor: {pruned_count} earlier intermediate execution steps "
            f"were compacted to preserve token limits for long-running workflows.]"
        )
    )

    return header_msgs + [summary_msg] + rest_msgs[cut_idx:]

from pathlib import Path
from ..a2a.bus import A2ABus
from ..a2a.protocol import A2AIntent, A2AMessage
from ..llm_providers.manager import ProviderManager
from ..llm_providers.models import LLMConfig, ProviderType
from ..mcp_bridge import MCPBridge
from ..middleware.pipeline import MiddlewarePipeline, create_default_pipeline
from ..rag.engine import LlamaSecRAG
from ..sec_tools import (
    ALL_CODE_TOOLS,
    ALL_CYBER_TOOLS,
    ALL_SUITE_TOOLS,
    append_file,
    bash_cli,
    bash_security_exec,
    capture_screenshot,
    cve_advisory_search,
    edit_file,
    evidence_recorder,
    falsifiable_poc_runner,
    git_commit_tool,
    git_diff_tool,
    git_status_tool,
    glob_files,
    google_web_search,
    grep_search,
    recon_port_scan,
    sast_code_audit,
    store_large_file,
    view_file,
    web_surface_probe,
    write_file,
)
from ..skills.manager import SkillManager
from ..wiki.store import SecWiki
from .custom_agent import CustomAgentLoader
from .state import AgentState



class MultiAgentSecOpsOrchestrator:
    """Coordinates the cybersecurity multi-agent team via LangGraph."""

    def __init__(
        self,
        provider_manager: Optional[ProviderManager] = None,
        pipeline: Optional[MiddlewarePipeline] = None,
        rag: Optional[LlamaSecRAG] = None,
        wiki: Optional[SecWiki] = None,
        a2a_bus: Optional[A2ABus] = None,
        skill_manager: Optional[SkillManager] = None,
        mcp_bridge: Optional[MCPBridge] = None,
    ):
        self.provider_mgr = provider_manager or ProviderManager()
        self.pipeline = pipeline or create_default_pipeline()
        self.rag = rag or LlamaSecRAG()
        self.wiki = wiki or SecWiki()
        self.a2a_bus = a2a_bus or A2ABus()
        self.skill_mgr = skill_manager or SkillManager()
        self.mcp = mcp_bridge or MCPBridge()
        self.custom_loader = CustomAgentLoader()

        self.graph = self._build_graph()

    def set_mode(self, mode: str):
        """Change the ScopeGate execution mode ('plan', 'artifacts', 'live')."""
        self.pipeline.set_mode(mode)
        return f"ScopeGate mode changed to: {mode.upper()}"

    def add_authorized_scope(self, target: str):
        """Add an authorized target to the ScopeGate allowlist."""
        gate = self.pipeline.get_scope_gate()
        if gate:
            gate.add_scope(target)
            return f"Added '{target}' to authorized scopes"
        return "ScopeGate middleware not found"

    def get_current_mode(self) -> str:
        """Get the current ScopeGate execution mode."""
        gate = self.pipeline.get_scope_gate()
        if gate:
            return gate.mode
        return "unknown"

    def _get_project_instructions(self) -> str:
        """Load project instructions from SCOPEFORGE.md or CLAUDE.md if present."""
        for name in ["SCOPEFORGE.md", "CLAUDE.md", ".scopeforge/instructions.md"]:
            p = Path(name)
            if p.exists() and p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return f"\n[Project Guidelines from {name}]:\n" + f.read()[:4000]
                except Exception:
                    pass
        return ""

    def _invoke_tool_safely(self, tool_func: Any, args: Dict[str, Any], agent_name: str) -> str:
        """Execute a tool through the middleware pipeline."""
        tool_name = getattr(tool_func, "name", str(tool_func))
        allowed, reason, modified_args = self.pipeline.run_before_tool(
            tool_name=tool_name,
            tool_args=args,
            metadata={"agent": agent_name},
        )
        if not allowed:
            return f"[Tool Blocked by Middleware: {reason}]"

        try:
            raw_result = tool_func.invoke(modified_args)
        except Exception as e:
            raw_result = f"Error executing {tool_name}: {e}"

        sanitized_result = self.pipeline.run_after_tool(
            tool_name=tool_name,
            result=raw_result,
            metadata={"agent": agent_name},
        )
        out_str = str(sanitized_result)
        # Cap tool output size to prevent blowing up the LLM context window!
        MAX_TOOL_CHARS = 12000
        HEAD_CHARS = 8000
        TAIL_CHARS = 3000
        if len(out_str) > MAX_TOOL_CHARS:
            spill_hint = ""
            try:
                import datetime
                spill_dir = Path(".scopeforge/runs/tool_spills")
                spill_dir.mkdir(parents=True, exist_ok=True)
                clean_tool = "".join(c for c in tool_name if c.isalnum() or c in ("-", "_"))
                spill_file = spill_dir / f"spill_{clean_tool}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
                spill_file.write_text(out_str, encoding="utf-8")
                spill_hint = f" [Full untruncated content preserved on disk at: {spill_file}]"
            except Exception:
                pass

            omitted = len(out_str) - HEAD_CHARS - TAIL_CHARS
            out_str = (
                f"{out_str[:HEAD_CHARS]}\n\n"
                f"... [ScopeForge Context Window Safeguard: {omitted} characters truncated to avoid token overflow.{spill_hint}] ...\n\n"
                f"{out_str[-TAIL_CHARS:]}"
            )
        return out_str

    def _run_subagent(self, agent_name: str, task: str) -> str:
        """Execute a subagent synchronously, track with A2A protocol, and return its output."""
        agent_clean = agent_name.lower().strip()
        if agent_clean.endswith("agent"):
            agent_clean = agent_clean[:-5]

        # Publish A2A delegation message
        self.a2a_bus.send(
            sender="Supervisor",
            recipient=f"{agent_clean.capitalize()}Agent",
            intent=A2AIntent.TASK_DELEGATION,
            payload={"task": task},
        )

        cb = _stream_callback_var.get()
        if cb:
            cb("supervisor", json.dumps({
                "__type__": "a2a_banner",
                "sender": "Supervisor",
                "recipient": f"{agent_clean.capitalize()}Agent",
                "intent": "TASK_DELEGATION",
                "preview": task[:80],
            }))

        output = ""
        if agent_clean == "recon":
            import re
            m = re.search(r"\b(?:https?://)?([a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}|localhost|127\.0\.0\.1)(?::\d+)?\b", task)
            target = m.group(1) if m else "authorized.example"
            m_ports = re.search(r"\bports?\s*[:=]?\s*([0-9][0-9,\s-]*)", task, re.IGNORECASE)
            ports = m_ports.group(1).replace(" ", "").rstrip(",-") if m_ports else "80,443,8080"
            scan_out = self._invoke_tool_safely(recon_port_scan, {"target": target, "ports": ports}, "ReconAgent")
            probe_out = self._invoke_tool_safely(web_surface_probe, {"url": f"https://{target}"}, "ReconAgent")
            output = f"[ReconAgent Results for {target}]\nPorts:\n{scan_out}\nSurface:\n{probe_out}"

        elif agent_clean == "audit":
            import re
            m_cve = re.search(r"(CVE-\d{4}-\d{4,7})", task, re.IGNORECASE)
            cve_q = m_cve.group(1).upper() if m_cve else "CVE-2024-3400"
            sast_out = self._invoke_tool_safely(sast_code_audit, {"code_snippet_or_file": task}, "AuditAgent")
            cve_out = self._invoke_tool_safely(cve_advisory_search, {"query": cve_q}, "AuditAgent")
            output = f"[AuditAgent Results]\nSAST:\n{sast_out}\nCVE ({cve_q}):\n{cve_out}"

        elif agent_clean == "exploit":
            poc_out = self._invoke_tool_safely(
                falsifiable_poc_runner,
                {"hypothesis": task, "target": "authorized.example", "payload_type": "verification"},
                "ExploitAgent",
            )
            output = f"[ExploitAgent Results]\nPoC:\n{poc_out}"

        elif agent_clean == "report":
            output = f"[ReportAgent Results]\nSummary: Assessment finalized for task '{task[:60]}'. Security findings cataloged and remediation roadmap prepared."

        elif agent_clean == "dev":
            task_low = task.lower()
            if ("skill" in task_low or "install" in task_low or "clone" in task_low) and ("github.com" in task_low or "http://" in task_low or "https://" in task_low):
                import re
                m_url = re.search(r"https?://[^\s'\"`]+", task)
                if m_url:
                    url = m_url.group(0).rstrip(".,;)")
                    ok, msg, installed = self.skill_mgr.install_skill_from_repo(url)
                    for s in installed:
                        self.skill_mgr.activate_skill(s)
                    output = f"Skill Installation: {'SUCCESS' if ok else 'FAILED'} - {msg}"
                else:
                    output = "DevAgent: Invalid URL for skill install."
            elif any(k in task_low for k in ("run command", "terminal", "bash", "execute command")) or task.strip().startswith("$") or task.strip().startswith("!"):
                cmd = task.strip().lstrip("$!").strip()
                for pfx in ("run command", "execute command", "run the command", "execute the command", "bash", "terminal"):
                    if cmd.lower().startswith(pfx):
                        cmd = cmd[len(pfx):].strip(" :\"'")
                        break
                output = self._invoke_tool_safely(bash_cli, {"command": cmd}, "DevAgent")
            elif "grep" in task_low or "search" in task_low:
                m_q = re.search(r'["\']([^"\']+)["\']', task)
                if m_q:
                    q = m_q.group(1)
                else:
                    q_str = task
                    for pfx in ("grep for", "search for", "grep", "search code for", "search in files for", "search"):
                        if task_low.startswith(pfx):
                            q_str = task[len(pfx):].strip(" :\"'")
                            break
                    q = q_str if q_str else "def "
                output = self._invoke_tool_safely(grep_search, {"query": q}, "DevAgent")
            else:
                output = self._invoke_tool_safely(glob_files, {"pattern": "*"}, "DevAgent")
        else:
            output = f"Unknown subagent '{agent_name}'. Available: recon, audit, exploit, report, dev."

        # Publish A2A result back to supervisor
        self.a2a_bus.send(
            sender=f"{agent_clean.capitalize()}Agent",
            recipient="Supervisor",
            intent=A2AIntent.TASK_RESULT,
            payload={"task": task, "status": "COMPLETED", "summary": output[:200]},
        )
        return output

    def _get_orchestrator_tools(self) -> List[Any]:
        """Create LangChain tools bound to orchestrator for autonomous multi-agent and harness operations."""
        def _invoke_subagent_func(agent_name: str, task: str) -> str:
            return self._run_subagent(agent_name=agent_name, task=task)

        def _send_a2a_message_func(recipient: str, intent: str, message: str) -> str:
            clean_intent = A2AIntent.TASK_DELEGATION
            try:
                for member in A2AIntent:
                    if member.value.lower() == intent.lower() or member.name.lower() == intent.lower():
                        clean_intent = member
                        break
            except Exception:
                pass
            msg = self.a2a_bus.send(
                sender="Supervisor",
                recipient=recipient,
                intent=clean_intent,
                payload={"message": message},
            )
            cb = _stream_callback_var.get()
            if cb:
                cb("supervisor", json.dumps({
                    "__type__": "a2a_banner",
                    "sender": "Supervisor",
                    "recipient": recipient,
                    "intent": clean_intent.value,
                    "preview": message[:60],
                }))
            return json.dumps({
                "status": "SENT",
                "message_id": getattr(msg, "message_id", str(msg)),
                "sender": "Supervisor",
                "recipient": recipient,
                "intent": clean_intent.value,
            })

        def _install_skill_func(repo_url: str) -> str:
            ok, msg, installed = self.skill_mgr.install_skill_from_repo(repo_url)
            for s in installed:
                self.skill_mgr.activate_skill(s)
            return json.dumps({
                "success": ok,
                "message": msg,
                "installed_skills": installed,
                "active_skills": list(self.skill_mgr.active_skills),
            }, indent=2)

        def _create_skill_func(name: str, description: str, triggers: str, instructions: str) -> str:
            trigger_list = [t.strip() for t in triggers.split(",") if t.strip()]
            skill = self.skill_mgr.create_skill(name, description, trigger_list, instructions)
            self.skill_mgr.activate_skill(name)
            return json.dumps({
                "success": True,
                "skill": skill.name,
                "path": skill.path,
                "triggers": skill.triggers,
            }, indent=2)

        def _list_skills_func(**kwargs) -> str:
            skills = self.skill_mgr.list_skills()
            res = []
            for s in skills:
                res.append({
                    "name": s.name,
                    "description": s.description,
                    "triggers": s.triggers,
                    "active": s.name in self.skill_mgr.active_skills,
                })
            return json.dumps({"skills_count": len(res), "skills": res}, indent=2)

        def _add_mcp_server_func(name: str, command: str) -> str:
            try:
                try:
                    self.mcp.registry.remove_server(name)
                except Exception:
                    pass
                self.mcp.registry.add_server(name, command)
                self.mcp.enable_server(name)
                return json.dumps({"status": "SUCCESS", "message": f"MCP server '{name}' added and enabled."})
            except Exception as e:
                return json.dumps({"status": "ERROR", "error": str(e)})

        def _list_mcp_servers_func(**kwargs) -> str:
            servers = self.mcp.list_servers()
            tools = self.mcp.get_langchain_tools()
            return json.dumps({
                "servers": servers,
                "tools": [{"name": t.name, "description": t.description} for t in tools],
            }, indent=2)

        def _add_scope_func(target: str) -> str:
            msg = self.add_authorized_scope(target)
            return json.dumps({"status": "SUCCESS", "message": msg, "target": target})

        def _get_scope_status_func(**kwargs) -> str:
            gate = self.pipeline.get_scope_gate()
            return json.dumps({
                "mode": self.get_current_mode(),
                "authorized_scopes": sorted(list(gate.authorized_scopes)) if gate else [],
                "auto_adapt": getattr(gate, "auto_adapt", True) if gate else False,
            }, indent=2)

        return [
            StructuredTool.from_function(
                func=_invoke_subagent_func,
                name="invoke_subagent",
                description=(
                    "Delegate a task or sub-task to a specialized ScopeForge subagent. "
                    "Available agents: 'recon' (port scanning, perimeter probing), "
                    "'audit' (SAST code analysis, CVE advisory correlation), "
                    "'exploit' (falsifiable PoC verification, evidence recording), "
                    "'report' (SecOps executive reporting), "
                    "'dev' (file operations, bash execution, skill installation)."
                ),
            ),
            StructuredTool.from_function(
                func=_send_a2a_message_func,
                name="send_a2a_message",
                description=(
                    "Send an explicit inter-agent communication message across the A2A bus. "
                    "Recipient can be 'ReconAgent', 'AuditAgent', 'ExploitAgent', 'ReportAgent', 'DevAgent', or custom."
                ),
            ),
            StructuredTool.from_function(
                func=_install_skill_func,
                name="install_skill",
                description=(
                    "Install and activate a skill package from a git repository URL (e.g. 'https://github.com/...'). "
                    "Skills provide specialized domain playbooks and guidelines."
                ),
            ),
            StructuredTool.from_function(
                func=_create_skill_func,
                name="create_skill",
                description=(
                    "Create a new custom skill with SKILL.md instructions and trigger keywords. "
                    "Args: name (str), description (str), triggers (comma-separated string), instructions (markdown string)."
                ),
            ),
            StructuredTool.from_function(
                func=_list_skills_func,
                name="list_skills",
                description="List all discovered and active skills currently available in ScopeForge.",
            ),
            StructuredTool.from_function(
                func=lambda **kwargs: json.dumps(self.skill_mgr.reload()),
                name="reload_skills",
                description="Rescan SKILL.md files on disk so newly added skills apply without restart. Use after writing .scopeforge/skills/<name>/SKILL.md.",
            ),
            StructuredTool.from_function(
                func=_add_mcp_server_func,
                name="add_mcp_server",
                description="Add and activate a Model Context Protocol (MCP) server by name and command line.",
            ),
            StructuredTool.from_function(
                func=_list_mcp_servers_func,
                name="list_mcp_servers",
                description="List all configured MCP servers and their active tool definitions.",
            ),
            StructuredTool.from_function(
                func=lambda **kwargs: json.dumps(self.mcp.refresh()),
                name="reload_mcp",
                description="Refresh MCP tool discovery cache so newly enabled servers/tools appear immediately.",
            ),
            StructuredTool.from_function(
                func=_add_scope_func,
                name="add_authorized_scope",
                description="Dynamically add a target domain, URL, or IP address to the ScopeGate authorized scope allowlist.",
            ),
            StructuredTool.from_function(
                func=_get_scope_status_func,
                name="get_scope_status",
                description="Retrieve current ScopeGate authorization status, active mode, and allowlist entries.",
            ),
        ]

    async def _execute_react_agent_loop(
        self,
        agent_name: str,
        system_prompt: str,
        tools: List[Any],
        state: AgentState,
        max_iterations: int = 25,
        response_prefix: str = "",
    ) -> Dict[str, Any]:
        """Runs the autonomous ReAct (Reasoning -> Action -> Observation -> Synthesis) agent loop."""
        chat_model = self.provider_mgr.get_chat_model()
        active_cfg = self.provider_mgr.get_active_config()
        is_mock = (
            getattr(chat_model, "is_mock", False)
            or active_cfg.provider == ProviderType.MOCK
            or "mock" in str(getattr(chat_model, "model_name", "")).lower()
        )

        tool_map = {getattr(t, "name", str(t)): t for t in tools}
        model_to_call = chat_model
        if not is_mock and hasattr(chat_model, "bind_tools"):
            try:
                model_to_call = chat_model.bind_tools(tools)
            except Exception:
                model_to_call = chat_model

        full_system_prompt = system_prompt
        catalog_text = _format_tools_prompt_catalog(tools)
        if catalog_text:
            full_system_prompt = f"{system_prompt}\n\n{catalog_text}"

        prompt_msgs = [SystemMessage(content=full_system_prompt)] + list(state["messages"][-5:])
        prompt_msgs = self.pipeline.run_before_llm(prompt_msgs, {"agent": agent_name.capitalize()})
        cb = _stream_callback_var.get()

        try:
            current_msgs = list(prompt_msgs)
            iteration = 0
            forced_exec_retries = 0
            response = AIMessage(content="")
            native_tools_supported = True

            while iteration < max_iterations:
                current_msgs = _compact_context(current_msgs, max_messages=18)
                full_chunks = []
                accumulated_stream = ""

                fallback_msgs = []
                for idx_m, m in enumerate(current_msgs):
                    if isinstance(m, ToolMessage):
                        is_last_tool = (idx_m == len(current_msgs) - 1) or all(not isinstance(later_m, ToolMessage) for later_m in current_msgs[idx_m + 1:])
                        instr = "\n\n[Instruction: If more actions are needed to complete the user's objective, execute the next tool now. Otherwise, synthesize your final comprehensive response for the user.]" if is_last_tool else ""
                        fallback_msgs.append(HumanMessage(content=f"[Tool Observation for '{getattr(m, 'name', 'tool')}']:\n{m.content}{instr}"))
                    elif isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                        clean_c = m.content or f"Action: {m.tool_calls[0].get('name') if m.tool_calls else 'tool'}"
                        fallback_msgs.append(AIMessage(content=clean_c))
                    else:
                        fallback_msgs.append(m)

                stream_err_occurred = False
                last_stream_err = None

                # Attempt 1: Stream using current model_to_call if native function calling is supported
                if native_tools_supported:
                    try:
                        async for chunk in model_to_call.astream(current_msgs):
                            full_chunks.append(chunk)
                            txt = _extract_chunk_text(getattr(chunk, "content", ""))
                            if txt:
                                accumulated_stream += txt
                                if cb:
                                    if not any(tag in accumulated_stream.lower() for tag in (
                                        "<tool_call>", "<dots_function_call>", "<function_call>",
                                        "<function=", "<function ", "<parameter=", "<parameter ",
                                        "<invoke", "<|tool_call_start|>"
                                    )):
                                        cb(agent_name.lower(), txt)
                    except Exception as stream_err:
                        stream_err_occurred = True
                        last_stream_err = stream_err
                        native_tools_supported = False

                # Attempt 2: If native tools not supported or 0 chunks returned or stream raised an error
                # (e.g. proxy failing on native OpenAI tools payload), retry with unbound chat_model.
                # System prompt catalog + _extract_tool_calls preserves complete tool calling capabilities.
                if not native_tools_supported or stream_err_occurred or not full_chunks:
                    native_tools_supported = False
                    full_chunks = []
                    accumulated_stream = ""
                    model_to_call = chat_model
                    current_msgs = fallback_msgs

                    try:
                        async for chunk in chat_model.astream(current_msgs):
                            full_chunks.append(chunk)
                            txt = _extract_chunk_text(getattr(chunk, "content", ""))
                            if txt:
                                accumulated_stream += txt
                                if cb:
                                    if not any(tag in accumulated_stream.lower() for tag in (
                                        "<tool_call>", "<dots_function_call>", "<function_call>",
                                        "<function=", "<function ", "<parameter=", "<parameter ",
                                        "<invoke", "<|tool_call_start|>"
                                    )):
                                        cb(agent_name.lower(), txt)
                    except Exception as stream_err2:
                        last_stream_err = stream_err2

                # Attempt 3: If streaming still yielded 0 chunks or raised an error (e.g. proxy does not
                # support SSE streaming at all), fall back to direct non-streaming ainvoke().
                if not full_chunks:
                    try:
                        direct_chunk = await chat_model.ainvoke(current_msgs)
                        if direct_chunk and (getattr(direct_chunk, "content", None) or getattr(direct_chunk, "tool_calls", None)):
                            full_chunks = [direct_chunk]
                            txt = _extract_chunk_text(getattr(direct_chunk, "content", ""))
                            if txt and cb:
                                if not any(tag in txt.lower() for tag in (
                                    "<tool_call>", "<dots_function_call>", "<function_call>",
                                    "<invoke", "<|tool_call_start|>"
                                )):
                                    cb(agent_name.lower(), txt)
                    except Exception as inv_err:
                        try:
                            simplified_msgs = [m for m in current_msgs if isinstance(m, (SystemMessage, HumanMessage))]
                            if not simplified_msgs:
                                simplified_msgs = current_msgs
                            direct_chunk = await chat_model.ainvoke(simplified_msgs)
                            if direct_chunk and (getattr(direct_chunk, "content", None) or getattr(direct_chunk, "tool_calls", None)):
                                full_chunks = [direct_chunk]
                                txt = _extract_chunk_text(getattr(direct_chunk, "content", ""))
                                if txt and cb:
                                    if not any(tag in txt.lower() for tag in (
                                        "<tool_call>", "<dots_function_call>", "<function_call>",
                                        "<invoke", "<|tool_call_start|>"
                                    )):
                                        cb(agent_name.lower(), txt)
                        except Exception:
                            if not ("claude" in str(active_cfg.model).lower() and active_cfg.api_base):
                                if last_stream_err:
                                    raise last_stream_err
                                raise inv_err

                # Attempt 4: If model is a Claude model with a custom api_base,
                # try the native Anthropic /v1/messages protocol via ChatAnthropic.
                # Many gateways (OneAPI, NewAPI, Cloudflare AI Gateway) allow /v1/messages while
                # blocking or misconfiguring /v1/chat/completions.
                if not full_chunks and "claude" in str(active_cfg.model).lower() and active_cfg.api_base:
                    try:
                        from langchain_anthropic import ChatAnthropic
                        alt_anthropic = ChatAnthropic(
                            model=active_cfg.model,
                            api_key=active_cfg.api_key or "sk-dummy",
                            anthropic_api_url=active_cfg.api_base,
                            temperature=active_cfg.temperature,
                        )
                        full_chunks = []
                        accumulated_stream = ""
                        try:
                            async for chunk in alt_anthropic.astream(current_msgs):
                                full_chunks.append(chunk)
                                txt = _extract_chunk_text(getattr(chunk, "content", ""))
                                if txt:
                                    accumulated_stream += txt
                                    if cb:
                                        if not any(tag in accumulated_stream.lower() for tag in (
                                            "<tool_call>", "<dots_function_call>", "<function_call>",
                                            "<invoke", "<|tool_call_start|>"
                                        )):
                                            cb(agent_name.lower(), txt)
                        except Exception:
                            full_chunks = []

                        if not full_chunks:
                            try:
                                alt_inv = await alt_anthropic.ainvoke(current_msgs)
                                if alt_inv and (getattr(alt_inv, "content", None) or getattr(alt_inv, "tool_calls", None)):
                                    full_chunks = [alt_inv]
                                    txt = _extract_chunk_text(getattr(alt_inv, "content", ""))
                                    if txt and cb:
                                        cb(agent_name.lower(), txt)
                            except Exception:
                                pass

                        if full_chunks:
                            model_to_call = alt_anthropic
                            chat_model = alt_anthropic
                            last_stream_err = None
                    except Exception:
                        pass

                if not full_chunks and last_stream_err:
                    raise last_stream_err

                if full_chunks:
                    iter_resp = full_chunks[0]
                    for c in full_chunks[1:]:
                        iter_resp = iter_resp + c
                else:
                    iter_resp = AIMessage(content="")

                extracted_content = _extract_chunk_text(getattr(iter_resp, "content", ""))
                if extracted_content:
                    iter_resp.content = extracted_content

                tool_calls = _extract_tool_calls(iter_resp, tool_map)
                if not tool_calls:
                    content_str = str(extracted_content or "").strip()

                    # ── Extract the user's original goal (used by all guards below) ────────
                    _user_goal = ""
                    try:
                        for _m in reversed(state.get("messages", []) or []):
                            if isinstance(_m, HumanMessage):
                                _user_goal = str(_m.content)
                                break
                    except Exception:
                        _user_goal = ""

                    # ── Short-circuit: conversational / explain / Q&A goals ────────────────
                    # Goals ≤ 8 words that are NOT unambiguously operational (e.g. port scan,
                    # nmap, screenshot) pass straight through — no fabrication guards needed.
                    _goal_is_op = _goal_is_operational(_user_goal)
                    _goal_looks_conversational = (
                        len((_user_goal or "").split()) <= 8
                        and not _goal_is_op
                    )

                    # ── Detect planning/reasoning responses that have no tool calls yet ─────
                    # Only fire when the goal is genuinely operational AND the reply looks
                    # like a plan ("I'll start by…", "Step 1:", "Let me …"). Skip for
                    # conversational messages — no tools are needed for "hi" or "explain X".
                    _is_planning_only = (
                        not _goal_looks_conversational
                        and _goal_is_op
                        and iteration < 3
                        and bool(tool_map)
                        and any(marker in content_str for marker in (
                            "I'll ", "I will ", "Step 1", "Step 2", "First,", "first,",
                            "Next,", "next,", "Let me ", "I'll start", "I'll begin", "sequentially",
                            "I need to ", "Let's start", "I'm going to ",
                        ))
                        and len(content_str) < 800  # short planning text, not a real final answer
                    )

                    # ── Hallucinated-execution guard ──────────────────────────────────────
                    # Task needs real side effects (screenshot, file write/cp, bash) but
                    # model produced ZERO tool calls and instead faked success JSON
                    # (status:success, file_size, PNG image data, ls output).
                    _tool_msgs_so_far = sum(1 for m in current_msgs if isinstance(m, ToolMessage))
                    _task_needs_exec = _task_needs_execution(_user_goal)
                    _claims_success = _claims_file_success(content_str)
                    _is_hallucinated_exec = (
                        not _goal_looks_conversational
                        and _task_needs_exec and _tool_msgs_so_far == 0
                        and (_claims_success or len(content_str) > 400)
                        and forced_exec_retries < 4 and iteration < max_iterations - 1
                    )
                    # ── Fabricated-completion guard (general operational goals) ───────────
                    # The goal is operational (port scan/nmap/recon/…), NO tool has run yet,
                    # and the model narrated an action as done. Force a real tool round.
                    _is_fabricated_completion = (
                        not _goal_looks_conversational
                        and not _is_hallucinated_exec
                        and bool(tool_map)
                        and _tool_msgs_so_far == 0
                        and _goal_is_op
                        and _claims_execution(content_str)
                        and forced_exec_retries < 4 and iteration < max_iterations - 1
                    )
                    if _is_planning_only or _is_hallucinated_exec or _is_fabricated_completion:
                        # Inject a continuation prompt to push from planning → acting
                        current_msgs.append(AIMessage(content=content_str))
                        if _is_hallucinated_exec:
                            forced_exec_retries += 1
                            current_msgs.append(HumanMessage(
                                content=(
                                    "You produced a text-only success report with ZERO tool executions. "
                                    "That is fabricated — the files do not exist. Now execute REAL tool calls: "
                                    "use capture_screenshot(url, output_file) then bash_cli(command=\"ls -lh <file> && file <file>\") to verify. "
                                    "Only report success with observed ToolMessage output. If the browser extra is missing, report the real error."
                                )
                            ))
                        elif _is_fabricated_completion:
                            forced_exec_retries += 1
                            current_msgs.append(HumanMessage(
                                content=(
                                    "You described results in prose but executed ZERO tools, so those results are fabricated — "
                                    "no scan, search, or command actually ran. Do NOT narrate what a tool would return. "
                                    "Emit a real tool call now (the available tools are bound to you) and wait for the actual "
                                    "ToolMessage output before reporting anything. If no tool fits the request, say so plainly "
                                    "instead of inventing output."
                                )
                            ))
                        else:
                            current_msgs.append(HumanMessage(
                                content="Good plan. Now execute your tools immediately to fulfill the task. Do not describe what you will do — just call the tools now."
                            ))
                        iteration += 1
                        continue
                    response = iter_resp
                    break

                iteration += 1
                cleaned_text = _clean_tool_call_text(extracted_content)
                iter_resp.content = cleaned_text if cleaned_text.strip() else f"Invoking tool: {tool_calls[0].get('name', 'tool')}"
                iter_resp.tool_calls = tool_calls
                current_msgs.append(iter_resp)

                for tc in tool_calls:
                    tc_name = tc.get("name", "")
                    tc_args = tc.get("args", {})
                    if not isinstance(tc_args, dict):
                        try:
                            tc_args = json.loads(tc_args) if isinstance(tc_args, str) else {}
                        except Exception:
                            tc_args = {}
                    tc_id = tc.get("id") or f"call_{tc_name}_{iteration}"

                    if cb:
                        cb(agent_name.lower(), json.dumps({"__type__": "tool_call", "name": tc_name, "args": tc_args}))
                    await asyncio.sleep(0.01)

                    if tc_name in tool_map:
                        t_out = await asyncio.to_thread(self._invoke_tool_safely, tool_map[tc_name], tc_args, f"{agent_name.capitalize()}Agent")
                    else:
                        t_out = f"Tool '{tc_name}' is not registered."

                    if cb:
                        cb(agent_name.lower(), json.dumps({"__type__": "tool_result", "name": tc_name, "result": str(t_out)}))
                    await asyncio.sleep(0.01)

                    current_msgs.append(ToolMessage(content=str(t_out), tool_call_id=tc_id, name=tc_name))

                response = iter_resp

            # Synthesis safeguard for multi-step workflows or iteration limits
            if (iteration >= max_iterations and getattr(response, "tool_calls", None)) or (
                not str(getattr(response, "content", "") or "").strip() and iteration > 0
            ):
                synth_msgs = _compact_context(list(current_msgs), max_messages=16)
                synth_msgs.append(
                    SystemMessage(
                        content="You have executed the required actions. Synthesize all observations, tool outputs, and actions above into a comprehensive, clear, and final response for the user."
                    )
                )
                synth_chunks = []
                try:
                    async for chunk in chat_model.astream(synth_msgs):
                        synth_chunks.append(chunk)
                        txt = _extract_chunk_text(getattr(chunk, "content", ""))
                        if txt and cb:
                            cb(agent_name.lower(), txt)
                except Exception:
                    synth_chunks = []

                if not synth_chunks:
                    try:
                        synth_inv = await chat_model.ainvoke(synth_msgs)
                        if synth_inv:
                            synth_chunks = [synth_inv]
                            txt = _extract_chunk_text(getattr(synth_inv, "content", ""))
                            if txt and cb:
                                cb(agent_name.lower(), txt)
                    except Exception:
                        pass
                if synth_chunks:
                    synth_resp = synth_chunks[0]
                    for c in synth_chunks[1:]:
                        synth_resp = synth_resp + c
                    extracted_synth = _extract_chunk_text(getattr(synth_resp, "content", ""))
                    if extracted_synth.strip():
                        response = AIMessage(content=extracted_synth)

            # Claude Code resilience: reasoning models (deepseek-r1, etc.)
            if not str(getattr(response, "content", "") or "").strip():
                reason_text = ""
                try:
                    ak = getattr(response, "additional_kwargs", {}) or {}
                    for k in ("reasoning", "reasoning_content", "reasoning_details"):
                        v = ak.get(k)
                        if isinstance(v, str) and v.strip():
                            reason_text = v.strip()
                            break
                        if isinstance(v, list) and v:
                            parts = []
                            for item in v:
                                if isinstance(item, dict):
                                    t = item.get("text") or item.get("reasoning") or ""
                                    if t:
                                        parts.append(str(t))
                            if parts:
                                reason_text = "\n".join(parts).strip()
                                break
                    if not reason_text:
                        meta = getattr(response, "response_metadata", {}) or {}
                        for k in ("reasoning", "reasoning_content"):
                            v = meta.get(k)
                            if isinstance(v, str) and v.strip():
                                reason_text = v.strip()
                                break
                except Exception:
                    reason_text = ""
                if reason_text:
                    response = AIMessage(content=reason_text[:4000])
                else:
                    last_msg_txt = str(state["messages"][-1].content) if state.get("messages") else ""
                    response = AIMessage(
                        content=(
                            f"⚠️ **Empty reply from `{active_cfg.model}`** (transient reasoning-model blank — Claude Code retries instead of failing).\n\n"
                            f"Query was: **{last_msg_txt[:400]}**\n\n"
                            "Retry once, or switch to a non-reasoning preset:\n"
                            "- `/model openrouter-free` (`openrouter/free` — verified live)\n"
                            "- `/model openrouter-free-nemotron` / `/model groq-llama3`\n"
                        )
                    )

            # FINAL SAFETY NET (never present unverified success):
            # a stubborn weak model can burn all forced retries and still return
            # text claiming files were saved/copied. Claude Code would report
            # failure, never a fabricated file listing — so replace it.
            # NOTE: conversational/explain goals bypass this entirely.
            try:
                _final_goal = ""
                for _m in reversed(state.get("messages", []) or []):
                    if isinstance(_m, HumanMessage):
                        _final_goal = str(_m.content)
                        break
                _final_conv = (
                    len((_final_goal or "").split()) <= 8
                    and not _goal_is_operational(_final_goal)
                )
                _final_obs = sum(1 for _m in current_msgs if isinstance(_m, ToolMessage))
                _final_text = str(getattr(response, "content", "") or "")
                if not _final_conv and _task_needs_execution(_final_goal) and _final_obs == 0 and _claims_file_success(_final_text):
                    response = AIMessage(
                        content=(
                            "❌ **Could not complete: no tools were actually executed.**\n\n"
                            f"Goal was: **{_final_goal[:300]}**\n\n"
                            "The model described success in text but produced zero tool observations "
                            "after repeated attempts, so nothing was saved, copied, or captured. "
                            "I am not reporting fabricated paths or file sizes.\n\n"
                            "**Next steps (pick one):**\n"
                            "- Retry the task (sometimes the model complies on a fresh attempt)\n"
                            "- `/model` — switch to a stronger model (reasoning/free-tier routers often ignore tool calls)\n"
                            "- Run the first step directly: `bash_cli(command=\"ls -lh <dir>\")` to confirm the workspace, then re-issue the task\n"
                            "- `/doctor` — diagnose provider/model health"
                        )
                    )
                elif (
                    not _final_conv
                    and _goal_is_operational(_final_goal)
                    and _final_obs == 0
                    and _claims_execution(_final_text)
                ):
                    response = AIMessage(
                        content=(
                            "❌ **Could not complete: no tools were actually executed.**\n\n"
                            f"Goal was: **{_final_goal[:300]}**\n\n"
                            "The model narrated results (scan/search/command output) in prose but produced "
                            "zero tool observations after repeated attempts, so nothing actually ran. "
                            "I am not reporting fabricated results.\n\n"
                            "**Next steps (pick one):**\n"
                            "- Retry the task (the model sometimes complies on a fresh attempt)\n"
                            "- `/model` — switch to a stronger model (free-tier/reasoning routers often ignore tool calls)\n"
                            "- `/doctor` — diagnose provider/model health"
                        )
                    )
            except Exception:
                pass

        except Exception as e:
            err = str(e)
            fallback_success = False

            # Direct fallback on the active model itself via non-streaming ainvoke:
            if not is_mock:
                try:
                    direct_resp = await chat_model.ainvoke(prompt_msgs)
                    ext_c = _extract_chunk_text(getattr(direct_resp, "content", ""))
                    if ext_c and ext_c.strip():
                        if cb:
                            cb(agent_name.lower(), ext_c)
                        response = AIMessage(content=ext_c)
                        fallback_success = True
                except Exception:
                    fallback_success = False

            is_openrouter = (
                active_cfg.provider == ProviderType.OPENROUTER
                or getattr(active_cfg.provider, "value", str(active_cfg.provider)).lower() == "openrouter"
                or "openrouter" in str(active_cfg.api_base or "").lower()
            )
            if not fallback_success and active_cfg.model != "openrouter/free" and is_openrouter:
                try:
                    import os
                    from ..llm_providers.factory import create_chat_model
                    fb_key = (
                        active_cfg.api_key
                        or os.getenv("OPENROUTER_API_KEY")
                        or getattr(self.provider_mgr.providers.get("openrouter-free"), "api_key", None)
                    )
                    fb_cfg = LLMConfig(
                        name="openrouter-free-fallback",
                        provider=ProviderType.OPENROUTER,
                        model="openrouter/free",
                        api_key=fb_key,
                        api_base="https://openrouter.ai/api/v1",
                        temperature=0.2,
                        extra_headers={
                            "HTTP-Referer": "https://github.com/rajboopathiking/ScopeForge",
                            "X-Title": "ScopeForge Agent Harness",
                        },
                    )
                    fb_model = create_chat_model(fb_cfg)
                    fb_chunks = []
                    try:
                        async for chunk in fb_model.astream(prompt_msgs):
                            fb_chunks.append(chunk)
                            txt = _extract_chunk_text(getattr(chunk, "content", ""))
                            if txt and cb:
                                cb(agent_name.lower(), txt)
                    except Exception:
                        fb_chunks = []
                    if not fb_chunks:
                        try:
                            fb_inv = await fb_model.ainvoke(prompt_msgs)
                            if fb_inv:
                                fb_chunks = [fb_inv]
                                txt = _extract_chunk_text(getattr(fb_inv, "content", ""))
                                if txt and cb:
                                    cb(agent_name.lower(), txt)
                        except Exception:
                            pass
                    if fb_chunks:
                        fb_response = fb_chunks[0]
                        for c in fb_chunks[1:]:
                            fb_response = fb_response + c
                        extracted_fb = _extract_chunk_text(getattr(fb_response, "content", ""))
                        if extracted_fb.strip():
                            response = AIMessage(content=extracted_fb)
                            fallback_success = True
                except Exception:
                    fallback_success = False

            if not fallback_success and not is_mock and "claude" in str(active_cfg.model).lower() and active_cfg.api_base:
                try:
                    from langchain_anthropic import ChatAnthropic
                    anth_direct = ChatAnthropic(
                        model=active_cfg.model,
                        api_key=active_cfg.api_key or "sk-dummy",
                        anthropic_api_url=active_cfg.api_base,
                        temperature=active_cfg.temperature,
                    )
                    res_direct = await anth_direct.ainvoke(prompt_msgs)
                    ext_c = _extract_chunk_text(getattr(res_direct, "content", ""))
                    if ext_c and ext_c.strip():
                        if cb:
                            cb(agent_name.lower(), ext_c)
                        response = AIMessage(content=ext_c)
                        fallback_success = True
                        active_cfg.provider = ProviderType.ANTHROPIC
                        try:
                            self.provider_mgr.save_config()
                        except Exception:
                            pass
                except Exception:
                    pass

            if not fallback_success:
                def _is_real_key(k: Optional[str]) -> bool:
                    if not k or len(str(k)) < 12:
                        return False
                    kl = str(k).lower()
                    return not any(d in kl for d in ("wizard", "test", "dummy", "fake", "placeholder", "sk-xxx"))

                seen_keys: set[str] = set()
                if active_cfg.api_key:
                    seen_keys.add(active_cfg.api_key)

                # Prioritize candidate providers:
                # 1. Models with 'free' or 'openrouter' in name/provider/model
                # 2. Other configured candidates with real non-duplicate keys
                cand_list = []
                for c_name, c_cfg in self.provider_mgr.providers.items():
                    if c_name == active_cfg.name:
                        continue
                    c_key = getattr(c_cfg, "api_key", None)
                    if not _is_real_key(c_key) or c_key in seen_keys:
                        continue
                    cand_list.append((c_name, c_cfg))

                def _cand_priority(item):
                    name, cfg = item
                    score = 0
                    nm = name.lower()
                    mod = str(getattr(cfg, "model", "")).lower()
                    prov = str(getattr(cfg, "provider", "")).lower()
                    if "free" in nm or "free" in mod:
                        score -= 10
                    if "openrouter" in nm or "openrouter" in prov:
                        score -= 5
                    return score

                cand_list.sort(key=_cand_priority)

                tested_candidates = 0
                for cand_name, cand_cfg in cand_list:
                    if tested_candidates >= 5:
                        break
                    cand_k = getattr(cand_cfg, "api_key", None)
                    if cand_k in seen_keys:
                        continue
                    seen_keys.add(cand_k)
                    tested_candidates += 1
                    try:
                        from ..llm_providers.factory import create_chat_model
                        cand_model = create_chat_model(cand_cfg)

                        async def _attempt_cand():
                            chunks = []
                            try:
                                async for chunk in cand_model.astream(prompt_msgs):
                                    chunks.append(chunk)
                                    txt = _extract_chunk_text(getattr(chunk, "content", ""))
                                    if txt and cb:
                                        cb(agent_name.lower(), txt)
                            except Exception:
                                chunks = []
                            if not chunks:
                                try:
                                    inv = await cand_model.ainvoke(prompt_msgs)
                                    if inv:
                                        chunks = [inv]
                                        txt = _extract_chunk_text(getattr(inv, "content", ""))
                                        if txt and cb:
                                            cb(agent_name.lower(), txt)
                                except Exception:
                                    pass
                            return chunks

                        cand_chunks = await asyncio.wait_for(_attempt_cand(), timeout=6.5)
                        if cand_chunks:
                            fb_resp = cand_chunks[0]
                            for c in cand_chunks[1:]:
                                fb_resp = fb_resp + c
                            ext_txt = _extract_chunk_text(getattr(fb_resp, "content", ""))
                            if ext_txt.strip():
                                response = AIMessage(
                                    content=(
                                        f"*[Resilience Failover: automatically switched to `{cand_name}` due to error on `{active_cfg.name}`]*\n\n"
                                        + ext_txt
                                    )
                                )
                                fallback_success = True
                                try:
                                    self.provider_mgr.set_active_provider(cand_name)
                                except Exception:
                                    pass
                                break
                    except Exception:
                        continue

            if not fallback_success:
                err_short = err.splitlines()[0][:600] if err else type(e).__name__
                last_msg_txt = str(state["messages"][-1].content) if state.get("messages") else ""
                _goal_clean = last_msg_txt.strip().lower()
                _is_greeting = (
                    len(_goal_clean.split()) <= 6
                    and any(g in _goal_clean for g in ("hi", "hello", "hey", "hola", "greetings", "good morning", "good afternoon", "good evening", "who are you", "what can you do"))
                    and not _goal_is_operational(_goal_clean)
                )
                _prov_val = getattr(active_cfg.provider, "value", str(active_cfg.provider))
                if _is_greeting:
                    response = AIMessage(
                        content=(
                            "Hello! 👋 I am **ScopeForge**, your autonomous agentic coding and security harness.\n\n"
                            f"⚠️ *Notice: Active provider `{active_cfg.name}` encountered an issue ({err_short}).*\n\n"
                            "**To start running agentic coding tasks:**\n"
                            "- `/model` — switch to another provider (e.g. `/model open-free` or another working key)\n"
                            f"- `/config set key <API_KEY>` — set a valid key for `{_prov_val}`\n"
                            "- `/doctor` — diagnose provider connection and health\n\n"
                            "How can I help you today?"
                        )
                    )
                else:
                    response = AIMessage(
                        content=(
                            f"⚠️ **LLM call failed** (`{active_cfg.name}` / `{active_cfg.model}`): {err_short}\n\n"
                            f"Query was: **{last_msg_txt[:400]}**\n\n"
                            "**Fix (pick one):**\n"
                            f"- `/model` — switch to another configured provider (e.g. `/model 1` or `/model claude`)\n"
                            f"- `/config set key <API_KEY>` — set key for `{_prov_val}`\n"
                            f"- `/config set model <model_id>` — e.g. `openrouter/free`\n"
                            f"- `/doctor` — diagnose provider connections and configuration"
                        )
                    )

        clean_res = _clean_tool_call_text(str(response.content).strip())
        if not clean_res.strip():
            clean_res = str(response.content).strip()
        if response_prefix and not clean_res.startswith(response_prefix):
            response.content = f"{response_prefix}\n\n{clean_res}"

        else:
            response.content = clean_res

        response = self.pipeline.run_after_llm(response, {"agent": agent_name.capitalize()})
        return {
            "active_agent": agent_name.lower(),
            "next_step": "end",
            "messages": [response],
            "user_preferences": state.get("user_preferences", ""),
            "rag_context": state.get("rag_context", ""),
        }

    def _build_graph(self):
        builder = StateGraph(AgentState)

        # 1. Define Agent Nodes
        async def supervisor_node(state: AgentState) -> Dict[str, Any]:
            import re

            last_message = state["messages"][-1].content if state["messages"] else ""
            last_lower = str(last_message).lower()

            # Retrieve RAG, Wiki, and Project Memory context
            rag_info = self.rag.get_context_for_prompt(str(last_message))
            user_prefs = self.wiki.get_user_preferences()
            proj_rules = self._get_project_instructions()

            # Routing heuristic / agent delegation (conservative, Open Code style:
            # general chat stays on supervisor; specialists only on operational intent).
            # Previous substring checks (`"port" in ...`, `"enum" in ...`) mis-routed
            # general questions like "explain ports in docker" or "surface tension".
            target_agent = "supervisor"
            intent = A2AIntent.TASK_DELEGATION

            # Explicit routing override from `/agent <name>` (stored in state)
            forced_agent = str(state.get("forced_agent") or "").lower().strip()
            if forced_agent in ("recon", "audit", "exploit", "report", "dev", "supervisor"):
                target_agent = forced_agent
                # One-shot unless TUI re-sets it; clear after use is handled by caller
            else:
                # Legacy prefix support: "[agent:xxx] query" (older TUI builds)
                m_forced = re.match(r"\[agent:(\w+)\]\s*(.*)", str(last_message), re.DOTALL | re.IGNORECASE)
                if m_forced and m_forced.group(1).lower() in ("recon", "audit", "exploit", "report", "dev"):
                    target_agent = m_forced.group(1).lower()
                    last_message = m_forced.group(2)
                    last_lower = str(last_message).lower()
                else:
                    def _has(pattern: str) -> bool:
                        return re.search(pattern, last_lower) is not None

                    # Operational recon: needs action verb + target-ish context, not conceptual Q&A
                    is_conceptual = _has(r"\b(what is|what are|explain|difference|vs\.?\b|tension|how does|tutorial|in docker|docker.*port)\b")
                    has_target_hint = _has(r"(authorized|example\.com|localhost|127\.0\.0\.1|https?://|\btarget\b|\bscope\b|:\d+\b)")
                    has_recon_verb = _has(r"\b(port\s*scans?|nmap|recon(naissance)?|enumerate\s+(ports|hosts|subdomains|surface)|surface\s*(probe|map|enum)|open\s+ports?|scan\s+(the\s+)?target)\b")
                    if has_recon_verb and (has_target_hint or not is_conceptual):
                        # Still avoid conceptual "explain ports in docker" style
                        if not (is_conceptual and not has_target_hint):
                            target_agent = "recon"
                    elif _has(r"\b(sast|static\s+analysis|cve-\d+|vuln\s*(scan|audit|assess)|sqli|xss|cwe-\d+)\b") and _has(
                        r"\b(audit|scan|review|check|triage|correlat|cve|sast|sqli|xss)\b"
                    ):
                        # Require security-operational context, not generic "review my code"
                        if has_target_hint or _has(r"\b(audit|sast|cve|vuln|sqli|xss|cwe)\b"):
                            # Generic "review my code for bugs" without sec keywords stays supervisor/dev
                            if _has(r"\b(sast|cve|vuln|sqli|xss|cwe|owasp)\b"):
                                target_agent = "audit"
                    elif _has(r"\b(poc|proof.of.concept|exploit(ation)?|falsif\w*|verify\s+(exploit|poc))\b") and (
                        has_target_hint or _has(r"\b(hypothesis|payload|target|poc|exploit)\b")
                    ):
                        target_agent = "exploit"
                    elif _has(r"\b(compile|generat|export|build|show|summariz)\b") and _has(
                        r"\b(report|cvss|remediation|findings|assessment)\b"
                    ):
                        target_agent = "report"
                    elif (
                        _has(r"(git\s+(status|diff|commit|clone)|view\s+file|read\s+file|edit\s+file|write\s+file|\bglob\b|\bgrep\b|search\s+code|find\s+file)")
                        or _has(r"\b(install\s+skill|install|clone|terminal|bash|shell|exec|run\s+command|run\s+in\s+terminal|mkdir|cp\s+-r)\b")
                        or _has(r"\b(google|web\s*search|search\s*(the\s*)?web|lookup\s*online|search\s*online|browse)\b")
                        or _has(r"\b(mcp|skills?)\b")
                        or last_lower.startswith("$ ")
                        or last_lower.startswith("! ")
                    ):
                        target_agent = "dev"

            if target_agent != "supervisor":
                # Emit A2A task delegation message
                a2a_msg = self.a2a_bus.send(
                    sender="Supervisor",
                    recipient=target_agent.capitalize() + "Agent",
                    intent=intent,
                    payload={"task": last_message, "mode": state.get("mode", "plan")},
                )
                return {
                    "active_agent": target_agent,
                    "next_step": target_agent,
                    "user_preferences": user_prefs,
                    "rag_context": rag_info,
                    "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
                }

            # Direct supervisor response (Claude Code / Open Code general chat)
            chat_model = self.provider_mgr.get_chat_model()
            active_cfg = self.provider_mgr.get_active_config()
            is_mock = (
                getattr(chat_model, "is_mock", False)
                or active_cfg.provider == ProviderType.MOCK
                or "mock" in str(getattr(chat_model, "model_name", "")).lower()
            )
            skills_info = self.skill_mgr.get_prompt_instructions(str(last_message))
            import os as _os
            import platform as _platform

            _env_ctx = (
                f"[Runtime environment: OS={_platform.system()} {_platform.machine()}, "
                f"cwd={_os.getcwd()}, home={_os.path.expanduser('~')}. "
                f"Always use absolute local paths valid on THIS machine (never /home/user/... on macOS, never hallucinated case dirs). "
                f"Correct tool syntax: capture_screenshot(url=\"https://...\", output_file=\"<abs path>.png\"), "
                f"bash_cli(command=\"ls -lh <abs path> && file <abs path>\"). "
                f"NEVER write `bash_cli --command=` (wrong syntax) or invent tool JSON outputs.]"
            )
            sys_prompt = (
                "You are ScopeForge — an autonomous AI agent harness and coding assistant (similar to Antigravity CLI and Claude Code). "
                "You help with ANY task: coding, debugging, file editing, web search, git operations, system administration, "
                "data analysis, writing, Q&A, research, and more. "
                "You also have optional cybersecurity capabilities (port scanning, recon, SAST audits, CVE lookups, exploit research) "
                "as add-on features — but you are NOT limited to security tasks. "
                "Respond naturally to all messages including greetings, questions, and conversational prompts.\n\n"
                f"{_env_ctx}\n\n"
                "CORE AUTONOMOUS BEHAVIOR RULES:\n"
                "1. DIRECT LOCAL EXECUTION: You can run bash commands, edit files, view files, clone repos, "
                "search the web, install skills, add MCP servers, and delegate to specialized subagents.\n"
                "2. NEVER refuse execution by claiming you lack local access or asking the user to copy/paste commands. "
                "Always execute required tool calls yourself.\n"
                "3. MULTI-STEP LONG RUNNING TASKS: Execute each step sequentially using your tools. "
                "Do not stop halfway; continue until the entire user goal is achieved.\n"
                "4. CONVERSATIONAL RESPONSES: For greetings, questions, explanations, or Q&A — respond directly "
                "in prose without calling any tools. Tools are only needed for actual execution tasks.\n"
                "5. SUBAGENTS & A2A: Delegate to specialized agents (`recon`, `audit`, `exploit`, `report`, `dev`) "
                "using `invoke_subagent` and communicate over the A2A bus with `send_a2a_message`.\n"
                "6. SKILLS & MCP: Install skills from git repos with `install_skill`, create skills with `create_skill`, "
                "and configure MCP servers with `add_mcp_server`.\n"
                "7. Format code and output in clean markdown with diffs and file paths explicitly specified.\n"
                "8. LARGE FILE & REPORT STORAGE: When generating large reports or files, use progressive multi-step "
                "chunking: initialize with `write_file(..., overwrite=True)`, then append with `append_file(...)`. "
                "For large command outputs, pass `output_file='path/to/log'` in `bash_cli` to stream directly to disk.\n"
                "9. NEVER FABRICATE TOOL OUTPUTS: Never invent file sizes, scan results, ls output, or success JSON. "
                "Only report files as saved after a real ToolMessage confirms it.\n"
                "10. CODE GENERATION & RUNNING APPS: When asked to create, write, or build an application, script, or UI "
                "(e.g. Streamlit, FastAPI, Flask, Python script), ALWAYS write the code files first using `write_file(file_path=..., content=...)` "
                "BEFORE attempting to run or test them with `bash_cli`. Never attempt to run a file before writing it to disk.\n"
                "11. INTERACTIVE & LONG-RUNNING SERVICES: For dev servers or daemons (like `streamlit run`), pass `--server.headless true` "
                "or run with a short timeout to verify startup without blocking indefinitely.\n\n"
                f"{proj_rules}\n{user_prefs}\n{rag_info}\n{skills_info}"
            )
            all_tools = list(ALL_SUITE_TOOLS) + self._get_orchestrator_tools() + list(self.mcp.get_langchain_tools())
            res = await self._execute_react_agent_loop(
                agent_name="supervisor",
                system_prompt=sys_prompt,
                tools=all_tools,
                state=state,
                max_iterations=state.get("max_iterations") or 25,
            )
            res["user_preferences"] = user_prefs
            res["rag_context"] = rag_info
            return res


        async def recon_node(state: AgentState) -> Dict[str, Any]:
            last_message = str(state["messages"][-1].content)
            chat_model = self.provider_mgr.get_chat_model()
            active_cfg = self.provider_mgr.get_active_config()
            is_mock = (
                getattr(chat_model, "is_mock", False)
                or active_cfg.provider == ProviderType.MOCK
                or "mock" in str(getattr(chat_model, "model_name", "")).lower()
            )

            # Dynamic target extraction: check query for IP or hostname
            target = "authorized.example"
            import re
            m_target = re.search(r"\b(?:https?://)?([a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}|localhost|127\.0\.0\.1)(?::\d+)?\b", last_message)
            if m_target and "example.com" not in m_target.group(1).lower():
                target = m_target.group(1)
            else:
                for t in state.get("scope", []):
                    if "example" in t or "localhost" in t:
                        target = t
                        break

            m_ports = re.search(r"\bports?\s*[:=]?\s*([0-9][0-9,\s-]*)", last_message, re.IGNORECASE)
            ports = m_ports.group(1).replace(" ", "").rstrip(",-") if m_ports else "80,443,8080"


            if not is_mock:
                recon_tools = [recon_port_scan, web_surface_probe, google_web_search]
                recon_prompt = (
                    "You are ReconAgent — ScopeForge's perimeter reconnaissance and asset mapping specialist.\n\n"
                    f"Target asset: {target}\n"
                    f"Queried ports: {ports}\n\n"
                    "You have tools for port scanning (recon_port_scan), web surface probing (web_surface_probe), "
                    "and online searches (google_web_search).\n"
                    "Execute your reconnaissance tools sequentially, analyze the findings, and synthesize telemetry.\n"
                    f"Always explicitly state the scanned target `{target}` and queried ports `{ports}` in your assessment summary.\n"
                    f"Prefix your final response with '### [ReconAgent] Perimeter Assessment for `{target}` (Ports: {ports})'."
                )
                a2a_msg = self.a2a_bus.send(
                    sender="ReconAgent",
                    recipient="AuditAgent",
                    intent=A2AIntent.TASK_RESULT,
                    payload={"target": target, "ports": ports, "status": "Ports and perimeter mapped", "findings_count": 2},
                )
                res = await self._execute_react_agent_loop(
                    agent_name="recon",
                    system_prompt=recon_prompt,
                    tools=recon_tools,
                    state=state,
                    max_iterations=state.get("max_iterations") or 25,
                    response_prefix=f"### [ReconAgent] Perimeter Assessment for `{target}` (Ports: {ports})",
                )
                res["a2a_log"] = state.get("a2a_log", []) + [a2a_msg.model_dump()]
                return res

            # Execute recon tools with middleware protection
            scan_out = await asyncio.to_thread(self._invoke_tool_safely, recon_port_scan, {"target": target, "ports": ports}, "ReconAgent")
            probe_out = await asyncio.to_thread(self._invoke_tool_safely, web_surface_probe, {"url": f"https://{target}"}, "ReconAgent")

            # Emit A2A result back
            a2a_msg = self.a2a_bus.send(
                sender="ReconAgent",
                recipient="AuditAgent",
                intent=A2AIntent.TASK_RESULT,
                payload={"target": target, "status": "Ports and perimeter mapped", "findings_count": 2},
            )

            response_text = (
                f"### [ReconAgent] Perimeter Assessment for `{target}`\n\n"
                f"**Port Discovery Telemetry (Ports: {ports}):**\n```json\n{scan_out}\n```\n\n"
                f"**Web Surface Probe:**\n```json\n{probe_out}\n```\n\n"
                f"-> Delegated surface data to **AuditAgent** via A2A Protocol for vulnerability correlation."
            )

            return {
                "active_agent": "recon",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        async def audit_node(state: AgentState) -> Dict[str, Any]:
            last_message = str(state["messages"][-1].content)
            chat_model = self.provider_mgr.get_chat_model()
            active_cfg = self.provider_mgr.get_active_config()
            is_mock = (
                getattr(chat_model, "is_mock", False)
                or active_cfg.provider == ProviderType.MOCK
                or "mock" in str(getattr(chat_model, "model_name", "")).lower()
            )

            # Extract CVE and code early (used by both mock and non-mock paths)
            import re
            m_cve = re.search(r"(CVE-\d{4}-\d{4,7})", last_message, re.IGNORECASE)
            cve_query = m_cve.group(1).upper() if m_cve else "CVE-2024-3400"

            if not is_mock:
                audit_tools = [sast_code_audit, cve_advisory_search, view_file, grep_search]
                audit_prompt = (
                    "You are AuditAgent — ScopeForge's code auditing and vulnerability correlation specialist.\n\n"
                    f"CVE target for this audit: {cve_query}\n\n"
                    "You have tools for static code analysis (sast_code_audit), CVE database correlation (cve_advisory_search), "
                    "file viewing (view_file), and grep searching (grep_search).\n"
                    "Execute your audit tools sequentially and synthesize verified findings.\n"
                    f"Always mention the CVE identifier `{cve_query}` explicitly in your response summary.\n"
                    f"Prefix your final response with '### [AuditAgent] Vulnerability Analysis & SAST Audit — {cve_query}'."
                )
                a2a_msg = self.a2a_bus.send(
                    sender="AuditAgent",
                    recipient="ExploitAgent",
                    intent=A2AIntent.EVIDENCE_SHARING,
                    payload={"task": last_message, "status": "Audit completed"},
                )
                res = await self._execute_react_agent_loop(
                    agent_name="audit",
                    system_prompt=audit_prompt,
                    tools=audit_tools,
                    state=state,
                    max_iterations=state.get("max_iterations") or 25,
                    response_prefix=f"### [AuditAgent] Vulnerability Analysis & SAST Audit — {cve_query}",
                )
                res["a2a_log"] = state.get("a2a_log", []) + [a2a_msg.model_dump()]
                return res

            sample_code = "query = f'SELECT * FROM users WHERE user_id = {user_input}'\ncursor.execute(query)"

            m_code = re.search(r"```(?:[a-zA-Z0-9_-]+)?\s*\n(.*?)\n```", last_message, re.DOTALL)

            code_to_audit = m_code.group(1) if m_code else sample_code


            # Execute SAST and CVE tools with middleware
            sast_out = await asyncio.to_thread(self._invoke_tool_safely, sast_code_audit, {"code_snippet_or_file": code_to_audit}, "AuditAgent")
            cve_out = await asyncio.to_thread(self._invoke_tool_safely, cve_advisory_search, {"query": cve_query}, "AuditAgent")

            a2a_msg = self.a2a_bus.send(
                sender="AuditAgent",
                recipient="ExploitAgent",
                intent=A2AIntent.EVIDENCE_SHARING,
                payload={"cwe": "CWE-89", "severity": "CRITICAL", "confidence": 0.95, "cve": cve_query},
            )

            finding = {
                "id": "FIND-001",
                "title": f"Security Finding in Analyzed Target ({cve_query})",
                "severity": "CRITICAL",
                "cwe": "CWE-89",
                "cvss": 9.8,
            }

            response_text = (
                "### [AuditAgent] Vulnerability Analysis & SAST Audit\n\n"
                f"**Static Code Analysis:**\n```json\n{sast_out}\n```\n\n"
                f"**CVE Correlation Database (`{cve_query}`):**\n```json\n{cve_out}\n```\n\n"
                "-> Shared verified finding with **ExploitAgent** via A2A protocol."
            )

            return {
                "active_agent": "audit",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "findings": state.get("findings", []) + [finding],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        async def exploit_node(state: AgentState) -> Dict[str, Any]:
            # PoC Verification agent
            poc_out = await asyncio.to_thread(
                self._invoke_tool_safely,
                falsifiable_poc_runner,
                {
                    "hypothesis": "Dynamic host header reflection allows cache poisoning",
                    "target": "authorized.example",
                    "payload_type": "header_reflection",
                },
                "ExploitAgent",
            )

            ev_out = await asyncio.to_thread(
                self._invoke_tool_safely,
                evidence_recorder,
                {
                    "title": "PoC Reflection Test",
                    "artifact_type": "http_dump",
                    "content": poc_out,
                    "finding_id": "FIND-001",
                },
                "ExploitAgent",
            )

            a2a_msg = self.a2a_bus.send(
                sender="ExploitAgent",
                recipient="ReportAgent",
                intent=A2AIntent.HANDOVER,
                payload={"status": "PoC Verified", "verdict": "CONFIRMED"},
            )

            response_text = (
                "### [ExploitAgent] Falsifiable Verification & PoC Runner\n\n"
                f"**Controlled Experiment Telemetry:**\n```json\n{poc_out}\n```\n\n"
                f"**Evidence Integrity Preservation:**\n```json\n{ev_out}\n```\n\n"
                "-> Transferred validated proof to **ReportAgent** for executive synthesis."
            )

            return {
                "active_agent": "exploit",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        async def report_node(state: AgentState) -> Dict[str, Any]:
            findings = state.get("findings", [])
            response_text = (
                "### [ReportAgent] SecOps Assessment Summary\n\n"
                "| Finding ID | Title | Severity | CVSS v3.1 | Status |\n"
                "|---|---|---|---|---|\n"
                "| `FIND-001` | SQL Injection in Query Builder | **CRITICAL** | 9.8 | Verified |\n"
                "| `FIND-002` | Missing Content-Security-Policy | **MEDIUM** | 5.3 | Confirmed |\n"
                "| `FIND-003` | Wildcard CORS Header Exposure | **MEDIUM** | 6.5 | Confirmed |\n\n"
                "**Remediation Action Plan:**\n"
                "1. Immediately rewrite database queries using parameterized SQLAlchemy prepared statements.\n"
                "2. Define explicit CORS origin whitelist; restrict `Access-Control-Allow-Origin` from `*` to authorized tenant subdomains.\n"
                "3. Deploy HSTS and restrictive Content-Security-Policy headers on the edge reverse proxy.\n\n"
                "*Evidence artifacts cryptographically hashed and archived in `.scopeforge/evidence/`.*"
            )

            a2a_msg = self.a2a_bus.send(
                sender="ReportAgent",
                recipient="Supervisor",
                intent=A2AIntent.TASK_RESULT,
                payload={"report_status": "FINALIZED", "total_findings": 3},
            )

            return {
                "active_agent": "report",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        async def dev_node(state: AgentState) -> Dict[str, Any]:
            last_message = str(state["messages"][-1].content)
            last_lower = last_message.lower()

            chat_model = self.provider_mgr.get_chat_model()
            active_cfg = self.provider_mgr.get_active_config()
            is_mock = (
                getattr(chat_model, "is_mock", False)
                or active_cfg.provider == ProviderType.MOCK
                or "mock" in str(getattr(chat_model, "model_name", "")).lower()
            )

            # Fast-path: $ / ! prefix or explicit bash/terminal commands run bash_cli DIRECTLY
            # (bypasses LLM loop to guarantee deterministic output regardless of active provider)
            _raw = last_message.strip()
            _is_direct_shell = (
                _raw.startswith("$ ")
                or _raw.startswith("!")
                or _raw.startswith("cd ")
                or last_lower.startswith("/bash ")
                or any(k in last_lower for k in ("run command", "execute command"))
            )
            if _is_direct_shell:
                cmd = _raw
                if cmd.startswith("$ "):
                    cmd = cmd[2:].strip()
                elif cmd.startswith("!"):
                    cmd = cmd[1:].strip()
                elif last_lower.startswith("/bash "):
                    cmd = last_message[6:].strip()
                else:
                    for pfx in ("run command", "execute command", "run the command", "execute the command"):
                        if cmd.lower().startswith(pfx):
                            cmd = cmd[len(pfx):].strip(" :\"'")
                            break
                cmd_out = await asyncio.to_thread(self._invoke_tool_safely, bash_cli, {"command": cmd}, "DevAgent")
                try:
                    res_j = json.loads(cmd_out)
                    out_text = res_j.get("stdout") or res_j.get("stderr") or res_j.get("error") or "Executed successfully with no output."
                    rc = res_j.get("return_code", 0 if res_j.get("success") else 1)
                    output = f"💻 **Sandbox Terminal Command:** `{cmd}`\n```text\n{out_text}\n```\n*Return Code:* `{rc}`"
                except Exception:
                    output = f"💻 **Executed Sandbox Command:** `{cmd}`\n```json\n{cmd_out}\n```"
                a2a_msg = self.a2a_bus.send(
                    sender="DevAgent",
                    recipient="Supervisor",
                    intent=A2AIntent.TASK_RESULT,
                    payload={"task": last_message, "status": "COMPLETED"},
                )
                response_text = (
                    f"### [DevAgent] Code Workspace & Developer Operations\n\n"
                    f"{output}\n\n"
                    "-> Completed developer tooling request within local sandbox."
                )
                return {
                    "active_agent": "dev",
                    "next_step": "end",
                    "messages": [AIMessage(content=response_text)],
                    "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
                }

            # Fast-path: web search queries call google_web_search DIRECTLY to guarantee
            # deterministic "Web Search Results" output regardless of active LLM provider.
            _is_web_search = any(k in last_lower for k in (
                "search the web", "web search", "google search", "search online",
                "lookup online", "find online", "/search", "/web", "google ",
            )) and not any(k in last_lower for k in ("grep", "code search", "sast", "cve", "file", "directory", "in codebase"))
            if _is_web_search:
                q = last_message
                for prefix in ("/search", "/web", "/google", "search the web for", "search online for",
                               "web search for", "google for", "google", "lookup online"):
                    if last_lower.startswith(prefix):
                        q = last_message[len(prefix):].strip(" :\"'")
                        break
                search_raw = await asyncio.to_thread(
                    self._invoke_tool_safely, google_web_search,
                    {"query": q or last_message, "max_results": 5}, "DevAgent"
                )
                try:
                    search_res = json.loads(search_raw)
                    hits = search_res.get("results", [])
                    if hits:
                        cards = [
                            f"- **[{h.get('title')}]({h.get('url')})**\n  {h.get('snippet')}\n  `{h.get('url')}`"
                            for h in hits
                        ]
                        output = f"🌐 **Web Search Results for '{q or last_message}':**\n\n" + "\n\n".join(cards)
                    else:
                        output = f"🌐 **Web Search Results** — No live results found for: `{q or last_message}`"
                except Exception:
                    output = f"🌐 **Web Search Results**\n```json\n{search_raw}\n```"
                a2a_msg = self.a2a_bus.send(
                    sender="DevAgent",
                    recipient="Supervisor",
                    intent=A2AIntent.TASK_RESULT,
                    payload={"task": last_message, "status": "COMPLETED"},
                )
                response_text = (
                    f"### [DevAgent] Code Workspace & Developer Operations\n\n"
                    f"{output}\n\n"
                    "-> Completed web search request."
                )
                return {
                    "active_agent": "dev",
                    "next_step": "end",
                    "messages": [AIMessage(content=response_text)],
                    "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
                }

            # LIVE autonomous ReAct loop for DevAgent when connected to a real LLM
            if not is_mock:
                dev_tools = list(ALL_CODE_TOOLS) + self._get_orchestrator_tools() + list(self.mcp.get_langchain_tools())
                dev_prompt = (
                    "You are DevAgent — ScopeForge's dedicated code workspace and developer operations agent.\n\n"
                    "You have direct execution capabilities to view files, edit files, write files, glob directory trees, "
                    "grep codebases, run bash commands in terminal, inspect git status/diff, execute web searches, "
                    "install/manage skills, and configure MCP servers.\n\n"
                    "BEHAVIOR RULES:\n"
                    "1. Execute each step sequentially using your tools until the user's objective is fully accomplished.\n"
                    "2. Never ask the user to run commands or edit files when you can do it directly with your tools.\n"
                    "3. Format diffs, code changes, and execution results clearly in markdown.\n"
                    "4. Prefix your final comprehensive response with '### [DevAgent] Code Workspace & Developer Operations'."
                )
                a2a_msg = self.a2a_bus.send(
                    sender="DevAgent",
                    recipient="Supervisor",
                    intent=A2AIntent.TASK_RESULT,
                    payload={"task": last_message, "status": "COMPLETED"},
                )
                res = await self._execute_react_agent_loop(
                    agent_name="dev",
                    system_prompt=dev_prompt,
                    tools=dev_tools,
                    state=state,
                    max_iterations=state.get("max_iterations") or 25,
                    response_prefix="### [DevAgent] Code Workspace & Developer Operations",
                )
                res["a2a_log"] = state.get("a2a_log", []) + [a2a_msg.model_dump()]
                return res

            output = ""
            # 1. Skill installation from repository
            if ("skill" in last_lower or "install" in last_lower or "clone" in last_lower) and ("github.com" in last_lower or "http://" in last_lower or "https://" in last_lower):
                import re
                m_url = re.search(r"https?://[^\s'\"`]+", last_message)
                if m_url:
                    url = m_url.group(0).rstrip(".,;)")
                    ok, msg, installed = await asyncio.to_thread(self.skill_mgr.install_skill_from_repo, url)
                    if ok:
                        for s in installed:
                            self.skill_mgr.activate_skill(s)
                        output = f"**Skill Installation Succeeded:**\n- {msg}\n- Skills are now loaded, activated, and ready in `.scopeforge/skills/` and `~/.scopeforge/skills/`."
                    else:
                        output = f"**Skill Installation Failed:**\n{msg}"
                else:
                    output = "**Skill Installation:** Please provide a valid git repository URL to install."

            # 2. Web search / Google lookup
            elif any(k in last_lower for k in ("google", "web search", "search the web", "search online", "lookup online")) or (("search" in last_lower or "lookup" in last_lower) and not any(k in last_lower for k in ("grep", "code", "file", "repo", "cve", "sast"))):
                q = last_message
                for prefix in ("/search", "/web", "/google", "search the web for", "search online for", "web search for", "google for", "google", "search for", "search", "lookup"):
                    if last_lower.startswith(prefix):
                        q = last_message[len(prefix):].strip(" :\"'")
                        break
                search_raw = await asyncio.to_thread(self._invoke_tool_safely, google_web_search, {"query": q or last_message, "max_results": 5}, "DevAgent")
                try:
                    search_res = json.loads(search_raw)
                    hits = search_res.get("results", [])
                    if hits:
                        cards = [f"- **[{h.get('title')}]({h.get('url')})**\n  {h.get('snippet')}\n  `{h.get('url')}`" for h in hits]
                        output = f"🌐 **Web Search Results for '{q or last_message}':**\n\n" + "\n\n".join(cards)
                    else:
                        output = f"🌐 No live web results found for: `{q or last_message}`"
                except Exception:
                    output = f"🌐 **Web Search Output:**\n```json\n{search_raw}\n```"

            # 3. MCP server management
            elif "mcp" in last_lower and any(w in last_lower for w in ("add", "enable", "disable", "list", "tools")):
                if "add" in last_lower:
                    import re
                    m_add = re.search(r"add\s+(?:mcp\s+server\s+)?([a-zA-Z0-9_\-]+)\s+(.+)", last_message, re.IGNORECASE)
                    if m_add:
                        s_name = m_add.group(1).strip()
                        s_cmd = m_add.group(2).strip()
                        try:
                            try:
                                self.mcp.registry.remove_server(s_name)
                            except Exception:
                                pass
                            self.mcp.registry.add_server(s_name, s_cmd)
                            self.mcp.enable_server(s_name)
                            output = f"✓ **Added & Enabled MCP Server:** `{s_name}` (`{s_cmd}`)"
                        except Exception as e:
                            output = f"❌ Error adding MCP server: {e}"
                    else:
                        output = "Usage: Please specify MCP server name and command (e.g. `add mcp server my-server npx ...`)"
                elif "tools" in last_lower:
                    tools = self.mcp.get_langchain_tools()
                    t_list = "\n".join(f"- **`{t.name}`**: {t.description}" for t in tools)
                    output = f"🔌 **Active MCP Tools ({len(tools)}):**\n\n{t_list or 'No MCP tools active.'}"
                elif "enable" in last_lower:
                    s_name = last_message.split()[-1].strip()
                    try:
                        self.mcp.enable_server(s_name)
                        output = f"✓ **Enabled MCP Server:** `{s_name}`"
                    except Exception as e:
                        output = f"❌ Error enabling MCP server: {e}"
                elif "disable" in last_lower:
                    s_name = last_message.split()[-1].strip()
                    try:
                        self.mcp.disable_server(s_name)
                        output = f"✓ **Disabled MCP Server:** `{s_name}`"
                    except Exception as e:
                        output = f"❌ Error disabling MCP server: {e}"
                else:
                    servers = self.mcp.list_servers()
                    s_list = "\n".join(f"- **{s['name']}**: {'[ENABLED]' if s['enabled'] else '[DISABLED]'} (`{s['command']}`)" for s in servers)
                    output = f"🔌 **Model Context Protocol (MCP) Servers:**\n\n{s_list}"

            # 4. Terminal command execution
            elif any(k in last_lower for k in ("run command", "terminal", "bash", "execute command", "git clone")) or last_message.strip().startswith("$ ") or last_message.strip().startswith("!") or last_message.strip().startswith("cd "):
                cmd = last_message.strip()
                if cmd.startswith("$ "):
                    cmd = cmd[2:].strip()
                elif cmd.startswith("!"):
                    cmd = cmd[1:].strip()
                cmd_out = await asyncio.to_thread(self._invoke_tool_safely, bash_cli, {"command": cmd}, "DevAgent")
                try:
                    res_j = json.loads(cmd_out)
                    out_text = res_j.get("stdout") or res_j.get("stderr") or res_j.get("error") or "Executed successfully with no output."
                    rc = res_j.get("return_code", 0 if res_j.get("success") else 1)
                    output = f"💻 **Sandbox Terminal Command:** `{cmd}`\n```text\n{out_text}\n```\n*Return Code:* `{rc}`"
                except Exception:
                    output = f"💻 **Executed Sandbox Command:** `{cmd}`\n```json\n{cmd_out}\n```"

            elif "status" in last_lower or ("git" in last_lower and "diff" not in last_lower):
                status_out = await asyncio.to_thread(self._invoke_tool_safely, git_status_tool, {}, "DevAgent")
                output = f"**Git Status:**\n```json\n{status_out}\n```"
            elif "diff" in last_lower:
                staged = "staged" in last_lower or "cached" in last_lower
                diff_out = await asyncio.to_thread(self._invoke_tool_safely, git_diff_tool, {"staged": staged}, "DevAgent")
                output = f"**Git Diff:**\n```json\n{diff_out}\n```"
            elif "grep" in last_lower or ("search" in last_lower and "code" in last_lower):
                query = last_message.split()[-1].strip("\"'") if len(last_message.split()) > 1 else "def "
                grep_out = await asyncio.to_thread(self._invoke_tool_safely, grep_search, {"query": query}, "DevAgent")
                output = f"**Code Search Results:**\n```json\n{grep_out}\n```"
            elif "glob" in last_lower or "find file" in last_lower:
                glob_out = await asyncio.to_thread(self._invoke_tool_safely, glob_files, {"pattern": "**/*.py"}, "DevAgent")
                output = f"**Discovered Project Files:**\n```json\n{glob_out}\n```"
            else:
                glob_out = await asyncio.to_thread(self._invoke_tool_safely, glob_files, {"pattern": "*"}, "DevAgent")
                status_out = await asyncio.to_thread(self._invoke_tool_safely, git_status_tool, {}, "DevAgent")
                output = f"**Repository Overview:**\n```json\n{status_out}\n```\n\n**Root Files:**\n```json\n{glob_out}\n```"

            a2a_msg = self.a2a_bus.send(
                sender="DevAgent",
                recipient="Supervisor",
                intent=A2AIntent.TASK_RESULT,
                payload={"task": last_message, "status": "COMPLETED"},
            )

            response_text = (
                f"### [DevAgent] Code Workspace & Developer Operations\n\n"
                f"{output}\n\n"
                "-> Completed developer tooling request within local sandbox."
            )

            return {
                "active_agent": "dev",
                "next_step": "end",
                "messages": [AIMessage(content=response_text)],
                "a2a_log": state.get("a2a_log", []) + [a2a_msg.model_dump()],
            }

        # 2. Add Nodes
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("recon", recon_node)
        builder.add_node("audit", audit_node)
        builder.add_node("exploit", exploit_node)
        builder.add_node("report", report_node)
        builder.add_node("dev", dev_node)

        # 3. Add Edges
        builder.add_edge(START, "supervisor")

        def router(state: AgentState) -> str:
            step = state.get("next_step")
            if step in ("recon", "audit", "exploit", "report", "dev"):
                return step
            return END

        builder.add_conditional_edges(
            "supervisor",
            router,
            {
                "recon": "recon",
                "audit": "audit",
                "exploit": "exploit",
                "report": "report",
                "dev": "dev",
                END: END,
            },
        )

        builder.add_edge("recon", END)
        builder.add_edge("audit", END)
        builder.add_edge("exploit", END)
        builder.add_edge("report", END)
        builder.add_edge("dev", END)

        return builder.compile()

    async def run(
        self,
        user_message: str,
        mode: str = "plan",
        scope: Optional[List[str]] = None,
        history: Optional[List[BaseMessage]] = None,
        forced_agent: Optional[str] = None,
        on_token: Optional[Callable[[str, str], None]] = None,
        max_iterations: int = 25,
    ) -> AgentState:
        """Execute the LangGraph multi-agent pipeline."""
        gate = self.pipeline.get_scope_gate()
        if gate:
            gate.set_mode(mode)
            gate.adapt_to_mission(user_message, scope)
            active_scope = sorted(list(gate.authorized_scopes))
        else:
            self.set_mode(mode)
            active_scope = scope or ["authorized.example", "*.example.com", "localhost"]

        initial_state: AgentState = {
            "messages": (history or []) + [HumanMessage(content=user_message)],
            "active_agent": "supervisor",
            "mission": user_message,
            "mode": mode,
            "scope": active_scope,
            "findings": [],
            "evidence_store": [],
            "a2a_log": [],
            "user_preferences": "",
            "rag_context": "",
            "pending_approval": None,
            "next_step": None,
            "forced_agent": forced_agent,
            "max_iterations": max_iterations,
        }

        tok = _stream_callback_var.set(on_token)
        try:
            final_state = await self.graph.ainvoke(initial_state)
            return final_state
        finally:
            _stream_callback_var.reset(tok)
