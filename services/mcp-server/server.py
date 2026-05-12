import asyncio
import os
import json
from typing import Optional, Dict, Any, List
from mcp.server.stdio import stdio_server
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.types as types
from dotenv import load_dotenv

# Import logic from api-server
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "api-server"))

from core.calculations import (
    calculate_development_profit, calculate_profit_margin, 
    calculate_ltv, calculate_ltc, calculate_exit_sale_rate
)
from core.risk_assessment import assess_risk_item, calculate_overall_risk
from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from agents.ai_connection import resolve_ai_context
from agents.korean_law_context import build_korean_law_mcp_query_plan
from agents.legal_review_agent import LegalReviewAgent
from agents.marketability_agent import MarketabilityAgent
from agents.orchestration_agent import OrchestrationAgent
from agents.parser_agent import ParserAgent
from agents.report_agent import ReportAgent

load_dotenv()

server = Server("realytics-pf-mcp")


def _ai_schema_properties() -> Dict[str, Any]:
    return {
        "user_id": {
            "type": "string",
            "description": "End-user identifier to attach to AI/MCP runs and audit metadata.",
        },
        "ai_provider": {
            "type": "string",
            "description": "AI provider hint: claude_desktop for host LLM mode, anthropic for direct Claude API, or openai.",
        },
        "ai_context": {
            "type": "object",
            "description": "Optional AI connection context with provider, model, host, and user_id.",
        },
    }


def _with_ai_schema(properties: Dict[str, Any]) -> Dict[str, Any]:
    return {**properties, **_ai_schema_properties()}


def _build_mcp_ai_context(arguments: Dict[str, Any]) -> Dict[str, Any]:
    context = dict(arguments.get("ai_context") or {})
    if arguments.get("user_id"):
        context["user_id"] = arguments["user_id"]
    if arguments.get("ai_provider"):
        context["provider"] = arguments["ai_provider"]
    context.setdefault("provider", os.environ.get("REALYTICS_AI_PROVIDER", "claude_desktop"))
    context.setdefault("host", "claude_desktop")
    context.setdefault(
        "user_id",
        os.environ.get("MCP_USER_ID")
        or os.environ.get("REALYTICS_USER_ID")
        or "claude-desktop-user",
    )
    return resolve_ai_context(context, host="claude_desktop")

@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    """List available tools for PF analysis."""
    return [
        types.Tool(
            name="calculate_pf_metrics",
            description="PF 수지분석 기초 지표(LTV, LTC, Profit, Exit Sale Rate)를 계산합니다.",
            inputSchema={
                "type": "object",
                "properties": _with_ai_schema({
                    "total_revenue": {"type": "number", "description": "총 분양 수입 (원)"},
                    "total_cost": {"type": "number", "description": "총 사업비 (원)"},
                    "pf_amount": {"type": "number", "description": "총 PF 조달 금액 (원)"},
                }),
                "required": ["total_revenue", "total_cost", "pf_amount"],
            },
        ),
        types.Tool(
            name="get_professional_sample",
            description="실제 대구 본동 주상복합 PF 등 고도화된 실무 샘플 데이터를 반환합니다. 이를 학습하여 고품질 보고서를 작성하세요.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="resolve_ai_connection",
            description="Resolve the active AI provider, Claude Desktop MCP host mode, model, and linked user_id for audit metadata.",
            inputSchema={"type": "object", "properties": _ai_schema_properties()},
        ),
        types.Tool(
            name="analyze_business_plan",
            description="PDF 사업계획서에서 PF 관련 핵심 데이터를 추출합니다.",
            inputSchema={
                "type": "object",
                "properties": _with_ai_schema({
                    "file_path": {"type": "string", "description": "분석할 PDF 파일 경로"},
                }),
                "required": ["file_path"],
            },
        ),
        types.Tool(
            name="generate_pf_report",
            description="데이터를 바탕으로 20개 섹션의 전문 PF 심사보고서 구조화 JSON과 마크다운을 생성합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_data": {"type": "object", "description": "사업개요, 수지, PF구조, Covenants 등이 포함된 전체 데이터 객체"},
                },
                "required": ["project_data"],
            },
        ),
        types.Tool(
            name="orchestrate_pf_review",
            description="프로젝트 데이터 로드, 디벨로퍼 사업수지분석, 사업성 계산, PF 구조 계산, 분양성 검증, 법률/인허가 예비 검토, 리스크 평가, Phase 11 보고서 생성, 품질 점검을 하나의 실행 run으로 수행합니다.",
            inputSchema={
                "type": "object",
                "properties": _with_ai_schema({
                    "project_id": {"type": "string", "description": "프로젝트 ID"},
                    "project_data": {"type": "object", "description": "오케스트레이션에 사용할 프로젝트 전체 데이터"},
                    "options": {"type": "object", "description": "실행 옵션"},
                }),
                "required": ["project_id", "project_data"],
            },
        ),
        types.Tool(
            name="analyze_development_feasibility",
            description="사업계획서/건축 기본계획에서 추출된 데이터를 바탕으로 디벨로퍼 관점 사업수지와 PF 심사용 조달 가능성을 분석합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_data": {"type": "object", "description": "토지, 건축계획, 분양가정, 비용가정, PF 구조가 포함된 프로젝트 데이터"},
                },
                "required": ["project_data"],
            },
        ),
        types.Tool(
            name="analyze_marketability",
            description="아파트, 오피스텔, 지식산업센터, 주상복합 등 분양형 부동산의 분양성을 검증합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_data": {"type": "object", "description": "분양성 검증 대상 프로젝트 데이터"},
                },
                "required": ["project_data"],
            },
        ),
        types.Tool(
            name="get_korean_law_mcp_query_plan",
            description="REALYTICS 프로젝트 데이터에 맞춰 Korean Law MCP sidecar에서 조회해야 할 법령/판례/인허가 쿼리 계획을 반환합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_data": {"type": "object", "description": "법령 조회 계획을 만들 프로젝트 데이터"},
                },
                "required": ["project_data"],
            },
        ),
        types.Tool(
            name="review_korean_real_estate_law",
            description="국내 부동산 PF의 법률/인허가/권리관계/Covenant를 예비 검토합니다. Korean Law MCP sidecar 조회 결과를 law_context로 전달하세요.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_data": {"type": "object", "description": "검토 대상 프로젝트 데이터"},
                    "law_context": {"type": "object", "description": "Korean Law MCP가 반환한 법령/인허가 근거 컨텍스트"},
                },
                "required": ["project_data"],
            },
        ),
        types.Tool(
            name="assess_project_risk",
            description="사업 리스크 항목들을 평가하고 종합 등급을 산출합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "risk_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "category": {"type": "string"},
                                "item": {"type": "string"},
                                "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                                "probability": {"type": "integer", "minimum": 1, "maximum": 5},
                            },
                            "required": ["category", "item", "severity", "probability"]
                        }
                    }
                },
                "required": ["risk_items"],
            },
        ),
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: Optional[Dict[str, Any]]
) -> List[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Execute the requested tool."""
    if name == "get_professional_sample":
        sample_path = os.path.join(os.path.dirname(__file__), "..", "api-server", "data", "examples", "daegu_bondong_mixed_use_pf.json")
        try:
            with open(sample_path, "r", encoding="utf-8") as f:
                sample_data = json.load(f)
            return [types.TextContent(type="text", text=json.dumps(sample_data, ensure_ascii=False, indent=2))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error loading sample: {str(e)}")]

    arguments = arguments or {}

    if name == "resolve_ai_connection":
        result = {
            "ai_connection": _build_mcp_ai_context(arguments),
            "note": (
                "Claude Desktop is the MCP host LLM. In claude_desktop mode the server "
                "returns extracted text and instructions for Claude to transform. Use "
                "ai_provider=anthropic with ANTHROPIC_API_KEY only when the server itself "
                "must perform direct model extraction."
            ),
        }
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "calculate_pf_metrics":
        rev = arguments["total_revenue"]
        cost = arguments["total_cost"]
        pf = arguments["pf_amount"]
        
        profit = calculate_development_profit(rev, cost)
        margin = calculate_profit_margin(profit, rev)
        ltv = calculate_ltv(pf, rev)
        ltc = calculate_ltc(pf, cost)
        exit_rate = calculate_exit_sale_rate(pf, 0, rev)
        
        result = {
            "development_profit": profit,
            "profit_margin": margin,
            "ltv": ltv,
            "ltc": ltc,
            "exit_sale_rate": exit_rate
        }
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    elif name == "analyze_business_plan":
        path = arguments["file_path"]
        if not os.path.exists(path):
            return [types.TextContent(type="text", text=f"Error: File not found at {path}")]
            
        ai_context = _build_mcp_ai_context(arguments)
        agent = ParserAgent(ai_context)
        text = agent.parse_document(path)
        data = agent.extract_project_data(text, ai_context)
        if data.get("_meta", {}).get("parsing_source") == "host_llm_required":
            data["next_step"] = (
                "Claude Desktop should now read host_llm_extraction.document_text and "
                "produce project_data JSON, then call orchestrate_pf_review with that JSON."
            )
        return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]

    elif name == "generate_pf_report":
        data = arguments["project_data"]
        agent = ReportAgent()
        structured_report = agent.generate_structured_report(data)
        report = agent.generate_report(data)
        result = {
            "template": structured_report["template"],
            "report": structured_report,
            "report_markdown": report,
            "metadata": {
                "section_count": structured_report["qualityChecks"]["actualSectionCount"],
                "expected_section_count": structured_report["qualityChecks"]["expectedSectionCount"],
                "complete": structured_report["qualityChecks"]["isComplete"],
            },
        }
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    elif name == "orchestrate_pf_review":
        ai_context = _build_mcp_ai_context(arguments)
        options = {
            **arguments.get("options", {}),
            "ai_context": ai_context,
            "user_id": ai_context["user_id"],
            "host": "claude_desktop",
        }
        run = OrchestrationAgent().run_pf_review(
            arguments["project_id"],
            arguments["project_data"],
            options,
        )
        return [types.TextContent(type="text", text=json.dumps(run, ensure_ascii=False, indent=2))]

    elif name == "analyze_development_feasibility":
        result = DevelopmentFeasibilityAgent().analyze(arguments["project_data"])
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    elif name == "analyze_marketability":
        result = MarketabilityAgent().analyze(arguments["project_data"])
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    elif name == "get_korean_law_mcp_query_plan":
        result = {
            "source": "realytics-korean-law-adapter",
            "sidecar": "chrisryugj/korean-law-mcp",
            "queries": build_korean_law_mcp_query_plan(arguments["project_data"]),
            "next_step": (
                "Call the korean-law MCP sidecar with these queries, then pass the collected "
                "results into review_korean_real_estate_law as law_context."
            ),
        }
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    elif name == "review_korean_real_estate_law":
        result = LegalReviewAgent().review(
            arguments["project_data"],
            arguments.get("law_context"),
        )
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    elif name == "assess_project_risk":
        items = arguments["risk_items"]
        assessed = [assess_risk_item(i) for i in items]
        overall = calculate_overall_risk(assessed)
        result = {"overall": overall, "details": assessed}
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="realytics-pf-mcp",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
