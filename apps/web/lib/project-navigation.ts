import { TRACK_LABELS } from "./workflow-track.ts";

export const PROJECT_WORKSPACE_GROUPS = [
  {
    key: "overview", label: "딜 개요", routes: [
      { path: "", label: "프로젝트 개요" },
      { path: "chief-orchestration", label: "AI 딜룸" },
    ],
  },
  {
    key: "diligence", label: "실사·근거", routes: [
      { path: "input-package", label: "자료 입력" },
      { path: "documents/review", label: "문서 검토" },
      { path: "land", label: "토지" },
      { path: "map", label: "지도" },
      { path: "map-3d", label: "3D 지도" },
      { path: "evidence", label: "근거" },
      { path: "kakao-intake", label: "카카오 자료 접수" },
    ],
  },
  {
    key: "feasibility", label: "사업성", routes: [
      { path: "feasibility", label: "프로젝트 사업성" },
      { path: "marketability", label: "시장성" },
      { path: "asset-comparison", label: "자산 비교" },
    ],
  },
  {
    key: "finance", label: "금융구조", routes: [
      { path: "cashflow", label: "현금흐름" },
      { path: "pf-structure", label: "PF 구조" },
    ],
  },
  {
    key: "risk", label: "리스크·승인", routes: [
      { path: "risk", label: "위험 항목 검토" },
      { path: "orchestration", label: "PF 심사 실행·승인" },
      { path: "mobile", label: "모바일 검토" },
    ],
  },
  {
    key: "report", label: "보고서·감사", routes: [
      { path: "report", label: "심사보고서" },
      { path: "report-templates", label: "보고서 템플릿" },
      { path: "audit", label: "감사 이력" },
    ],
  },
] as const;

export function projectWorkspaceHref(projectId: string, path = "", track?: string | null): string {
  const base = `/project/${encodeURIComponent(projectId)}`;
  const pathname = path ? `${base}/${path}` : base;
  return track && Object.hasOwn(TRACK_LABELS, track) ? `${pathname}?track=${encodeURIComponent(track)}` : pathname;
}

export function projectWorkspaceLocation(pathname: string, projectId: string) {
  const path = pathname.match(/^\/project\/[^/]+(?:\/(.*))?$/)?.[1]?.replace(/\/+$/, "") ?? "";
  if (projectIdFromLocation(pathname, "") !== projectId) return null;
  for (const group of PROJECT_WORKSPACE_GROUPS) {
    const route = group.routes.find((item) => item.path === path || (item.path && path.startsWith(`${item.path}/`)));
    if (route) return { group, route };
  }
  return null;
}

/** URL context wins over remembered selection; never invent a project ID. */
export function projectIdFromLocation(pathname: string, search: string): string | null {
  const pathId = pathname.match(/^\/project\/([^/]+)(?:\/|$)/)?.[1];
  const value = pathId ?? new URLSearchParams(search).get("projectId");
  if (!value) return null;
  try {
    const decoded = pathId ? decodeURIComponent(value) : value;
    return /^[A-Za-z0-9:_-]+$/.test(decoded) ? decoded : null;
  } catch {
    return null;
  }
}

export function withProjectContext(href: string, projectId: string | null): string {
  if (!projectId || !href.startsWith("/") || href.startsWith("//")) return href;
  const url = new URL(href, "http://local.invalid");
  url.searchParams.set("projectId", projectId);
  return `${url.pathname}${url.search}${url.hash}`;
}
