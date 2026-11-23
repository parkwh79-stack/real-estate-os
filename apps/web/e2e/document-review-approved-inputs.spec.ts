import { expect, test, type Page } from "@playwright/test";
import type { ApprovedInputSnapshotRecord, CanonicalSelectionRecord, DocumentReviewData, ParserInputApplicationPreview } from "../lib/document-review-workspace";

// MOCK-DATA: approved synthetic source facts and a separate human approver.
// Actual RBAC, MFA, four-eyes and PostgreSQL CAS are verified in the API suite.
const projectId = "synthetic-approved-site-inputs";
const archiveId = "a".repeat(64);
const fields = ["project.name", "land.address", "land.area_sqm"];
const values = ["승인된 사업", "서울특별시 중랑구 상봉동 1-1", 1234.56];

async function fixture(page: Page, seeded = false) {
  const state = { selections: [] as CanonicalSelectionRecord[], snapshots: [] as ApprovedInputSnapshotRecord[],
    posts: [] as { path: string; body: Record<string, unknown> }[], selectionStatus: 201, snapshotStatus: 201, applyStatus: 200,
    failReads: false, omitSavedSelection: false, failReadsAfterSelection: false, failPreviewAfterApply: false, malformedSelection: false,
    current: ["기존 사업", "기존 주소", 100] as unknown[], lastApplication: null as ParserInputApplicationPreview["lastApplication"],
  };
  const candidates = fields.map((fieldKey, index) => ({ candidateId: `candidate-${index}`, parsingRunId: "site-run",
    documentVersionId: "site-v1", fieldKey, normalizedValue: values[index], rawValue: values[index],
    unit: index === 2 ? "sqm" : undefined, presenceStatus: "present", reviewStatus: "reviewed",
    approvalStatus: "approved", validationStatus: "passed", validationCodes: [], conflictStatus: "none",
    sourceLocator: { documentVersionId: "site-v1", fileSha256: archiveId, pageNumber: index + 1, paragraphIndex: 1 },
  }));
  const addSelection = (index: number, selectionId = `selection-${index}`) => {
    const result = { selectionId, candidateId: `candidate-${index}`, reviewDecisionId: `decision-${index}`,
      fieldKey: fields[index], normalizedValue: values[index], unit: index === 2 ? "sqm" : undefined,
      selectionVersion: 1, validityStatus: "active", approvalStatus: "approved" };
    state.selections.push(result); return result;
  };
  const addSnapshot = (snapshotId: string, selectionIds: string[]) => {
    const result = { snapshotId, selectionIds, selections: state.selections.filter((item) => selectionIds.includes(String(item.selectionId))),
      purpose: "project-site-inputs", snapshotVersion: 1, validityStatus: "active", approvalStatus: "approved",
      inputItems: state.selections.filter((item) => selectionIds.includes(String(item.selectionId))).map((item) => ({
        fieldKey: item.fieldKey, normalizedValue: item.normalizedValue, unit: item.unit, candidateId: item.candidateId, selectionId: item.selectionId,
      })) };
    state.snapshots.push(result); return result;
  };
  if (seeded) { fields.forEach((_, index) => addSelection(index)); addSnapshot("snapshot-1", state.selections.map((item) => String(item.selectionId))); }
  const data = (): DocumentReviewData => ({
    schemaVersion: "realytics-document-review-workspace-v1", viewModelVersion: "document-review-workspace-2026-07-27",
    projectId, snapshotId: `view-${state.selections.length}-${state.snapshots.length}`, snapshotHash: "b".repeat(64), observedAt: "2026-11-09T00:00:00Z",
    sourceDocuments: [{ archiveId, originalFileName: "합성_사업개요.txt", contentType: "text/plain", archiveIntegrityStatus: "verified" }],
    runs: [], packages: [{ parsingRun: { parsingRunId: "site-run", documentVersionId: "site-v1", executionStatus: "completed" },
      documentVersion: { documentVersionId: "site-v1", archiveId, sourceHash: archiveId, fileName: "합성_사업개요.txt", mediaType: "text/plain" },
      candidates, conflicts: [], reviewDecisions: fields.map((_, index) => ({ reviewDecisionId: `decision-${index}`,
        candidateId: `candidate-${index}`, action: "approve", decisionSequence: 1, reviewerId: "separate-human-reviewer",
        resultingReviewStatus: "reviewed", resultingApprovalStatus: "approved", reason: "합성 원본 대조" })),
      canonicalSelections: state.omitSavedSelection ? [] : state.selections, approvedInputSnapshots: state.snapshots,
    }], glossaryCandidates: [], reviewQueue: [], snapshotDiffs: [], warnings: [],
  });
  const preview = (snapshotId: string): ParserInputApplicationPreview => ({
    schemaVersion: "realytics-parser-input-application-v1", projectId, snapshotId, snapshotHash: "c".repeat(64), previewHash: "d".repeat(64), expectedStateVersion: state.lastApplication ? 2 : 1,
    items: fields.filter((field) => state.snapshots.find((item) => item.snapshotId === snapshotId)?.inputItems?.some((item) => item.fieldKey === field)).map((fieldKey) => {
      const index = fields.indexOf(fieldKey);
      return { fieldKey, targetPath: fieldKey, currentValue: state.current[index], approvedValue: values[index], unit: index === 2 ? "sqm" : undefined,
        selectionId: String(state.selections.find((item) => item.fieldKey === fieldKey)?.selectionId), candidateId: `candidate-${index}`,
        documentVersionId: "site-v1", sourceHash: archiveId, sourceLocator: candidates[index]!.sourceLocator, changed: state.current[index] !== values[index] };
    }), lastApplication: state.lastApplication, limitations: ["선택한 사업 기본 입력만 반영합니다. 법률·전체 Pod 승인을 뜻하지 않습니다."],
  });
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    const reply = (json: unknown, status = 200) => route.fulfill({json, status});
    if (path.endsWith("/auth/me")) return reply({ authenticated: true,
      user: { userId: "separate-human-approver", tenantId: "synthetic-tenant", role: "admin", status: "active" },
      session: { userId: "separate-human-approver", tenantId: "synthetic-tenant", mfaVerified: true }, permissions: ["project:read", "evidence:read", "assumption:override"] });
    if (path.endsWith("/projects")) return reply([{ project: { id: projectId, name: String(state.current[0]) } }]);
    if (path.endsWith("/workspace/context")) return reply({projectId, projectMemory: {projectName: String(state.current[0])},
      pageData: {land: {address: state.current[1], area_sqm: state.current[2]}, landLedger: {parcels: []}, landLedgerSummary: {parcelCount:0},
        landEvidenceSummary: {count:0}, landEvidenceRecords: [], pnuValidation: {}}});
    if (path.endsWith("/workspace/document-review")) return state.failReads ? reply({}, 503) : reply(data());
    if (path.endsWith("/input-preview")) return state.failPreviewAfterApply && state.lastApplication ? reply({}, 503) : reply(preview(decodeURIComponent(path.split("/").at(-2)!)));
    if (request.method() === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>; state.posts.push({path, body});
      if (path.endsWith("/canonical-selections")) {
        if (state.selectionStatus !== 201) return reply({ detail: "review decision sequence changed" }, state.selectionStatus);
        const result = addSelection(Number(String(body.candidateId).split("-").at(-1)), String(body.selectionId));
        if (state.failReadsAfterSelection) state.failReads = true;
        return reply(state.malformedSelection ? null : result, 201);
      }
      if (path.endsWith("/approved-snapshots")) {
        if (state.snapshotStatus !== 201) return reply({detail: "four-eyes control requires separate snapshot approver"}, state.snapshotStatus);
        return reply(addSnapshot(String(body.snapshotId), body.selectionIds as string[]), 201);
      }
      if (path.endsWith("/apply-input")) {
        if (state.applyStatus !== 200) return reply({detail: "project input changed"}, state.applyStatus);
        const keys = body.fieldKeys as string[];
        for (const key of keys) state.current[fields.indexOf(key)] = values[fields.indexOf(key)];
        state.lastApplication = { applicationId: String(body.applicationId), snapshotId: decodeURIComponent(path.split("/").at(-2)!), fieldKeys: keys };
        return reply({applicationId: body.applicationId, status: "applied", appliedFieldKeys: keys});
      }
      return reply({detail:"unexpected mutation"}, 500);
    }
    if (path.endsWith("/land-ledger")) return reply({projectId, land: {address: state.current[1], area_sqm: state.current[2]}, summary: {}, parcels: []});
    if (path.endsWith("/auth/ai-credentials/current")) return reply({revoked: true});
    return reply({});
  });
  await page.goto(`/project/${projectId}/documents/review`);
  const inspector = page.getByRole("complementary", {name:"문서 검토 Inspector"});
  await inspector.getByRole("button").filter({has: page.getByText("Candidate candidate-0", {exact:true})}).click();
  await inspector.locator("summary").filter({hasText:"검토 더보기"}).click();
  await inspector.getByRole("tab", {name:"검토 결정", exact:true}).click();
  await expect(page.getByRole("region", {name:"승인 입력 선택과 프로젝트 반영"})).toBeVisible();
  return state;
}

const panel = (page: Page) => page.getByRole("region", {name:"승인 입력 선택과 프로젝트 반영"});
async function selectName(page: Page) {
  await panel(page).getByLabel("값·단위·원본과 기존 선택값의 차이를 확인했습니다.").check();
  await panel(page).getByRole("button", {name:"이 값을 승인 입력으로 선택", exact:true}).click();
}

test("reviewed candidate needs explicit selection then snapshot; source facts alone never change project input", async ({page}) => {
  const state = await fixture(page);
  await expect(panel(page).getByRole("button", {name:"이 값을 승인 입력으로 선택", exact:true})).toBeDisabled();
  await selectName(page);
  await expect(panel(page).getByText("선택한 값을 최신 원장에서 확인했습니다.", {exact:false})).toBeVisible();
  expect(state.current[0]).toBe("기존 사업");
  await panel(page).getByLabel(/^사업명 · 승인된 사업$/).check();
  await panel(page).getByLabel("선택한 값만 승인본으로 저장합니다. 프로젝트 입력 반영은 다음 단계입니다.").check();
  await panel(page).getByRole("button", {name:"선택한 값으로 승인본 저장", exact:true}).click();
  await expect(panel(page).getByText("승인본 저장을 확인했습니다.", {exact:false})).toBeVisible();
  expect(state.current[0]).toBe("기존 사업");
  expect(state.posts.map((post) => post.path.split("/").at(-1))).toEqual(["canonical-selections", "approved-snapshots"]);
  await panel(page).getByRole("button", {name:"현재 입력과 비교", exact:true}).click();
  await panel(page).getByLabel("사업명 프로젝트 입력 반영 선택").check();
  await panel(page).getByLabel("선택한 현재 입력을 승인본 값으로 변경하는 데 동의합니다.").check();
  await panel(page).getByRole("button", {name:"선택한 승인 입력을 프로젝트에 반영", exact:true}).click();
  await expect(panel(page).getByText("선택한 승인 입력의 프로젝트 반영과 출처 기록을 다시 조회해 확인했습니다.")).toBeVisible();
  expect(state.current[0]).toBe("승인된 사업");
  expect(state.posts[2]!.body).toMatchObject({fieldKeys:["project.name"], expectedStateVersion:1, expectedSnapshotHash:"c".repeat(64)});
  expect(state.posts[2]!.body).not.toHaveProperty("value");
});

test("area and address apply only after current-versus-approved comparison and reopen preserves raw precision", async ({page}) => {
  test.setTimeout(60_000); // Includes a cold compilation/navigation to the actual land page.
  const state = await fixture(page, true);
  await panel(page).getByRole("button", {name:"현재 입력과 비교", exact:true}).click();
  await expect(panel(page).getByText("승인본: 1,235 ㎡", {exact:false})).toBeVisible();
  await panel(page).getByLabel("전체 대지면적 프로젝트 입력 반영 선택").check();
  await panel(page).getByLabel("사업지 주소 프로젝트 입력 반영 선택").check();
  await panel(page).getByLabel("선택한 현재 입력을 승인본 값으로 변경하는 데 동의합니다.").check();
  await panel(page).getByRole("button", {name:"선택한 승인 입력을 프로젝트에 반영", exact:true}).click();
  await expect(panel(page).getByText("선택한 승인 입력의 프로젝트 반영과 출처 기록을 다시 조회해 확인했습니다.")).toBeVisible();
  expect(state.current).toEqual(["기존 사업", values[1], 1234.56]);
  await page.reload();
  const inspector = page.getByRole("complementary", {name:"문서 검토 Inspector"});
  await inspector.locator("summary").filter({hasText:"검토 더보기"}).click();
  await inspector.getByRole("tab", {name:"검토 결정", exact:true}).click();
  await panel(page).getByRole("button", {name:"현재 입력과 비교", exact:true}).click();
  await expect(panel(page).getByText("현재: 1,235 ㎡", {exact:false})).toBeVisible();
  await panel(page).getByRole("link", {name:"토지 화면에서 반영값 확인", exact:true}).click();
  await page.locator("summary").filter({hasText:"토지 기본 정보·저장된 증빙"}).click();
  await expect(page.getByText("서울특별시 중랑구 상봉동 1-1", {exact:true})).toBeVisible();
  await expect(page.getByText("1,235㎡", {exact:true})).toBeVisible();
  expect(state.posts).toHaveLength(1);
});

test("saved selection with failed readback blocks duplicate writes and recovers only from the stored record", async ({page}) => {
  const state = await fixture(page); state.failReadsAfterSelection = true;
  await selectName(page);
  await expect(panel(page).getByText("저장 응답은 받았지만 최신 원장을 불러오지 못했습니다.", {exact:false})).toBeVisible();
  await expect(panel(page).getByRole("button", {name:"이 값을 승인 입력으로 선택", exact:true})).toBeDisabled();
  state.failReads = false;
  await panel(page).getByRole("button", {name:"저장 기록 다시 확인", exact:true}).click();
  await expect(panel(page).getByText("선택한 값을 최신 원장에서 확인했습니다.", {exact:false})).toBeVisible();
  expect(state.posts).toHaveLength(1);
});

test("four-eyes rejection leaves selection intact and explains the separate approver requirement", async ({page}) => {
  const state = await fixture(page, true); state.snapshotStatus = 409;
  await panel(page).getByLabel(/^사업명 · 승인된 사업$/).check();
  await panel(page).getByLabel("선택한 값만 승인본으로 저장합니다. 프로젝트 입력 반영은 다음 단계입니다.").check();
  await panel(page).getByRole("button", {name:"선택한 값으로 승인본 저장", exact:true}).click();
  await expect(panel(page).getByText("검토한 사람과 승인본을 승인하는 사람은 달라야 합니다.", {exact:false})).toBeVisible();
  expect(state.posts).toHaveLength(1); expect(state.current[0]).toBe("기존 사업");
});

test("malformed successful selection response waits for the immutable stored selection instead of allowing a duplicate", async ({page}) => {
  const state = await fixture(page); state.malformedSelection = true;
  await selectName(page);
  await expect(panel(page).getByRole("button", {name:"저장 기록 다시 확인", exact:true})).toBeVisible();
  await expect(panel(page).getByRole("button", {name:"이 값을 승인 입력으로 선택", exact:true})).toBeDisabled();
  await panel(page).getByRole("button", {name:"저장 기록 다시 확인", exact:true}).click();
  await expect(panel(page).getByText("선택한 값을 최신 원장에서 확인했습니다.", {exact:false})).toBeVisible();
  expect(state.posts).toHaveLength(1);
});

test("apply conflict requires rereading and renewed field consent instead of retrying the same write", async ({page}) => {
  const state = await fixture(page, true); state.applyStatus = 409;
  await panel(page).getByRole("button", {name:"현재 입력과 비교", exact:true}).click();
  await panel(page).getByLabel("사업명 프로젝트 입력 반영 선택").check();
  await panel(page).getByLabel("선택한 현재 입력을 승인본 값으로 변경하는 데 동의합니다.").check();
  await panel(page).getByRole("button", {name:"선택한 승인 입력을 프로젝트에 반영", exact:true}).click();
  await expect(panel(page).getByRole("button", {name:"선택한 승인 입력을 프로젝트에 반영", exact:true})).toBeDisabled();
  state.current[0] = "다른 사용자가 수정한 사업";
  await panel(page).getByRole("button", {name:"저장 기록 다시 확인", exact:true}).click();
  await expect(panel(page).getByText("갱신된 현재 입력을 확인했습니다.", {exact:false})).toBeVisible();
  await expect(panel(page).getByLabel("사업명 프로젝트 입력 반영 선택")).not.toBeChecked();
  await expect(panel(page).getByText("현재: 다른 사용자가 수정한 사업", {exact:false})).toBeVisible();
  expect(state.posts).toHaveLength(1);
});

test("apply acknowledgement without a readable receipt is not shown as complete", async ({page}) => {
  const state = await fixture(page, true); state.failPreviewAfterApply = true;
  await panel(page).getByRole("button", {name:"현재 입력과 비교", exact:true}).click();
  await panel(page).getByLabel("사업명 프로젝트 입력 반영 선택").check();
  await panel(page).getByLabel("선택한 현재 입력을 승인본 값으로 변경하는 데 동의합니다.").check();
  await panel(page).getByRole("button", {name:"선택한 승인 입력을 프로젝트에 반영", exact:true}).click();
  await expect(panel(page).getByRole("button", {name:"저장 기록 다시 확인", exact:true})).toBeVisible();
  await expect(panel(page).getByText("선택한 승인 입력의 프로젝트 반영과 출처 기록을 다시 조회해 확인했습니다.")).toHaveCount(0);
  state.failPreviewAfterApply = false;
  await panel(page).getByRole("button", {name:"저장 기록 다시 확인", exact:true}).click();
  await expect(panel(page).getByText("선택한 승인 입력의 프로젝트 반영과 출처 기록을 다시 조회해 확인했습니다.")).toBeVisible();
  expect(state.posts).toHaveLength(1);
});
