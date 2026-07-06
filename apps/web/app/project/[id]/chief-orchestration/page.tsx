"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, GitBranch, Upload } from "lucide-react";
import { ChiefDealRoomMap } from "../../../../components/orchestration/chief-deal-room-map";

export default function ChiefOrchestrationPage() {
  const params = useParams<{ id: string }>();
  const projectId = String(params.id);

  return (
    <div className="space-y-5 pb-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <Link
            href={`/project/${projectId}`}
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            aria-label="프로젝트로 돌아가기"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">프로젝트 #{projectId}</p>
            <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              Chief Deal Orchestrator
            </h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
              부동산 개발·PF 심사를 바로 결론내지 않고, Deal Intake Router가 업무 트랙과 자료 수준을 먼저 판단합니다.
              이후 Shared Deal State Graph를 중심으로 Agent Pod 실행, 자동 보강, 교차 검증, Risk Committee 판단까지 단계적으로 진행합니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href={`/project/${projectId}/input-package`}
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            <Upload className="h-4 w-4" />
            자료 보강
          </Link>
          <Link
            href={`/project/${projectId}/orchestration`}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-bold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950"
          >
            <GitBranch className="h-4 w-4" />
            실행 콘솔
          </Link>
        </div>
      </div>

      <ChiefDealRoomMap projectId={projectId} />
    </div>
  );
}
