import { expect, test } from "@playwright/test";

test("사업수지 화면에서 운영 부족과 Excel 차이를 확인하고 금융 검토안을 다시 연다", async ({ page }) => {
  let stored: Record<string, unknown> | undefined;
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    let body: Record<string, unknown> = {};
    if (url.pathname.endsWith("/auth/me")) {
      body = { authenticated: true, user: { userId: "analyst", role: "analyst", status: "active", tenantId: "demo" },
        session: { userId: "analyst", tenantId: "demo" }, permissions: ["project:read", "project:analyze"] };
    } else if (url.pathname.endsWith("/workspace/context")) {
      body = { projectId: "model-demo", projectMemory: { projectName: "사업수지 모형 검증" }, pageData: {} };
    } else if (url.pathname.endsWith("/calculation-runs")) {
      const model = url.searchParams.get("engine_key");
      body = { records: model === "cash_flow_engine" ? [{
        calculationRunId: "baseline-run", engineKey: model,
        resultSnapshot: { summary: { maxMinus: 0, maxMinusMonth: 1, peakOperatingShortfall: 100_000_000,
          firstOperatingShortfallPeriodIndex: 0, endingUnfundedOperatingObligations: 90_000_000 },
        monthlyCashFlow: [{ period: "M0", endingCash: 200_000_000 }] },
      }] : model === "dated_financing" && stored ? [stored] : [] };
    } else if (url.pathname.endsWith("/workbook-reconciliation")) {
      body = { status: "differences", sourceDocument: { originalFileName: "대사 예시.xlsx" },
        rows: [{ key: "total_cost", label: "총사업비", workbookValue: 100, workingValue: 120, differenceWon: 20,
          sourceCell: "수지!A1", sourceUnit: "원", unit: "KRW", status: "different", nextAction: "변경 사유를 확인하세요." }],
        periodComparison: { message: "기간 대응 확인이 필요합니다." } };
    } else if (url.pathname.endsWith("/feasibility/model-review")) {
      const inputSnapshot = route.request().postDataJSON() as Record<string, unknown>;
      expect(inputSnapshot.model).toBe("dated_financing");
      expect(inputSnapshot.reason).toBe("만기 상환 검토");
      body = { calculationRunId: "financial-run", analysisOnly: true, summary: { totalDrawn: 100_000_000,
        totalInterestAccrued: 5_000_000, peakMaturityShortfall: 15_000_000, endingRepaymentCash: 0 },
      maturities: [{ date: "2027-01-01", trancheId: "선순위", totalDue: 105_000_000,
        availableBeforePayment: 90_000_000, totalPaid: 90_000_000, shortfall: 15_000_000 }] };
      stored = { calculationRunId: "financial-run", engineKey: "dated_financing", inputSnapshot, resultSnapshot: body };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/project/model-demo/feasibility");
  await expect(page.getByRole("region", { name: "운영 가용현금 부족" })).toContainText("최대 100,000,000원");
  await page.getByText("Excel과 검토 입력 대사", { exact: true }).click();
  await page.getByRole("button", { name: "최신 입력 대사" }).click();
  await expect(page.getByRole("row", { name: /총사업비/ })).toContainText("수지!A1");
  await expect(page.getByRole("row", { name: /총사업비/ })).toContainText("20원");
  await page.getByText("날짜별 금융과 임대·회수 검토", { exact: true }).click();
  await page.getByRole("button", { name: "합성 예시 입력" }).click();
  await page.getByRole("textbox", { name: "입력 근거와 검토 사유" }).fill("만기 상환 검토");
  await page.getByRole("button", { name: "분석안 계산·저장" }).click();
  await expect(page.getByRole("region", { name: "저장된 분석 결과" })).toContainText("15,000,000원");
  await page.reload();
  await page.getByText("날짜별 금융과 임대·회수 검토", { exact: true }).click();
  await page.getByRole("button", { name: "저장한 검토안 보기" }).click();
  await page.getByRole("combobox", { name: "저장한 검토안" }).selectOption("financial-run");
  await expect(page.getByRole("textbox", { name: "입력 근거와 검토 사유" })).toHaveValue("만기 상환 검토");
  await expect(page.getByRole("region", { name: "저장된 분석 결과" })).toContainText("2027-01-01");
});
