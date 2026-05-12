# Korean Law MCP Integration

Updated: 2026-05-12

## Decision

REALYTICS should not vendor or copy `chrisryugj/korean-law-mcp` into this repository.

Use it as a sidecar MCP server and keep a small REALYTICS adapter that normalizes Korean Law MCP results into `law_context`.

## Why Sidecar

- Korean Law MCP is already distributed as an MCP server/CLI package.
- It tracks Law.go.kr behavior, User-Agent issues, citation verification, precedent, ordinance, `impact_map`, `time_travel`, and `action_plan` features.
- Keeping it external lets REALYTICS update the legal source layer without forking legal API maintenance.
- REALYTICS can stay focused on PF review, permit risk, covenant, land-rights, report, and orchestration logic.

## Claude Desktop Setup

Install Korean Law MCP:

```bash
npm install -g korean-law-mcp
```

Add it beside the REALYTICS MCP server in Claude Desktop:

```json
{
  "mcpServers": {
    "realytics-pf": {
      "command": "python",
      "args": [
        "C:/Users/User/Documents/real estate os/realytics-platform9-monorepo/services/mcp-server/server.py"
      ],
      "env": {
        "REALYTICS_AI_PROVIDER": "claude_desktop",
        "MCP_USER_ID": "your-realytics-user-id",
        "PYTHONPATH": "C:/Users/User/Documents/real estate os/realytics-platform9-monorepo/services/api-server"
      }
    },
    "korean-law": {
      "command": "korean-law-mcp",
      "env": {
        "LAW_OC": "your-law-go-kr-api-key"
      }
    }
  }
}
```

## Recommended Flow

1. Call REALYTICS MCP `get_korean_law_mcp_query_plan` with `project_data`.
2. Claude Desktop calls Korean Law MCP using the returned query plan.
3. Claude Desktop collects the legal outputs into `law_context`.
4. Call REALYTICS MCP `review_korean_real_estate_law` with:

```json
{
  "project_data": {},
  "law_context": {
    "source": "korean-law-mcp",
    "references": [
      {
        "type": "law",
        "title": "건축법",
        "lawName": "건축법",
        "article": "제11조",
        "summary": "건축허가 근거 조문"
      }
    ],
    "citationVerification": {
      "status": "passed",
      "checkedCount": 1,
      "failures": []
    }
  }
}
```

## REALYTICS law_context Schema

REALYTICS normalizes incoming data to:

```json
{
  "version": "realytics-law-context-v1",
  "source": "korean-law-mcp",
  "connected": true,
  "retrievedAt": "2026-05-12T00:00:00Z",
  "references": [
    {
      "type": "law",
      "title": "건축법",
      "citation": "제11조",
      "lawName": "건축법",
      "article": "제11조",
      "effectiveDate": "20260512",
      "url": "",
      "summary": "건축허가 관련 조문",
      "verified": true,
      "rawTool": "search_law"
    }
  ],
  "citationVerification": {
    "status": "passed",
    "failures": [],
    "checkedCount": 1
  },
  "queryPlan": []
}
```

## Notes

- `LAW_OC` is the Korean Law MCP/Law.go.kr API credential. Do not store it in Git.
- REALYTICS does not issue final legal opinions. Legal and permit findings remain preliminary review outputs.
- If `citationVerification.failures` is non-empty, REALYTICS marks the legal review as `blocking_issue`.
