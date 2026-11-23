import { expect, test } from "@playwright/test";

for (const [label, projectRead, platformOperator, projectAllowed] of [
  ["tenant admin", true, false, true],
  ["operator without project permission", false, true, false],
  ["operator with inaccessible project", true, true, false],
  ["authorized operator", true, true, true],
] as const) {
  test(`AI Deal Room diagnostics checks platform and project access for ${label}`, async ({ page }) => {
    let operatorChecks = 0;
    let plans = 0;
    await page.route("**/api/backend/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/auth/me")) {
        await route.fulfill({ json: {
          authenticated: true,
          user: { userId: "diagnostics-user", role: "admin", status: "active", tenantId: "diagnostics-tenant" },
          permissions: projectRead ? ["project:read", "project:analyze"] : ["user:manage"],
        } });
      } else if (path.endsWith("/auth/onboarding/status")) {
        operatorChecks += 1;
        await route.fulfill({ json: { platformOperator, profile: null, requests: [] } });
      } else if (path.endsWith("/chief-orchestration/plan")) {
        plans += 1;
        if (!projectAllowed) {
          await route.fulfill({ status: 403, json: { detail: "project_scope_denied" } });
        } else {
          await route.fulfill({ json: {
            projectId: "diagnostics",
            sharedDealStateGraph: {},
            agentPods: [],
            podRuns: [],
            taskGraph: { workflowTrack: "pf" },
          } });
        }
      } else if (path.endsWith("/workspace/context")) {
        await route.fulfill({ json: { projectId: "diagnostics", projectMemory: {}, pageData: {} } });
      } else {
        await route.fulfill({ json: {} });
      }
    });

    await page.goto("/project/diagnostics/chief-orchestration/diagnostics?track=pf", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "AI 딜룸 개발자 진단" })).toBeVisible();
    await expect(page.getByRole("link", { name: "AI 딜룸으로 돌아가기" }))
      .toHaveAttribute("href", "/project/diagnostics/chief-orchestration?track=pf");

    if (!projectRead || !platformOperator) {
      await expect(page.getByRole("alert").filter({ hasText: "플랫폼 운영자에게만 제공됩니다" })).toBeVisible();
      expect(plans).toBe(0);
      if (projectRead) expect(operatorChecks).toBeGreaterThan(0);
      else expect(operatorChecks).toBe(0);
    } else if (!projectAllowed) {
      await expect(page.getByText("AI 딜룸 계획 생성 실패")).toBeVisible();
      await expect(page.getByText("Task Planner & Event Bus", { exact: true })).toHaveCount(0);
    } else {
      await expect(page.getByText("Task Planner & Event Bus", { exact: true })).toBeVisible();
      expect(plans).toBeGreaterThan(0);
    }
  });
}
