import { expect, test } from "@playwright/test";

test.setTimeout(90_000);

test("opening the deal room loads its plan and execution starts only after a click", async ({ page }) => {
  const plan = {
    projectId: "explicit-run",
    sharedDealStateGraph: {},
    agentPods: [],
    podRuns: [{ podKey: "market_product", status: "planned", progressPercent: 0 }],
    taskGraph: { workflowTrack: "pf" },
    approvalReadinessGate: { pass: true, status: "ready" },
  };
  const savedPlan = {
    ...plan,
    podRuns: [{ podKey: "market_product", status: "completed", progressPercent: 100 }],
  };
  const executedPlan = {
    ...savedPlan,
    runAllSummary: {
      workflowTrack: "pf",
      requestedPodCount: 1,
      completedCount: 1,
      blockedCount: 0,
      rows: [],
    },
  };
  const runRequests: unknown[] = [];
  let saved = false;
  let releaseReadiness: () => void = () => {};
  const firstReadiness = new Promise<void>((resolve) => { releaseReadiness = resolve; });
  let readinessAvailable = false;
  let readinessAssessed = false;
  let legalWorkspaceAvailable = false;

  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    let status = 200;
    if (path.endsWith("/auth/me")) {
      body = {
        authenticated: true,
        user: { userId: "u", role: "admin", status: "active", tenantId: "test" },
        permissions: ["project:read", "project:analyze"],
      };
    } else if (path.endsWith("/chief-orchestration/plan")) {
      body = saved ? savedPlan : plan;
    } else if (path.endsWith("/chief-orchestration/approval-readiness")) {
      await firstReadiness;
      status = readinessAvailable ? 200 : 503;
      body = readinessAvailable
        ? {
          schemaVersion: "realytics-deal-state-approval-readiness-v1",
          projectId: "explicit-run", readable: true,
          assessmentStatus: readinessAssessed ? "assessed" : "unassessed",
          cureWriteAvailable: false,
          legalEvidenceSource: "normalized_postgres", legalEvidenceNormalizedReadable: true,
          legalReviewWriteAvailable: false, blockingIssueCount: 0, warningCount: 0,
        }
        : { detail: { message: "Dedicated legal-review DSN is unavailable." } };
    } else if (path.endsWith("/legal/workspace")) {
      status = legalWorkspaceAvailable ? 200 : 503;
      body = legalWorkspaceAvailable
        ? {
          schemaVersion: "realytics-legal-governance-workspace-v1",
          projectId: "explicit-run", source: "normalized_postgres", reviewWriteAvailable: false,
          matters: [], waivers: [], counselOpinions: [], analysisVersions: [],
          annotations: [], approvalDelegations: [], requiredClaimSets: [], tenantTermAliases: [],
        }
        : { detail: { error: "normalized_legal_store_unavailable", message: "Dedicated legal-review DSN is unavailable." } };
    } else if (path.endsWith("/legal/citator/review-queue")) {
      status = 503;
      body = { detail: { message: "Dedicated legal-review DSN is unavailable." } };
    } else if (path.endsWith("/chief-orchestration/run-all-pods")) {
      runRequests.push(route.request().postDataJSON());
      if (runRequests.length === 1) {
        status = 500;
        body = { detail: "전체 Pod 실행에 실패했습니다." };
      } else {
        saved = true;
        body = executedPlan;
      }
    } else if (path.endsWith("/workspace/context")) {
      body = { projectId: "explicit-run", projectMemory: {}, pageData: {} };
    } else if (path.endsWith("/projects/explicit-run")) {
      body = { project: { name: "합성 명시적 실행 검증" } };
    }
    await route.fulfill({ status, json: body });
  });

  await page.goto("/project/explicit-run/chief-orchestration?track=pf", { waitUntil: "domcontentloaded" });
  const runButton = page.getByRole("button", { name: "전체 자동 실행", exact: true });
  await expect(runButton).toBeInViewport({ timeout: 60_000 });
  await expect(page.getByText("분석 계획을 확인했습니다", { exact: true })).toBeVisible();
  const pendingReadiness = page.getByRole("region", { name: "승인 준비도를 불러오는 중입니다" });
  await expect(pendingReadiness).toBeVisible();
  await expect(pendingReadiness.getByRole("button", { name: "증빙 저장·재계산" })).toBeDisabled();
  releaseReadiness();
  const readiness = page.getByRole("region", { name: "승인 준비도를 불러올 수 없습니다" });
  await expect(readiness).toBeVisible();
  await expect(readiness.getByRole("button", { name: "증빙 저장·재계산" })).toBeDisabled();
  await expect(page.getByText("승인 가능성 검토 통과", { exact: true })).toHaveCount(0);
  const governance = page.getByRole("region", { name: "프로젝트 법률 거버넌스 워크스페이스" });
  const mattersTab = governance.getByRole("tab", { name: /^법률 이슈/ });
  await expect(governance.getByRole("alert")).toContainText("법률 기록 서버에 연결할 수 없습니다.");
  await expect(mattersTab).toContainText("—");
  await expect(page.getByText("Dedicated legal-review DSN", { exact: false })).toHaveCount(0);
  readinessAvailable = true;
  legalWorkspaceAvailable = true;
  await readiness.getByRole("button", { name: "승인 준비도 다시 조회" }).click();
  await expect(page.getByRole("heading", { name: "승인 준비도 평가 전입니다" })).toBeVisible();
  await expect(page.getByText("차단 리스크 0", { exact: false })).toHaveCount(0);
  await governance.getByRole("button", { name: "새로고침" }).click();
  await expect(mattersTab).toContainText("0");
  await governance.getByRole("tab", { name: /^필수 쟁점 정책/ }).click();
  await expect(governance.getByRole("button", { name: "정책 초안 제안" })).toBeDisabled();
  await governance.getByRole("tab", { name: /^Citator 검토/ }).click();
  await expect(governance.getByRole("alert")).toContainText("현재 서버에서 법률 인용 검토 기록을 확인하거나 저장할 수 없습니다.");
  await expect(governance.getByText("프로젝트 Citator 검토 대기 항목이 없습니다")).toHaveCount(0);
  expect(runRequests).toEqual([]);

  await runButton.click();
  await expect(page.getByRole("alert").filter({ hasText: "전체 Pod 실행에 실패했습니다." })).toBeVisible();
  await expect(page.getByText("분석 계획을 확인했습니다", { exact: true })).toBeVisible();
  await expect(runButton).toBeEnabled();
  expect(runRequests).toEqual([{ workflowTrack: "pf", options: {} }]);

  readinessAssessed = true;
  await runButton.click();
  const summary = page.getByText("전체 실행 완료", { exact: true });
  await expect(summary).toBeVisible();
  const assessed = page.getByRole("region", { name: "승인 전 보완이 필요합니다" });
  await expect(assessed.getByRole("status")).toContainText("현재 서버에서는 PF 보완 증빙을 저장하고 재계산할 수 없습니다.");
  await expect(assessed.getByRole("button", { name: "증빙 저장·재계산" })).toBeDisabled();
  await expect(page.getByText("전체 Pod 실행에 실패했습니다.", { exact: true })).toHaveCount(0);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: "새 자료 반영 재분석", exact: true })).toBeVisible();
  expect(runRequests).toEqual([
    { workflowTrack: "pf", options: {} },
    { workflowTrack: "pf", options: {} },
  ]);
});

test("saved agent assignments, material requests and execution evidence survive reload", async ({ page }) => {
  const requestedTracks: unknown[] = [];
  await page.addInitScript(() => localStorage.setItem("realytics.workflow.track.v1", "pf"));
  const plan = {
    projectId: "work-orders", sharedDealStateGraph: {}, agentPods: [],
    podRuns: [{ podKey: "market_product", status: "completed", progressPercent: 100 }],
    taskGraph: { workflowTrack: "development", workOrders: [
      { workOrderId: "w1", podKey: "market_product", toAgent: "MarketEvidenceAgent", reviewMode: "llm_evidence_aware_review",
        action: "collect_public_evidence", status: "executed", reason: "공개자료 수집 후 재검토 실행",
        requestedReason: "누락된 비교사례 확인", sourceReviewId: "saved-review-1", sourceArtifactKey: "saved-source-1", resultArtifactKey: "saved-result-1" },
      { workOrderId: "w2", podKey: "pf_credit", toAgent: "CreditReviewAgent", reviewMode: "deterministic_fallback_review",
        action: "request_material", status: "needs_material", reason: "실제 약정서 필요" },
      { workOrderId: "w3", podKey: "land_legal", toAgent: "LegalReviewAgent", reviewMode: "llm_evidence_aware_review",
        action: "rerun_pod", status: "queued", reason: "다음 실행에서 검토" },
    ], missingInputWorkPlan: [{ itemId: "loan", podKey: "pf_credit", agents: ["CreditReviewAgent"],
      description: "대출 약정 확인", acceptableSources: ["PF Term Sheet"], action: "request_material", limitation: "사용자 자료 필요" }] },
  };
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = { authenticated: true, user: { userId: "u", role: "admin", status: "active", tenantId: "test" }, permissions: ["project:read"] };
    else if (path.endsWith("/chief-orchestration/plan")) {
      requestedTracks.push(route.request().postDataJSON().workflowTrack);
      body = plan;
    }
    else if (path.includes("/pod-ai-reviews") || path.includes("/recalculation-events")) body = { records: [] };
    else if (path.includes("/legal-reviews") || path.includes("/permit-reviews")) body = { items: [] };
    else if (path.endsWith("/workspace/context")) body = { projectId: "work-orders", projectMemory: {}, pageData: {} };
    else if (path.endsWith("/projects/work-orders")) body = { project: { name: "합성 업무 배정 검증" } };
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/work-orders/chief-orchestration", { waitUntil: "domcontentloaded" });
  const panel = page.getByRole("region", { name: "추가 업무 배정" });
  await expect(panel).toBeVisible({ timeout: 60_000 });
  expect(requestedTracks.length).toBeGreaterThan(0);
  expect(requestedTracks.every((requestedTrack) => requestedTrack === null)).toBe(true);
  await panel.locator("summary").filter({ hasText: "재검토 실행 완료" }).click();
  await expect(panel).toContainText("LLM 검토 제안 → MarketEvidenceAgent");
  await expect(panel).toContainText("saved-result-1");
  await panel.locator("summary").filter({ hasText: "추가 자료 필요" }).click();
  await expect(panel).toContainText("규칙 기반 검토 → CreditReviewAgent");
  await expect(panel).toContainText("실제 약정서 필요");
  await panel.locator("summary").filter({ hasText: "Pod별 필요한 자료" }).click();
  await expect(panel).toContainText("PF Term Sheet");
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel).toContainText("재검토 실행 완료");
  await expect(panel).toContainText("실행 대기");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
