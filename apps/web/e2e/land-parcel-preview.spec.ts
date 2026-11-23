import { expect, test } from "@playwright/test";

test("land preview continues, retries unresolved parcels and saves only the selected current batch", async ({ page }) => {
  const projectId = "parcel-review-sample";
  const revision = "a".repeat(64);
  const pnu = (i: number) => `11110101001000${i}0000`;
  const previewBodies: unknown[] = [];
  let releaseSecondBatch: () => void = () => {};
  const secondBatch = new Promise<void>((resolve) => { releaseSecondBatch = resolve; });
  const saveBodies: unknown[] = [];
  const mapBodies: unknown[] = [];
  const row = (parcelIndex: number, status: "confirmed" | "ambiguous" = "confirmed") => ({
    parcelIndex,
    address: `합성동 ${parcelIndex}`,
    status,
    pnu: status === "confirmed" ? pnu(parcelIndex) : null,
    landUseStatus: status === "confirmed" ? "confirmed" : "not_queried",
    zoning: status === "confirmed" ? "제2종일반주거지역" : null,
    landUseSourceLocator: status === "confirmed" ? { sourceName: "VWorld 토지이용계획" } : null,
  });
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      return route.fulfill({ json: {
        authenticated: true,
        user: { userId: "review-user", tenantId: "review-tenant", role: "analyst", status: "active" },
        permissions: ["project:read", "project:analyze", "project:write"],
      } });
    }
    if (path.endsWith("/workspace/context")) {
      return route.fulfill({ json: {
        projectId,
        projectMemory: { projectName: "합성 필지 미리보기" },
        pageData: {
          landLedger: {
            summary: { parcelCount: 6, pnuCount: 1 },
            parcels: Array.from({ length: 6 }, (_, index) => ({
              sequence: index + 1, addressFragment: `합성동 ${index + 1}`,
              ...(index === 0 ? { pnu: pnu(1), normalizedLotNumber: "1", pnuResolution: { lotVerification: "verified" } } : {}),
            })),
          },
        },
      } });
    }
    if (path.endsWith("/legal-pod/preview")) {
      const body = route.request().postDataJSON() as { parcelOffset?: number; parcelIndexes?: number[] };
      previewBodies.push(body);
      const last = body.parcelOffset === 5;
      if (last) await secondBatch;
      const retry = Boolean(body.parcelIndexes);
      return route.fulfill({ json: {
        previewOnly: true,
        previewHash: (last ? "c" : retry ? "d" : "b").repeat(64),
        legalReview: {},
        parcelBatch: {
          parcelRevision: revision,
          totalParcels: 6,
          nextOffset: last || retry ? null : 5,
          rows: last ? [row(6)] : retry ? [row(2)] : [row(1), row(2, "ambiguous"), row(3), row(4), row(5)],
          jurisdictions: [{ jurisdictionCode: "11110", jurisdictionName: "합성구", zoning: "제2종일반주거지역", parcelIndexes: last ? [6] : retry ? [2] : [1, 3, 4, 5] }],
        },
      } });
    }
    if (path.endsWith("/legal-pod/preview-map")) {
      mapBodies.push(route.request().postDataJSON());
      return route.fulfill({ json: { parcelGeoJson: { type: "FeatureCollection", features: [] } } });
    }
    if (path.endsWith("/legal-pod/preview-summary") && route.request().method() === "POST") {
      saveBodies.push(route.request().postDataJSON());
      return route.fulfill({ json: { recordId: "saved-preview-batch", summary: { parcels: [{ parcelIndex: 2, parcelLabel: "합성동 2", pnu: pnu(2) }] } } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(`/project/${projectId}/land?track=development`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "토지·인허가 미리보기", exact: true }).click();
  const preview = page.getByRole("heading", { name: "필지별 조회 진행" }).locator("..").locator("..");
  await expect(preview.getByRole("status")).toContainText("전체 6필지 중 5필지 상태 확인");
  await expect(preview.getByRole("status")).toContainText("후보 확인 1필지");
  await expect(preview.getByRole("link", { name: "지도에서 보기" })).toHaveAttribute("href", `/project/${projectId}/map?track=development&pnu=${pnu(1)}`);
  await expect(preview.getByRole("button", { name: "지도 미리보기" })).toHaveCount(3);
  await expect(preview.getByText(/출처 VWorld 토지이용계획/)).toHaveCount(4);
  await preview.getByText("이 묶음의 관할·용도지역 근거").click();
  await expect(preview.getByText(/합성구 · 제2종일반주거지역/)).toBeVisible();
  expect(saveBodies).toHaveLength(0);

  await expect(preview.getByRole("button", { name: "필지 조회 중지" })).toBeVisible();
  releaseSecondBatch();
  await expect(preview.getByRole("status")).toContainText("전체 6필지 중 6필지 상태 확인");
  await expect(preview.getByRole("button", { name: "다음 필지 확인" })).toHaveCount(0);
  await preview.getByRole("listitem").filter({ hasText: "합성동 6" }).getByRole("button", { name: "지도 미리보기" }).click();
  const map = preview.getByRole("region", { name: "필지 지도 미리보기" });
  await expect(map).toContainText("이 필지의 공식 경계를 확인하지 못했습니다.");
  expect(mapBodies.length).toBeGreaterThan(0);
  for (const body of mapBodies) expect(body).toEqual({ parcelIndex: 6, parcelRevision: revision, pnu: pnu(6) });
  expect(saveBodies).toHaveLength(0);
  await map.getByRole("button", { name: "지도 닫기" }).click();
  await preview.getByLabel("검토한 필지 묶음").selectOption("0");
  await preview.getByRole("button", { name: "확인이 필요한 필지 다시 조회 (1)" }).click();
  await expect(preview.getByRole("status")).toContainText("PNU 확인 6필지");
  expect(previewBodies).toEqual([{}, { parcelOffset: 5, parcelRevision: revision }, { parcelIndexes: [2], parcelRevision: revision }]);
  expect(saveBodies).toHaveLength(0);

  await page.getByText("보관할 필지 선택", { exact: true }).click();
  await page.getByRole("checkbox", { name: "합성동 2", exact: true }).check();
  await page.getByRole("button", { name: "선택 항목만 저장" }).click();
  await expect(page.getByText("저장한 예비 검토 1")).toBeVisible();
  expect(saveBodies).toEqual([{
    sections: ["parcels"], parcelIndexes: [2], expectedPreviewHash: "d".repeat(64),
    parcelRevision: revision, reviewedParcelIndexes: [2],
  }]);

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: "토지·인허가 미리보기", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "필지별 조회 진행" })).toHaveCount(0);
});

test("manual PNU creates a temporary land ledger and saves only an explicit selection", async ({ page }) => {
  const pnu = "1111010100100010000";
  const revision = "1".repeat(64);
  const hash = "2".repeat(64);
  const previewRequests: unknown[] = [];
  const saves: unknown[] = [];

  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: {
      authenticated: true, user: { userId: "land-user", tenantId: "land-tenant", role: "analyst" },
      permissions: ["project:read", "project:analyze", "project:write"],
    } });
    if (path.endsWith("/workspace/context")) return route.fulfill({ json: {
      projectId: "manual-land", projectMemory: { projectName: "직접 PNU 검토" },
      pageData: { landLedger: { parcels: [], summary: { parcelCount: 0, pnuCount: 0 } } },
    } });
    if (path.endsWith("/legal-pod/preview")) {
      previewRequests.push(route.request().postDataJSON());
      return route.fulfill({ json: {
        previewOnly: true, previewHash: hash, legalReview: {},
        parcelBatch: {
          source: "manual_pnu", parcelRevision: revision, totalParcels: 1, nextOffset: null,
          rows: [{ parcelIndex: 1, requestedPnu: pnu, pnu, status: "confirmed", address: "합성시 시험동 1",
            landUseStatus: "confirmed", zoning: "제2종일반주거지역" }],
          jurisdictions: [],
        },
      } });
    }
    if (path.endsWith("/legal-pod/preview-summary") && route.request().method() === "POST") {
      saves.push(route.request().postDataJSON());
      return route.fulfill({ json: { recordId: "explicit-save", summary: {} } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto("/project/manual-land/land?track=development", { waitUntil: "domcontentloaded" });
  const input = page.getByLabel("필지고유번호(PNU)");
  const submit = page.getByRole("button", { name: "입력한 PNU로 미리보기" });
  await input.fill("123");
  await submit.click();
  await expect(page.getByText("올바른 19자리 필지고유번호를 최대 5개 입력해 주세요.")).toBeVisible();
  expect(previewRequests).toEqual([]);

  await input.fill(pnu);
  await submit.click();
  await expect(page.getByText(/직접 입력한 PNU로 만든 임시 토지조서입니다/)).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "합성시 시험동 1" })).toBeVisible();
  expect(previewRequests).toEqual([{ pnus: [pnu] }]);
  expect(saves).toEqual([]);

  await page.getByRole("checkbox", { name: "인허가 경로", exact: true }).check();
  await page.getByRole("button", { name: "선택 항목만 저장", exact: true }).click();
  await expect(page.getByText("저장한 예비 검토 1")).toBeVisible();
  expect(saves).toEqual([{ pnus: [pnu], parcelRevision: revision, sections: ["permit"], parcelIndexes: [], expectedPreviewHash: hash }]);
});
