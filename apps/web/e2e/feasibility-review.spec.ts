import { expect, test, type Page } from "@playwright/test";
import { eokToWon, wonToEok } from "../lib/feasibility-review";

test.setTimeout(90_000);

test("money survives won to eok to won without rounding", () => {
  for (const won of [0, 1, -1, 853951767703, -123456789, Number.MAX_SAFE_INTEGER]) {
    expect(eokToWon(wonToEok(won))).toBe(won);
  }
  expect(wonToEok(853951767703)).toBe("8539.51767703");
  for (const invalid of ["", "Infinity", "NaN", "1e3", "0.000000001", "1,000", "90071993"]) {
    expect(() => eokToWon(invalid)).toThrow();
  }
});

async function mockReview(page: Page, conflict = false) {
  const source = { archiveId: "a".repeat(64), originalFileName: "검토자료.xlsx" };
  const state = {
    values: { total_revenue: 853951767703 } as Record<string, number>,
    fieldReviews: {} as Record<string, Record<string, unknown>>,
    reviewHash: "first", sourceDocuments: [source],
  };
  const saves: Record<string, unknown>[] = [];
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith("/auth/me")) {
      body = { authenticated: true, user: { userId: "reviewer", role: "admin", status: "active", tenantId: "tenant-review" },
        session: { userId: "reviewer", tenantId: "tenant-review" }, permissions: ["project:read", "project:write", "assumption:override"], authSource: "bearer_token" };
    } else if (path.endsWith("/workspace/context")) {
      body = { projectId: "review-1", projectMemory: { projectName: "R1 검토" }, pageData: {} };
    } else if (path.endsWith("/feasibility-workbook/review")) {
      body = state;
    } else if (path.endsWith("/feasibility-workbook/apply-input")) {
      const submitted = route.request().postDataJSON();
      saves.push(submitted);
      if (conflict) {
        await route.fulfill({ status: 409, json: { detail: "저장된 입력이 변경되었습니다. 최신 값을 다시 불러온 뒤 수정하세요." } });
        return;
      }
      Object.assign(state.values, submitted.feasibility_input);
      for (const [key, review] of Object.entries(submitted.fieldReviews)) {
        state.fieldReviews[key] = { ...(review as Record<string, unknown>), sourceDocument: submitted.fieldSourceDocuments[key] ?? null };
      }
      state.reviewHash = "saved";
      body = { reviewState: state };
    } else if (path.endsWith("/workbook/cells")) {
      body = { schemaVersion: "realytics-workbook-evidence-preview-v1", sheetName: "손익", focusRange: "I2", previewRange: "I2",
        columns: [], rows: [{ rowNumber: 2, cells: [{ coordinate: "I2", displayValue: 853951767704, focused: true }] }], formulaPolicy: "display_only_never_executed" };
    }
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/review-1/input-package");
  const panel = page.getByRole("region", { name: "사업수지 입력 검토" });
  await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("8539.51767703", { timeout: 30_000 });
  return { panel, saves, source };
}

test("correct a source value and save an assumption, then reopen the review", async ({ page }) => {
  const { panel, saves, source } = await mockReview(page);
  await panel.getByLabel("확인할 원본").selectOption(source.archiveId);
  await panel.getByLabel("총매출 입력 구분").selectOption("document_correction");
  await panel.getByLabel("총매출 출처 셀").fill("손익!I2");
  await panel.getByRole("button", { name: "원본 보기" }).click();
  await expect(panel.getByLabel("원본 셀 미리보기")).toContainText("853951767704");
  await panel.getByLabel("총매출 (억원)").fill("8539.51767704");
  await panel.getByLabel("총매출 검토 이유").fill("원본 대조 후 1원 수정");
  await panel.getByLabel("금융비용 (억원)").fill("1.23456789");
  await panel.getByLabel("금융비용 검토 이유").fill("자료 수령 전 분석용 가정");
  await panel.getByRole("button", { name: "검토 입력 저장" }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(saves).toHaveLength(1);
  expect(saves[0]?.feasibility_input).toEqual({ total_revenue: 853951767704, finance_cost: 123456789 });
  expect(saves[0]?.expectedReviewHash).toBe("first");
  await page.reload();
  await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("8539.51767704");
  await expect(panel.getByLabel("금융비용 (억원)")).toHaveValue("1.23456789");
  await expect(panel.getByLabel("금융비용 입력 구분")).toHaveValue("user_assumption");
  await expect(panel.getByLabel("총매출 출처 셀")).toHaveValue("손익!I2");
  await expect(panel.getByLabel("총사업비 (억원)")).toHaveValue("");
  await expect(panel.getByRole("link", { name: "사업수지 분석으로 이동" })).toHaveAttribute("href", "/project/review-1/feasibility");
});

test("invalid amounts cannot partially save the other edited fields", async ({ page }) => {
  const { panel, saves } = await mockReview(page);
  await panel.getByLabel("총매출 (억원)").fill("NaN");
  await panel.getByLabel("총매출 검토 이유").fill("잘못된 입력");
  await panel.getByRole("button", { name: "검토 입력 저장" }).click();
  await expect(panel.getByRole("alert")).toContainText("금액은 억원 단위 숫자로");
  expect(saves).toHaveLength(0);
  await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("NaN");
});

test("a stale save keeps the user's edit and asks for a reload", async ({ page }) => {
  const { panel, saves } = await mockReview(page, true);
  await panel.getByLabel("총매출 (억원)").fill("9000");
  await panel.getByLabel("총매출 검토 이유").fill("사용자 가정");
  await panel.getByRole("button", { name: "검토 입력 저장" }).click();
  await expect(panel.getByRole("alert")).toContainText("저장된 입력이 변경되었습니다");
  await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("9000");
  expect(saves).toHaveLength(1);
});
