"use client";

import Link from "next/link";
import { FolderOpen, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useAuth } from "../../components/auth/auth-context";
import { WorkInbox, type DashboardDataError } from "../../components/dashboard/work-inbox";
import { DashboardAtmosphere } from "../../components/dashboard/dashboard-atmosphere";
import { NewProjectDialog } from "../../components/intake/new-project-dialog";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { hasPermission, PROJECT_WRITE_PERMISSION } from "../../lib/authorization";
import { fetchRecentWorkFeed, projectOptionsFromResponse, type ProjectOption, type RecentWorkItem } from "../../lib/project-client";

async function loadProjects(signal: AbortSignal) {
  const response = await fetch(`${apiBase}/projects`, {
    cache: "no-store",
    headers: authHeaders("web"),
    signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]),
  });
  if (!response.ok) throw new Error(`프로젝트 목록을 불러오지 못했습니다. (${response.status})`);
  return projectOptionsFromResponse(await response.json());
}

export default function DashboardPage() {
  const { status, user, permissions } = useAuth();
  const canWriteProjects = hasPermission(permissions, PROJECT_WRITE_PERMISSION);
  const [items, setItems] = useState<RecentWorkItem[] | null>(null);
  const [projects, setProjects] = useState<ProjectOption[] | null>(null);
  const [errors, setErrors] = useState<DashboardDataError[]>([]);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (status !== "authenticated") return;
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setErrors([]);
    setProjects(null);
    setItems(null);
    Promise.allSettled([loadProjects(controller.signal), fetchRecentWorkFeed(20, controller.signal)])
      .then(([projectsResult, recentResult]) => {
        if (!active) return;
        const nextErrors: DashboardDataError[] = [];
        if (projectsResult.status === "fulfilled") setProjects(projectsResult.value);
        else nextErrors.push({ source: "projects", message: "프로젝트 목록을 불러오지 못했습니다. 다시 확인해 주세요." });
        if (recentResult.status === "fulfilled") {
          setItems(recentResult.value.items);
          nextErrors.push(...recentResult.value.warnings.map((message) => ({ source: "recent-work" as const, message })));
        } else nextErrors.push({ source: "recent-work", message: "최근 작업을 불러오지 못했습니다. 다시 확인해 주세요." });
        setErrors(nextErrors);
        setLoading(false);
      });
    return () => { active = false; controller.abort(); };
  }, [status, user?.userId, user?.tenantId, reloadKey]);

  const firstProject = !loading && errors.length === 0 && projects?.length === 0 && items?.length === 0;

  return (
    <div className="mx-auto max-w-7xl space-y-6 pb-8">
      <div className="re-dashboard-intro">
        <DashboardAtmosphere />
        <header className="re-dashboard-glass flex flex-wrap items-center justify-between gap-4 rounded-re-panel px-5 py-5 sm:px-6" data-testid="dashboard-glass-header">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-re-text sm:text-3xl">대시보드</h1>
            <p className="mt-2 text-sm leading-6 text-re-secondary">
              {firstProject && canWriteProjects ? "보유한 자료로 첫 프로젝트를 시작하세요." : "진행하던 프로젝트를 이어서 검토하세요."}
            </p>
          </div>
          {canWriteProjects ? (
            <NewProjectDialog key={`${user?.tenantId}:${user?.userId}`} firstProject={firstProject} onProjectCreated={() => setReloadKey((value) => value + 1)} />
          ) : (
            <button type="button" disabled data-testid="dashboard-new-deal-disabled"
              className="min-h-11 rounded-re-control border border-re-border px-4 text-sm text-re-muted disabled:cursor-not-allowed">
              새 프로젝트 · 조회 전용
            </button>
          )}
        </header>
      </div>

      {!canWriteProjects ? (
        <div data-testid="review-only-notice" role="status" className="rounded-re-control bg-re-subtle px-4 py-3 text-sm text-re-secondary">
          <p data-testid="dashboard-intake-read-only">현재 계정에서는 기존 프로젝트를 검토할 수 있으며, 새 프로젝트 생성과 자료 업로드는 사용할 수 없습니다.</p>
        </div>
      ) : null}

      <div id="projects" className="scroll-mt-24">
        {firstProject ? (
          <section aria-labelledby="first-project-title" className="rounded-re-panel border border-re-border bg-re-surface px-6 py-10 sm:px-8">
            <FolderOpen className="mb-4 h-8 w-8 text-re-action" aria-hidden="true" />
            <h2 id="first-project-title" className="text-lg font-semibold text-re-text">
              {canWriteProjects ? "자료를 올리고 검토를 시작하세요" : "아직 표시할 프로젝트가 없습니다"}
            </h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-re-secondary">
              {canWriteProjects
                ? "사업계획서·사업수지·토지자료를 한 번에 올릴 수 있습니다. 확인된 자료와 분석 가정을 검토하고 결과를 저장해 이어서 작업하세요."
                : "접근 가능한 프로젝트가 등록되면 이곳에서 확인할 수 있습니다."}
            </p>
          </section>
        ) : (
          <WorkInbox items={items} totalProjectCount={projects?.length ?? null} loading={loading} errors={errors}
            onRetry={() => setReloadKey((value) => value + 1)} />
        )}
      </div>

      <AllProjects key={`${user?.tenantId}:${user?.userId}`} projects={projects} loading={loading} onRetry={() => setReloadKey((value) => value + 1)} />
    </div>
  );
}

function AllProjects({ projects, loading, onRetry }: { projects: ProjectOption[] | null; loading: boolean; onRetry: () => void }) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => {
    const openFromHash = () => {
      const details = document.getElementById("all-projects");
      if (window.location.hash === "#all-projects" && details instanceof HTMLDetailsElement) details.open = true;
    };
    openFromHash();
    window.addEventListener("hashchange", openFromHash);
    window.addEventListener("popstate", openFromHash);
    return () => {
      window.removeEventListener("hashchange", openFromHash);
      window.removeEventListener("popstate", openFromHash);
    };
  }, []);
  const matched = (projects ?? []).filter((project) => [project.name, project.address, project.pnu, project.id]
    .some((value) => value?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
  const lastPage = Math.max(0, Math.ceil(matched.length / 12) - 1);
  const currentPage = Math.min(page, lastPage);
  return (
    <details id="all-projects" className="scroll-mt-24 rounded-re-panel border border-re-border bg-re-surface">
      <summary className="cursor-pointer px-4 py-4 text-sm font-semibold text-re-text">
        전체 프로젝트 <span className="ml-1 font-normal text-re-secondary">{loading ? "불러오는 중" : projects ? `${projects.length.toLocaleString("ko-KR")}개` : "확인 불가"}</span>
      </summary>
      <div className="space-y-4 border-t border-re-border p-4">
        {loading ? <p role="status" className="text-sm text-re-secondary">프로젝트를 불러오는 중입니다.</p> : projects === null ? (
          <div role="status" className="text-sm text-re-secondary">
            <p>프로젝트 목록을 확인할 수 없습니다.</p>
            <button type="button" onClick={onRetry} className="mt-2 min-h-10 rounded-re-control border border-re-border px-3 font-medium">다시 불러오기</button>
          </div>
        ) : (
          <>
            <label className="flex min-h-11 max-w-lg items-center gap-2 rounded-re-control border border-re-border px-3">
              <Search className="h-4 w-4 shrink-0 text-re-muted" aria-hidden="true" />
              <span className="sr-only">전체 프로젝트 검색</span>
              <input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }}
                placeholder="프로젝트명·주소 검색" className="min-w-0 flex-1 bg-transparent py-2 text-base text-re-text sm:text-sm" />
            </label>
            <p className="text-xs text-re-secondary" role="status">현재 접근 가능한 전체 프로젝트 중 {matched.length.toLocaleString("ko-KR")}개</p>
            <ul className="divide-y divide-re-border">
              {matched.slice(currentPage * 12, currentPage * 12 + 12).map((project) => (
                <li key={project.id}>
                  <Link href={`/project/${encodeURIComponent(project.id)}`} prefetch={false} className="block rounded-re-control px-2 py-3 hover:bg-re-subtle">
                    <span className="break-words text-sm font-medium text-re-text">{project.name}</span>
                    {project.address ? <span className="mt-1 block break-words text-xs text-re-secondary">{project.address}</span> : null}
                  </Link>
                </li>
              ))}
            </ul>
            {matched.length === 0 ? <p className="text-sm text-re-secondary">{projects.length ? "검색 결과가 없습니다." : "등록된 프로젝트가 없습니다."}</p> : null}
            {lastPage > 0 ? (
              <div className="flex items-center justify-end gap-3 text-sm">
                <button type="button" onClick={() => setPage(currentPage - 1)} disabled={currentPage === 0} className="min-h-10 rounded-re-control border border-re-border px-3 disabled:opacity-40">이전</button>
                <span>{currentPage + 1} / {lastPage + 1}</span>
                <button type="button" onClick={() => setPage(currentPage + 1)} disabled={currentPage === lastPage} className="min-h-10 rounded-re-control border border-re-border px-3 disabled:opacity-40">다음</button>
              </div>
            ) : null}
          </>
        )}
      </div>
    </details>
  );
}
