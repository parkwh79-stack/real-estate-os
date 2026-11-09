"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowLeft, Terminal } from "lucide-react";
import { useAuth } from "../../../../../components/auth/auth-context";
import { ProjectWorkspaceLink } from "../../../../../components/layout/project-workspace-link";
import { ChiefDealRoomMap } from "../../../../../components/orchestration/chief-deal-room-map";
import { hasPermission } from "../../../../../lib/authorization";
import { loadOnboardingStatus } from "../../../../../lib/auth-client";

type Access = "checking" | "allowed" | "denied" | "unavailable";

function DiagnosticsContent({ projectId }: { projectId: string }) {
  const [access, setAccess] = useState<Access>("checking");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    void loadOnboardingStatus()
      .then((identity) => {
        if (active) setAccess(identity.platformOperator === true ? "allowed" : "denied");
      })
      .catch(() => {
        if (active) setAccess("unavailable");
      });
    return () => { active = false; };
  }, [attempt]);

  if (access === "allowed") return <ChiefDealRoomMap projectId={projectId} />;
  if (access === "denied") return <AccessDenied />;
  if (access === "checking") {
    return <p role="status" className="rounded-re-panel border border-re-border bg-re-surface p-5 text-sm text-re-secondary">플랫폼 운영 권한을 확인하고 있습니다.</p>;
  }

  return (
    <section className="rounded-re-panel border border-re-border bg-re-surface p-5 text-sm text-re-secondary">
      <p role="alert">운영 권한을 확인하지 못했습니다. 잠시 후 다시 확인해 주세요.</p>
      <button
        type="button"
        className="mt-2 inline-flex min-h-11 items-center font-semibold text-re-action hover:underline"
        onClick={() => {
          setAccess("checking");
          setAttempt((value) => value + 1);
        }}
      >
        권한 다시 확인
      </button>
    </section>
  );
}

function AccessDenied() {
  return (
    <p role="alert" className="rounded-re-panel border border-re-border bg-re-surface p-5 text-sm text-re-secondary">
      개발자 진단은 이 프로젝트에 접근할 수 있는 플랫폼 운영자에게만 제공됩니다.
    </p>
  );
}

export default function ChiefDiagnosticsPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const { status, user, session, permissions } = useAuth();
  if (status !== "authenticated") return null;
  const canReadProject = Boolean(user) && hasPermission(permissions, "project:read");

  return (
    <div className="space-y-5 pb-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight text-re-text">
            <Terminal className="size-5" aria-hidden="true" />
            AI 딜룸 개발자 진단
          </h1>
          <p className="mt-1 text-sm text-re-secondary">프로젝트별 Pod 실행, 이벤트 및 검토 기록을 점검합니다.</p>
        </div>
        <ProjectWorkspaceLink
          projectId={projectId}
          path="chief-orchestration"
          className="inline-flex min-h-11 items-center gap-2 rounded-re-control border border-re-border px-3 text-sm font-semibold text-re-action hover:bg-re-subtle"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
          AI 딜룸으로 돌아가기
        </ProjectWorkspaceLink>
      </header>
      {canReadProject ? (
        <DiagnosticsContent
          key={JSON.stringify([projectId, user?.userId, user?.tenantId, session?.sessionId])}
          projectId={projectId}
        />
      ) : <AccessDenied />}
    </div>
  );
}
