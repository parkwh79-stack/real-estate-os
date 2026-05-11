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
from agents.legal_review_agent import LegalReviewAgent
from agents.marketability_agent import MarketabilityAgent
from agents.orchestration_agent import OrchestrationAgent
from agents.parser_agent import ParserAgent
from agents.report_agent import ReportAgent

load_dotenv()

server = Server("realytics-pf-mcp")

@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    """List available tools for PF analysis."""
    return [
        types.Tool(
            name="calculate_pf_metrics",
            description="PF 수지분석 기초 지표(LTV, LTC, Profit, Exit Sale Rate)를 계산합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "total_revenue": {"type": "number", "description": "총 분양 수입 (원)"},
                    "total_cost": {"type": "number", "description": "총 사업비 (원)"},
                    "pf_amount": {"type": "number", "description": "총 PF 조달 금액 (원)"},
                },
                "required": ["total_revenue", "total_cost", "pf_amount"],
            },
        ),
        types.Tool(
            name="get_professional_sample",
            description="실제 대구 본동 주상복합 PF 등 고도화된 실무 샘플 데이터를 반환합니다. 이를 학습하여 고품질 보고서를 작성하세요.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="analyze_business_plan",
            description="PDF 사업계획서에서 PF 관련 핵심 데이터를 추출합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "분석할 PDF 파일 경로"},
                },
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
            description="프로젝트 데이터 로드, 사업성 계산, PF 구조 계산, 분양성 검증, 법률/인허가 예비 검토, 리스크 평가, Phase 11 보고서 생성, 품질 점검을 하나의 실행 run으로 수행합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "프로젝트 ID"},
                    "project_data": {"type": "object", "description": "오케스트레이션에 사용할 프로젝트 전체 데이터"},
                    "options": {"type": "object", "description": "실행 옵션"},
                },
                "required": ["project_id", "project_data"],
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
            name="review_korean_real_estate_law",
            description="국내 부동산 PF의 법률/인허가/권리관계/Covenant를 예비 검토합니다. Korean Law MCP 근거가 있으면 law_context로 전달하세요.",
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

    if not arguments:
        raise ValueError("Arguments are required")

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
            
        agent = ParserAgent()
        text = agent.parse_document(path)
        data = agent.extract_project_data(text)
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
        run = OrchestrationAgent().run_pf_review(
            arguments["project_id"],
            arguments["project_data"],
            arguments.get("options", {}),
        )
        return [types.TextContent(type="text", text=json.dumps(run, ensure_ascii=False, indent=2))]

    elif name == "analyze_marketability":
        result = MarketabilityAgent().analyze(arguments["project_data"])
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
