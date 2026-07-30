"use client";

import Link from "next/link";
import { ArrowDown, UploadCloud } from "lucide-react";
import { useEffect, useState } from "react";
import { useAuth } from "../../components/auth/auth-context";
import { ReviewOnlyNotice } from "../../components/auth/review-only-notice";
import {
  WorkInbox,
  type DashboardDataError,
} from "../../components/dashboard/work-inbox";
import { DashboardIntake } from "../../components/intake/dashboard-intake";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { hasPermission, PROJECT_WRITE_PERMISSION } from "../../lib/authorization";
import type { RecentWorkItem } from "../../lib/project-client";

interface RecentWorkResponse {
  items?: RecentWorkItem[];
}

async function loadProjectCount(signal: AbortSignal) {
  const response = await fetch(`${apiBase}/projects`, {
    cache: "no-store",
    headers: authHeaders("web"),
    signal,
  });
  if (!response.ok) throw new Error(`프로젝트 목록 요청 실패 (${response.status})`);
  const body: unknown = await response.json();
  if (!Array.isArray(body)) throw new Error("프로젝트 목록 응답 형식이 올바르지 않습니다.");
  return body.length;
}

async function loadRecentWork(signal: AbortSignal) {
  const response = await fetch(`${apiBase}/projects/recent-work?limit=20`, {
    cache: "no-store",
    headers: authHeaders("web"),
    signal,
  });
  if (!response.ok) throw new Error(`최근 작업 요청 실패 (${response.status})`);
  const body = (await response.json()) as RecentWorkResponse;
  if (!Array.isArray(body.items)) throw new Error("최근 작업 응답 형식이 올바르지 않습니다.");
  return body.items;
}

export default function DashboardPage() {
  const { status, user, permissions } = useAuth();
  const canWriteProjects = hasPermission(permissions, PROJECT_WRITE_PERMISSION);
  const [items, setItems] = useState<RecentWorkItem[] | null>(null);
  const [totalProjectCount, setTotalProjectCount] = useState<number | null>(null);
  const [errors, setErrors] = useState<DashboardDataError[]>([]);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (status !== "authenticated") return;
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setErrors([]);

    Promise.allSettled([
      loadProjectCount(controller.signal),
      loadRecentWork(controller.signal),
    ]).then(([projectsResult, recentWorkResult]) => {
      if (!active) return;
      const nextErrors: DashboardDataError[] = [];
      if (projectsResult.status === "fulfilled") {
        setTotalProjectCount(projectsResult.value);
      } else {
        setTotalProjectCount(null);
        nextErrors.push({
          source: "projects",
          message:
            projectsResult.reason instanceof Error
              ? projectsResult.reason.message
              : "프로젝트 목록을 불러오지 못했습니다.",
        });
      }
      if (recentWorkResult.status === "fulfilled") {
        setItems(recentWorkResult.value);
      } else {
        setItems(null);
        nextErrors.push({
          source: "recent-work",
          message:
            recentWorkResult.reason instanceof Error
              ? recentWorkResult.reason.message
              : "최근 작업을 불러오지 못했습니다.",
        });
      }
      setErrors(nextErrors);
      setLoading(false);
    });

    return () => {
      active = false;
      controller.abort();
    };
  }, [status, reloadKey]);

  const role = user?.role ? user.role.replaceAll("_", " ") : undefined;

  return (
    <div className="space-y-8 pb-10">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2 text-xs font-bold text-re-muted">
            <span>REALYTICS DEAL OPERATIONS</span>
            {role ? (
              <span className="rounded-re-control border border-re-border bg-re-subtle px-2 py-1 text-re-secondary">
                역할 {role}
              </span>
            ) : null}
          </div>
          <h1 className="mt-2 text-3xl font-black tracking-tight text-re-text">
            {user?.displayName ? `${user.displayName}님의 검토 업무함` : "검토 업무함"}
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-re-secondary">
            현재 Tenant에서 접근 가능한 실제 프로젝트의 실행 상태와 승인 상태를 분리해 보여줍니다.
            수치가 없는 영역에는 추정값을 채우지 않습니다.
          </p>
        </div>
        {canWriteProjects ? (
          <Link
            href="#new-deal-intake"
            className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-re-control bg-re-brand px-4 text-sm font-bold text-white hover:opacity-90"
          >
            <UploadCloud className="h-4 w-4" aria-hidden="true" />
            새 딜 시작
            <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>
        ) : (
          <button
            type="button"
            disabled
            title={`필요 권한: ${PROJECT_WRITE_PERMISSION}`}
            data-testid="dashboard-new-deal-disabled"
            className="inline-flex h-10 shrink-0 cursor-not-allowed items-center justify-center gap-2 rounded-re-control border border-re-border bg-re-subtle px-4 text-sm font-bold text-re-muted"
          >
            <UploadCloud className="h-4 w-4" aria-hidden="true" />
            심사 전용 · 새 딜 생성 불가
          </button>
        )}
      </header>

      {!canWriteProjects ? <ReviewOnlyNotice role={user?.role} tone="light" /> : null}

      <WorkInbox
        items={items}
        totalProjectCount={totalProjectCount}
        loading={loading}
        errors={errors}
        onRetry={() => setReloadKey((value) => value + 1)}
      />

      <section id="new-deal-intake" aria-labelledby="new-deal-title" className="scroll-mt-24 space-y-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-re-muted">New deal intake</p>
          <h2 id="new-deal-title" className="mt-1 text-xl font-black tracking-tight text-re-text">
            문서 업로드로 새 딜 시작
          </h2>
          <p className="mt-1 text-sm text-re-secondary">
            기존 업로드·프로젝트 생성 흐름을 유지합니다. 생성된 프로젝트는 처리 후 위 업무함에 반영됩니다.
          </p>
        </div>
        {canWriteProjects ? (
          <DashboardIntake onProjectCreated={() => setReloadKey((value) => value + 1)} />
        ) : (
          <div
            data-testid="dashboard-intake-read-only"
            className="rounded-2xl border border-dashed border-re-border bg-re-subtle px-6 py-10 text-center"
          >
            <UploadCloud className="mx-auto h-9 w-9 text-re-muted" aria-hidden="true" />
            <p className="mt-3 text-sm font-black text-re-text">심사 전용 역할에서는 새 자료를 업로드할 수 없습니다.</p>
            <p className="mt-1 text-xs leading-5 text-re-secondary">
              위 업무함에서 배정된 프로젝트를 선택해 심사·검토를 계속하세요. 새 딜 생성에는
              {" "}<span className="font-mono font-bold">{PROJECT_WRITE_PERMISSION}</span> 권한이 필요합니다.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
