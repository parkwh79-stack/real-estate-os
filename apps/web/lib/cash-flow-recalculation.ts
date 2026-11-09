import { apiBase, authHeaders } from "./auth-client";

export type CashFlowRecalculationBasis = "project" | "working";

export async function cashFlowRecalculationInput(projectId: string, basis: CashFlowRecalculationBasis) {
  if (basis === "project") return {};
  const response = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/feasibility-workbook/review`, {
    headers: authHeaders("web"), cache: "no-store",
  });
  const state = await response.json().catch(() => null);
  if (!response.ok || !state || typeof state.reviewHash !== "string" || !/^[a-f0-9]{64}$/.test(state.reviewHash)) {
    throw new Error("저장된 검토 입력을 확인하지 못했습니다. 입력 화면에서 저장 상태를 확인한 뒤 다시 계산해 주세요.");
  }
  return { workingInputReview: { expectedReviewHash: state.reviewHash } };
}
