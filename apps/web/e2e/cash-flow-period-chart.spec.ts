import { expect, test, type Page } from "@playwright/test";

const won = 100_000_000;
const sourceRows = [
  { period: "원본 1: 토지 잔금", cashIn: { totalCashIn: 2 * won }, cashOut: { totalCashOut: 10 * won }, cumulativeCashFlowBeforePfDraw: -8 * won },
  { period: "원본 2: 착공", cashIn: { totalCashIn: 0 }, cashOut: { totalCashOut: 0 }, cumulativeCashFlowBeforePfDraw: null },
  { period: "원본 3: 1개월", cashIn: { totalCashIn: 9 * won }, cashOut: { totalCashOut: 0 }, cumulativeCashFlowBeforePfDraw: won },
];

async function openChart(page: Page, options: { monthly?: boolean; maximumRow?: number } = {}) {
  const monthlyCashFlow = options.monthly ? sourceRows.map((row, index) => ({
    ...row,
    period: `M${index}`,
    endingCash: index === 0 ? 5 * won : null,
    monthlyFinancing: { principalRepayment: index === 1 ? null : 0 },
  })) : sourceRows;
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname.endsWith("/auth/me")) {
      body = { authenticated: true, user: { userId: "reviewer", role: "admin", status: "active", tenantId: "demo" },
        session: { userId: "reviewer", tenantId: "demo" }, permissions: ["project:read"], authSource: "bearer_token" };
    } else if (url.pathname.endsWith("/workspace/context")) {
      body = { projectId: "cashflow-chart", projectMemory: { projectName: "현금흐름 시각화 검증" }, pageData: {} };
    } else if (url.pathname.endsWith("/calculation-runs")) {
      body = { records: url.searchParams.get("engine_key") === "cash_flow_engine" ? [{
        calculationRunId: "chart-run", engineKey: "cash_flow_engine",
        resultSnapshot: {
          periodBasis: { schemaVersion: "cash-flow-period-basis-v1", kind: options.monthly ? "calendar_monthly" : "source_sequence" },
          summary: { maxMinus: -8 * won, maxMinusMonth: options.maximumRow ?? 1 },
          monthlyCashFlow,
          ...(options.monthly ? { monthlyFinancing: {} } : {}),
        },
      }] : [] };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/project/cashflow-chart/cashflow", { waitUntil: "domcontentloaded" });
  return page.getByRole("figure", { name: "현금흐름 추이" });
}

test("source-order chart uses the exact first-row deficit and leaves a missing balance disconnected", async ({ page }) => {
  const chart = await openChart(page);
  await expect(chart).toContainText("원본 사건·기간 순서");
  await expect(chart).toContainText("최대 자금 부족 8억원 · 원본 1: 토지 잔금");
  await expect(chart.locator(".cashflow-maximum-deficit")).toBeVisible();
  await expect(chart.locator(".recharts-line-dot")).toHaveCount(2);
  const zeroLine = chart.locator(".recharts-reference-line-line");
  await expect(zeroLine).toHaveAttribute("stroke-dasharray", "4 3");
  expect(Number(await zeroLine.getAttribute("x2"))).toBeGreaterThan(Number(await zeroLine.getAttribute("x1")));
  await expect(chart).toContainText("가로 간격은 실제 경과 기간을 나타내지 않습니다.");
});

test("monthly ending cash and the pre-draw deficit are displayed on separate bases", async ({ page }) => {
  const chart = await openChart(page, { monthly: true });
  await expect(chart).toContainText("월말 계정합계");
  await expect(chart).toContainText("최대 자금 부족 8억원 · M0");
  await expect(chart).toContainText("최대 자금 부족 표시는 PF 인출 전 기준입니다.");
  await expect(chart.locator(".cashflow-maximum-deficit")).toBeVisible();
  await expect(chart.locator(".recharts-line-dot")).toHaveCount(1);
});

test("a mismatched saved deficit row does not place an invented marker", async ({ page }) => {
  const chart = await openChart(page, { maximumRow: 3 });
  await expect(chart).toContainText("저장된 최대 자금 부족의 위치를 확인할 수 없습니다.");
  await expect(chart.locator(".recharts-line-dot")).toHaveCount(2);
  await expect(chart.locator(".cashflow-maximum-deficit")).toHaveCount(0);
});
