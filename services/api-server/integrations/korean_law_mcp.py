"""HTTP bridge for Korean Law MCP with an official Law.go.kr fallback.

The API server does not embed an MCP server. It calls a configured MCP bridge
or the official Law.go.kr search endpoint and records every failure explicitly.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict

from agents.korean_law_context import build_korean_law_mcp_query_plan


def run_korean_law_mcp(project_data: Dict[str, Any], *, timeout: float = 10.0, environ: Dict[str, str] | None = None) -> Dict[str, Any]:
    env = environ or os.environ
    bridge_url = (env.get("REALYTICS_KOREAN_LAW_MCP_URL") or "").strip()
    law_api_url = (env.get("REALYTICS_KOREAN_LAW_API_URL") or "https://www.law.go.kr/DRF/lawService.do").strip()
    law_oc = (env.get("LAW_OC") or env.get("REALYTICS_LAW_OC") or "").strip()
    plan = build_korean_law_mcp_query_plan(project_data)
    tool_results = []
    diagnostics = []
    for query in plan:
        if bridge_url:
            result = _call_bridge(bridge_url, query, timeout)
        elif law_oc:
            result = _call_law_api(law_api_url, query, law_oc, timeout)
        else:
            result = {"status": "skipped_missing_config", "message": "REALYTICS_KOREAN_LAW_MCP_URL or LAW_OC is not configured."}
        diagnostics.append({"tool": query.get("tool"), **{key: value for key, value in result.items() if key != "result"}})
        if result.get("result") is not None:
            tool_results.append({"tool": query.get("tool"), "result": result["result"]})
    return {
        "version": "realytics-korean-law-mcp-bridge-v1",
        "source": "korean-law-mcp" if tool_results else "not_connected",
        "connected": bool(tool_results),
        "transport": "mcp_http_bridge" if bridge_url else "law_go_kr_api" if law_oc else "unconfigured",
        "queryPlan": plan,
        "tool_results": tool_results,
        "diagnostics": diagnostics,
        "failureReason": None if tool_results else next((item.get("message") for item in diagnostics if item.get("message")), "No Korean Law MCP result was returned."),
    }


def _call_bridge(url: str, query: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    body = json.dumps({"tool": query.get("tool"), "arguments": {"query": query.get("query"), "purpose": query.get("purpose")}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "REALYTICS/legal-pod-v1"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status": "connected", "result": json.loads(response.read().decode("utf-8"))}
    except Exception as exc:
        return {"status": "bridge_failed", "message": f"Korean Law MCP bridge failed: {type(exc).__name__}: {exc}"}


def _call_law_api(url: str, query: Dict[str, Any], oc: str, timeout: float) -> Dict[str, Any]:
    params = {"OC": oc, "target": "law", "type": "JSON", "query": query.get("query", ""), "display": "5"}
    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        request = urllib.request.Request(request_url, headers={"User-Agent": "REALYTICS/legal-pod-v1"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        law_search = payload.get("LawSearch") if isinstance(payload, dict) else None
        laws = law_search.get("law", []) if isinstance(law_search, dict) else []
        return {"status": "connected", "result": {"laws": laws, "raw": payload}}
    except Exception as exc:
        return {"status": "law_api_failed", "message": f"Law.go.kr lookup failed: {type(exc).__name__}: {exc}"}
