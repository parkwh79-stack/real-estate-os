import { expect, test } from "@playwright/test";

test("보관 도면으로 구성한 면적을 같은 저장 현금흐름에서 다시 본다", async ({ page }) => {
  const projectId = "area-review";
  const archiveId = "a".repeat(64);
  const runId = "area-run-2";
  const drawing = { grossFloorAreaSqm: 1_800.5, revision: "R2", location: "A-10 면적표", archiveId };
  const areaBasis = {
    land: { kind: "parcel_estimate", amountKrw: 100_000_000, includedAreaSqm: 1000, parcels: [
      { parcelIndex: 1, pnu: "1111010100100010000", excluded: false, includedAreaSqm: 1000, estimatedCostKrw: 100_000_000 },
      { parcelIndex: 2, pnu: "1111010100100020000", excluded: true, ledgerAreaSqm: 100, includedAreaSqm: null, estimatedCostKrw: null },
    ] },
    construction: { kind: "drawing", grossFloorAreaSqm: drawing.grossFloorAreaSqm, revision: drawing.revision,
      amountKrw: 1_800_500_000, sourceLocator: { archiveId, fileName: "사업 도면.pdf", location: drawing.location } },
  };
  let calculated = false;
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname.endsWith("/auth/me")) {
      body = { authenticated: true, user: { userId: "analyst", role: "analyst", status: "active", tenantId: "demo" },
        session: { userId: "analyst", tenantId: "demo" }, permissions: ["project:read", "project:write", "project:analyze"] };
    } else if (url.pathname.endsWith("/workspace/context")) {
      body = { projectId, projectMemory: { projectName: "면적 근거 검증" }, pageData: {} };
    } else if (url.pathname.endsWith("/statutory-cost-review")) {
      body = { documents: [{ archiveId, fileName: "사업 도면.pdf" }] };
    } else if (url.pathname.endsWith("/feasibility/build-input")) {
      expect(route.request().postDataJSON()).toEqual({ apply: true, drawing });
      body = { applied: true, hasRevenueAndCost: true, programBreakdown: { programAreaBasis: areaBasis } };
    } else if (url.pathname.endsWith("/cash-flow/run")) {
      calculated = true;
      body = { calculationRunId: runId };
    } else if (url.pathname.endsWith("/calculation-runs")) {
      body = { records: calculated && url.searchParams.get("engine_key") === "cash_flow_engine" ? [{
        calculationRunId: runId, engineKey: "cash_flow_engine", inputSnapshot: { programAreaBasis: areaBasis },
        resultSnapshot: { summary: { totalRevenue: 3_000_000_000, totalProjectCost: 1_900_500_000 },
          monthlyCashFlow: [{ monthIndex: 0, period: "M0", cashOut: { totalCashOut: 100_000_000 } }] },
      }] : [] };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/project/" + projectId + "/cashflow");
  await page.getByText("도면으로 시공 연면적 지정", { exact: true }).click();
  await page.getByLabel("보관 도면").selectOption(archiveId);
  await page.getByLabel("시공 연면적(㎡)").fill(String(drawing.grossFloorAreaSqm));
  await page.getByLabel("도면 판본").fill(drawing.revision);
  await page.getByLabel("도면 위치").fill(drawing.location);
  await page.getByRole("button", { name: "도면 근거로 사업수지 구성" }).click();
  await expect(page.getByText("저장된 계산의 면적 근거", { exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: /1111010100100020000/ })).toContainText("제외");
  await page.reload();
  await expect(page.getByRole("link", { name: "사업 도면.pdf" })).toHaveAttribute("href", "/project/" + projectId + "/documents/review?archiveId=" + archiveId);
  await expect(page.getByText(/R2 · A-10 면적표/)).toBeVisible();
  await expect(page.getByRole("link", { name: "수식 Excel 다운로드" })).toHaveAttribute("href", "/api/projects/" + projectId + "/cashflow/export?anchorCalculationRunId=" + runId);
});
