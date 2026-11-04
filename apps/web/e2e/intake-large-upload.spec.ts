import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const PART = 3 * 1024 * 1024;
const classified = (filename: string) => ({ filename, documentType: "business_plan", label: "사업계획서", confidence: 1, needsReview: false, archiveStored: true });

async function fixture(page: Page) {
  const state = { parts: new Map<number, Buffer>(), partCalls: [] as number[], manifests: [] as Record<string, unknown>[],
    classifyCalls: 0, processCalls: 0, reviewCalls: 0, cleanupCalls: 0, archivedReview: false,
    smallBatches: [] as string[][], failPart: -1, expired: false };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/\/$/, "");
    const payload = request.postDataBuffer();
    // Simulate the platform rejecting any request that still carries a whole document.
    if ((payload?.length ?? 0) > 4 * 1024 * 1024) return route.fulfill({ status: 413, body: "FUNCTION_PAYLOAD_TOO_LARGE" });
    const reply = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path.endsWith("/auth/me")) return reply({ authenticated: true, authSource: "bearer_token",
      user: { userId: "upload-user", tenantId: "upload-tenant", role: "analyst", status: "active" },
      session: { userId: "upload-user", tenantId: "upload-tenant" }, permissions: ["project:read", "project:write"] });
    if (path.endsWith("/projects/recent-work")) return reply({ items: [] });
    if (path.endsWith("/projects")) return reply(request.method() === "POST" ? { id: "upload-project" } : []);
    if (path.endsWith("/intake/transfers")) {
      state.manifests.push(request.postDataJSON());
      const manifest = state.manifests.at(-1)!;
      return reply({ transferId: "transfer-1", partSizeBytes: PART,
        partCount: Math.ceil(Number(manifest.sizeBytes) / PART), expiresAt: new Date(Date.now() + 3_600_000).toISOString() });
    }
    const part = /\/transfers\/transfer-1\/parts\/(\d+)$/.exec(path);
    if (part) {
      const index = Number(part[1]); state.partCalls.push(index);
      if (state.failPart === index) return reply({ detail: "temporary upload failure" }, 503);
      state.parts.set(index, payload!);
      return reply({ stored: true });
    }
    if (path.endsWith("/transfers/transfer-1/classify")) {
      state.classifyCalls += 1;
      if (state.expired) return reply({ detail: "transfer expired" }, 410);
      const source = Buffer.concat([...state.parts.entries()].sort(([a], [b]) => a - b).map(([, bytes]) => bytes));
      const manifest = state.manifests.at(-1)!;
      expect(source.length).toBe(manifest.sizeBytes);
      expect(createHash("sha256").update(source).digest("hex")).toBe(manifest.sha256);
      return reply({ files: [{ ...classified(String(manifest.filename)), ...(state.archivedReview ? {
        archiveId: "d".repeat(64), processingMode: "document_review", processingTypes: ["business_plan"],
      } : {}) }] });
    }
    if (path.endsWith("/transfers/transfer-1/process")) {
      state.processCalls += 1;
      expect(request.postDataJSON()).toEqual({ documentType: "business_plan" });
      return reply({ project: { id: "upload-project" } });
    }
    if (path.endsWith("/parsing-v2/runs")) {
      state.reviewCalls += 1;
      expect(request.postDataJSON()).toEqual({ archiveId: "d".repeat(64) });
      return reply({ candidateOnly: true, authoritative: false, parsingRun: { parsingRunId: "large-review-run", executionStatus: "completed" } }, 201);
    }
    if (path.endsWith("/transfers/transfer-1") && request.method() === "DELETE") {
      state.cleanupCalls += 1;
      return reply({ detail: "temporary transfer cleanup unavailable" }, 503);
    }
    if (path.endsWith("/intake/classify")) {
      const names = [...(payload?.toString("utf8") ?? "").matchAll(/filename="([^"]+)"/g)].map((match) => match[1]!);
      state.smallBatches.push(names);
      return reply({ files: names.map(classified) });
    }
    return reply({ detail: "unmocked" }, 404);
  });
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "첫 프로젝트 시작", exact: true }).click();
  return { state, dialog: page.getByRole("dialog") };
}

test("a document above the platform limit uses bounded parts, preserves bytes, and processes by reference", async ({ page }) => {
  const { state, dialog } = await fixture(page);
  const bytes = Buffer.alloc(PART * 2 + 321, 97);
  Buffer.from("%PDF-1.7 synthetic original evidence").copy(bytes);
  await dialog.locator('input[type="file"]').setInputFiles({ name: "large-review.pdf", mimeType: "application/pdf", buffer: bytes });
  await expect(dialog.getByText("large-review.pdf", { exact: true })).toBeVisible();
  expect(state.partCalls).toEqual([0, 1, 2]);
  expect(state.smallBatches).toHaveLength(0);
  await dialog.getByRole("button", { name: /^자료 처리/ }).click();
  await expect(dialog.getByText(/처리 완료/)).toBeVisible();
  expect(state.processCalls).toBe(1);
  expect(state.partCalls).toEqual([0, 1, 2]);
});

test("several individually small documents are batched below the platform limit in input order", async ({ page }) => {
  const { state, dialog } = await fixture(page);
  await dialog.locator('input[type="file"]').setInputFiles(["first.pdf", "second.pdf", "third.pdf"].map((name) => ({
    name, mimeType: "application/pdf", buffer: Buffer.alloc(2 * 1024 * 1024, 98),
  })));
  await expect(dialog.getByText("third.pdf", { exact: true })).toBeVisible();
  expect(state.smallBatches).toEqual([["first.pdf"], ["second.pdf"], ["third.pdf"]]);
  expect(state.manifests).toHaveLength(0);
  const tableNames = await dialog.getByRole("listitem").locator("p").filter({ hasText: /^(first|second|third)\.pdf$/ }).allTextContents();
  expect(tableNames.map((name) => name.trim())).toEqual(["first.pdf", "second.pdf", "third.pdf"]);
});

test("a chunked PDF prepares review from its archive and stays completed if transfer cleanup is unavailable", async ({ page }) => {
  const { state, dialog } = await fixture(page);
  state.archivedReview = true;
  const bytes = Buffer.alloc(PART + 123, 97);
  Buffer.from("%PDF-1.7 synthetic original evidence").copy(bytes);
  await dialog.locator('input[type="file"]').setInputFiles({ name: "archive-review.pdf", mimeType: "application/pdf", buffer: bytes });
  const row = dialog.getByRole("listitem").filter({ hasText: "archive-review.pdf" });
  await expect(row).toContainText("처리 대기");
  await expect(row.getByRole("combobox")).toHaveCount(0);
  await dialog.getByRole("button", { name: /^자료 처리/ }).click();
  await expect(row).toContainText("문서 검토 준비");
  await expect.poll(() => state.cleanupCalls).toBe(1);
  expect(state.reviewCalls).toBe(1);
  expect(state.processCalls).toBe(0);
  expect(state.partCalls).toEqual([0, 1]);
  await expect(row.getByRole("alert")).toHaveCount(0);
});

test("an explicit retry keeps successful parts and resumes the failed part with the original bytes", async ({ page }) => {
  const { state, dialog } = await fixture(page);
  state.failPart = 1;
  await dialog.locator('input[type="file"]').setInputFiles({ name: "retry.pdf", mimeType: "application/pdf", buffer: Buffer.alloc(PART + 77, 99) });
  await expect(dialog.getByRole("alert")).toBeVisible();
  expect(state.partCalls).toEqual([0, 1]);
  state.failPart = -1;
  await dialog.getByRole("button", { name: "분류 다시 시도", exact: true }).click();
  await expect(dialog.getByText("retry.pdf", { exact: true })).toBeVisible();
  expect(state.manifests).toHaveLength(1);
  expect(state.partCalls).toEqual([0, 1, 1]);
});
