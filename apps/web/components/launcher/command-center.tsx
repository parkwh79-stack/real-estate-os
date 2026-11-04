"use client";

import { useCallback, useId, useRef, useState, type RefObject } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  BarChart3,
  Building2,
  CheckCircle2,
  Landmark,
  LayoutDashboard,
  Layers3,
  LogOut,
  Loader2,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  Wallet,
} from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import {
  hasPermission,
  PROJECT_WRITE_PERMISSION,
  projectWriteRestrictionMessage,
} from "../../lib/authorization";
import { backendErrorMessageForResponse } from "../../lib/backend-error";
import { validateIntakeFiles } from "../../lib/intake-upload-client";
import { getTrackWorkflowDefinition, setStoredWorkflowTrack, TRACK_LABELS, type WorkflowTrack } from "../../lib/workflow-track";
import { useAuth } from "../auth/auth-context";
import { ReviewOnlyNotice } from "../auth/review-only-notice";
import { DashboardAtmosphere } from "../dashboard/dashboard-atmosphere";
import { SmartIntake, type IntakeWorkStatus } from "../intake/smart-intake";
import { intakeProcessingFailure } from "../../lib/intake-processing-status";
import { RecentWorkPanel } from "./recent-work-panel";
import { ProjectNameEditor, PROJECT_NAME_MAX_LENGTH } from "../project/project-name-editor";

type Phase = "idle" | "creating" | "resume" | "ready" | "error";
type IntakeStage = "preparing" | "checking-history" | "creating-workspace";

const INTAKE_STAGE_COPY: Record<IntakeStage, { title: string; detail: string }> = {
  preparing: {
    title: "선택한 자료를 준비하고 있습니다",
    detail: "브라우저에서 원본을 다시 복사하지 않고 안전하게 처리합니다.",
  },
  "checking-history": {
    title: "이전에 검토한 자료인지 확인하고 있습니다",
    detail: "파일 지문만 비교하며, 원문은 아직 업로드하지 않습니다.",
  },
  "creating-workspace": {
    title: "새 심사 공간을 만들고 있습니다",
    detail: "생성이 끝나면 선택한 자료의 분류와 검토를 이어갑니다.",
  },
};

type Classified = {
  filename: string;
  documentType: string;
  label: string;
  confidence: number;
  needsReview: boolean;
  reasons?: string[];
};

type TriageTrack = {
  track: WorkflowTrack;
  label: string;
  title: string;
  description: string;
  entryHref: string;
  score: number;
  confidence: number;
  recommended: boolean;
  rationale: string;
  reasons: string[];
};

type TriageResult = {
  recommendedTrack: WorkflowTrack;
  recommendationSource: string;
  aiAssisted: boolean;
  aiError?: string;
  persisted?: boolean;
  fallbackReason?: string;
  confidenceBasis?: string;
  tracks: TriageTrack[];
  limitations?: string[];
};

type ContinuationMatch = {
  projectId: string;
  projectName: string;
  matchedFiles: Array<{ fileName?: string }>;
};

const TRACK_META: Record<WorkflowTrack, Pick<TriageTrack, "label" | "title" | "description" | "entryHref">> = {
  development: {
    label: TRACK_LABELS.development,
    title: TRACK_LABELS.development,
    description: "토지, 인허가, 사업수지, 분양수입, 개발 가능성을 먼저 검토합니다.",
    entryHref: "input-package",
  },
  pf: {
    label: TRACK_LABELS.pf,
    title: TRACK_LABELS.pf,
    description: "PF 구조, Tranche, 상환재원, Exit 분양률, 심사보고서 작성 가능성을 검토합니다.",
    entryHref: "chief-orchestration",
  },
  finance: {
    label: TRACK_LABELS.finance,
    title: TRACK_LABELS.finance,
    description: "담보대출, 리파이낸싱, 대출구조, 회수 가능성을 검토합니다.",
    entryHref: "input-package",
  },
  real_asset_finance: {
    label: TRACK_LABELS.real_asset_finance,
    title: TRACK_LABELS.real_asset_finance,
    description: "임대료, NOI, Cap Rate, 실물자산 인수와 담보가치를 검토합니다.",
    entryHref: "input-package",
  },
  reit: {
    label: TRACK_LABELS.reit,
    title: TRACK_LABELS.reit,
    description: "편입자산, 배당재원, 감정가, 운용구조와 공시 리스크를 검토합니다.",
    entryHref: "input-package",
  },
  fund: {
    label: TRACK_LABELS.fund,
    title: TRACK_LABELS.fund,
    description: "펀드 설정, 투자대상, 대출/에쿼티 구조, 운용·회수 리스크를 검토합니다.",
    entryHref: "input-package",
  },
};

const TRACK_ICONS: Record<WorkflowTrack, typeof Building2> = {
  development: Building2,
  pf: Landmark,
  finance: Wallet,
  real_asset_finance: BarChart3,
  reit: Layers3,
  fund: Wallet,
};

const DOCUMENT_TYPE_LABEL: Record<string, string> = {
  business_plan: "사업계획서 / IM",
  basic_architectural_plan: "건축 기본계획",
  land_ledger: "토지대장 / 토지조서",
  feasibility_workbook: "사업수지 / Cash Flow Excel",
  risk_report_workbook: "리스크 보고서 Excel",
  land_evidence: "토지 증빙",
  market_evidence: "시장·거래 증빙",
  unknown: "미분류",
};

function deriveProjectName(files: File[]): string {
  const base = (files[0]?.name ?? "").replace(/\.[^.]+$/, "").trim();
  if (base) return base.length > 60 ? base.slice(0, 60) : base;
  const d = new Date();
  return `신규 심사 ${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function buildLocalTriage(classified: Classified[], fallbackReason: string): TriageResult {
  const weights: Record<string, Partial<Record<WorkflowTrack, number>>> = {
    business_plan: { development: 3, pf: 2 },
    basic_architectural_plan: { development: 3, pf: 1 },
    land_ledger: { development: 2.5, pf: 1.5, real_asset_finance: 1 },
    feasibility_workbook: { development: 2.5, pf: 3, finance: 1 },
    risk_report_workbook: { pf: 3, finance: 2, fund: 1 },
    land_evidence: { real_asset_finance: 2.5, development: 1, pf: 1 },
    market_evidence: { real_asset_finance: 2, development: 1, pf: 1, reit: 1 },
  };
  const scores: Record<WorkflowTrack, number> = {
    development: 0,
    pf: 0,
    finance: 0,
    real_asset_finance: 0,
    reit: 0,
    fund: 0,
  };
  const reasons: Record<WorkflowTrack, string[]> = {
    development: [],
    pf: [],
    finance: [],
    real_asset_finance: [],
    reit: [],
    fund: [],
  };

  for (const item of classified) {
    const confidence = Math.max(0.25, Math.min(1, item.confidence || 0));
    const docWeights = weights[item.documentType] ?? {};
    for (const [track, weight] of Object.entries(docWeights) as Array<[WorkflowTrack, number]>) {
      const add = Number((weight * confidence).toFixed(3));
      scores[track] += add;
      reasons[track].push(`${DOCUMENT_TYPE_LABEL[item.documentType] ?? item.label ?? item.documentType}: 가중치 ${weight} x 분류 신뢰도 ${Math.round(confidence * 100)}%`);
    }

    const name = item.filename.toLowerCase();
    if (name.includes("pf")) {
      scores.pf += 2;
      reasons.pf.push("파일명에 PF 신호 포함");
    }
    if (name.includes("reit") || name.includes("리츠")) {
      scores.reit += 3;
      reasons.reit.push("파일명에 리츠/REIT 신호 포함");
    }
    if (name.includes("fund") || name.includes("펀드")) {
      scores.fund += 3;
      reasons.fund.push("파일명에 펀드/Fund 신호 포함");
    }
  }

  const recommendedTrack = (Object.keys(scores) as WorkflowTrack[]).sort((a, b) => scores[b] - scores[a])[0] ?? "development";
  const maxScore = Math.max(...Object.values(scores), 1);
  const tracks = (Object.keys(TRACK_META) as WorkflowTrack[])
    .map((track) => {
      const score = Number(scores[track].toFixed(3));
      return {
        ...TRACK_META[track],
        track,
        score,
        confidence: Math.min(0.98, Number((score / maxScore).toFixed(2))),
        recommended: track === recommendedTrack,
        rationale:
          track === recommendedTrack
            ? "업로드 문서유형, 파일명 신호, 분류 신뢰도를 기준으로 가장 관련성이 높습니다."
            : "일부 관련 신호는 있으나 추천 트랙보다 근거가 약합니다.",
        reasons: reasons[track].slice(0, 5),
      };
    })
    .sort((a, b) => Number(b.recommended) - Number(a.recommended) || b.score - a.score);

  return {
    recommendedTrack,
    recommendationSource: "local_fallback",
    aiAssisted: false,
    persisted: false,
    fallbackReason,
    confidenceBasis: "문서유형별 업무트랙 가중치 x 자동분류 신뢰도 + 파일명 키워드 신호",
    tracks,
    limitations: [
      "서버 트랙 추론이 실패해 브라우저에서 임시 추천을 계산했습니다. 워크스페이스 진입 후 다시 실행하면 DB에 저장됩니다.",
      "신뢰도는 업무트랙 추천 신뢰도이며, 승인 가능성이나 투자 적정성 신뢰도가 아닙니다.",
    ],
  };
}

export function CommandCenter() {
  const router = useRouter();
  const { logout, permissions, user } = useAuth();
  const canWriteProjects = hasPermission(permissions, PROJECT_WRITE_PERMISSION);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const nameInputId = useId();
  const [requestedName, setRequestedName] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [intakeStage, setIntakeStage] = useState<IntakeStage>("preparing");
  const [dragOver, setDragOver] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [triage, setTriage] = useState<TriageResult | null>(null);
  const [triageLoading, setTriageLoading] = useState(false);
  const [triageError, setTriageError] = useState<string | null>(null);
  const [selectedTrack, setSelectedTrack] = useState<WorkflowTrack | null>(null);
  const [finalizing, setFinalizing] = useState(false);
  const finalizingRef = useRef(false);
  const [finalizeError, setFinalizeError] = useState<string | null>(null);
  const [intakeWork, setIntakeWork] = useState<IntakeWorkStatus>({ busy: false, pending: true });
  const [continuationMatches, setContinuationMatches] = useState<ContinuationMatch[]>([]);
  const [switchingAccount, setSwitchingAccount] = useState(false);
  const [switchAccountError, setSwitchAccountError] = useState<string | null>(null);

  const switchAccount = useCallback(async () => {
    if (switchingAccount) return;
    setSwitchingAccount(true);
    setSwitchAccountError(null);
    try {
      // Account switching is an explicit current-device sign-out, not an
      // in-memory UI reset. Await cookie revocation before leaving this page.
      await logout();
      window.location.replace("/login?next=/start&switch=1");
    } catch {
      setSwitchAccountError(
        "현재 로그인 세션을 종료하지 못했습니다. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.",
      );
      setSwitchingAccount(false);
    }
  }, [logout, switchingAccount]);

  const start = useCallback(async (picked: File[], bypassContinuationCheck = false) => {
    if (!picked.length) return;
    if (!canWriteProjects) {
      setError(projectWriteRestrictionMessage(user?.role));
      setPhase("error");
      return;
    }
    const name = requestedName.trim() || deriveProjectName(picked);
    setProjectName(name);
    setPhase("creating");
    setIntakeStage("preparing");
    setError(null);
    setTriage(null);
    setTriageError(null);
    try {
      validateIntakeFiles(picked);
      // Browser File objects remain valid after the picker is reset. Reusing
      // them avoids reading every large PDF/XLSX twice before the first API
      // request, which previously made intake appear stalled.
      const buffered = picked;
      setFiles(buffered);
      if (!bypassContinuationCheck) {
        setIntakeStage("checking-history");
        const fingerprints = await Promise.all(buffered.map(async (file) => {
          const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
          const sha256 = Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
          return { name: file.name, size: file.size, sha256 };
        }));
        const continuationRes = await fetch(`${apiBase}/projects/intake/continuation-check`, {
          method: "POST",
          headers: { ...authHeaders("web"), "Content-Type": "application/json" },
          body: JSON.stringify({ files: fingerprints }),
          signal: AbortSignal.timeout(15_000),
        });
        if (continuationRes.ok) {
          const continuationBody = await continuationRes.json();
          const matches = Array.isArray(continuationBody.matches) ? continuationBody.matches as ContinuationMatch[] : [];
          if (matches.length) {
            setContinuationMatches(matches);
            setPhase("resume");
            return;
          }
        }
      }
      setContinuationMatches([]);
      setIntakeStage("creating-workspace");
      const res = await fetch(`${apiBase}/projects/`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ project: { name, status: "draft" } }),
        signal: AbortSignal.timeout(15_000),
      });
      if (!res.ok) {
        throw new Error(
          await backendErrorMessageForResponse(res, { action: "새 심사 공간 생성" }),
        );
      }
      const body = (await res.json()) as { id?: string; project?: { id?: string } };
      const id = body.id ?? body.project?.id;
      if (!id) throw new Error("생성된 심사 ID를 받지 못했습니다.");
      setProjectId(String(id));
      setPhase("ready");
    } catch (e) {
      const timedOut = e instanceof DOMException
        && (e.name === "TimeoutError" || e.name === "AbortError");
      setError(
        timedOut
          ? "서버 응답이 15초 안에 완료되지 않았습니다. 선택한 파일은 전송되지 않았으며 같은 파일로 다시 시도할 수 있습니다."
          : e instanceof Error
            ? e.message
            : "심사 공간 생성 중 오류가 발생했습니다.",
      );
      setPhase("error");
    }
  }, [canWriteProjects, user?.role, requestedName]);

  const runTriage = useCallback(
    async (classified: Classified[]) => {
      if (!projectId || !classified.length) return;
      setTriageLoading(true);
      setTriageError(null);
      try {
        const res = await fetch(`${apiBase}/projects/${projectId}/intake/triage`, {
          method: "POST",
          headers: { ...authHeaders("web"), "Content-Type": "application/json" },
          body: JSON.stringify({ files: classified }),
        });
        if (!res.ok) throw new Error(`트랙 추론 실패 (${res.status})`);
        const body = (await res.json()) as TriageResult;
        setTriage(body);
        setSelectedTrack(body.recommendedTrack);
      } catch (e) {
        const reason = e instanceof Error ? e.message : "트랙 추론 서버 호출 실패";
        const fallback = buildLocalTriage(classified, reason);
        setTriage(fallback);
        setSelectedTrack(fallback.recommendedTrack);
        setTriageError(`서버 트랙 추론이 아직 완료되지 않아 문서 신호 기반 임시 추천을 표시합니다. (${reason})`);
      } finally {
        setTriageLoading(false);
      }
    },
    [projectId],
  );

  const finalizeAndEnter = useCallback(async () => {
    if (!projectId || finalizingRef.current) return;
    finalizingRef.current = true;
    const track = (selectedTrack ?? triage?.recommendedTrack ?? "development") as WorkflowTrack;
    setStoredWorkflowTrack(track);
    setFinalizing(true);
    setFinalizeError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/intake/finalize`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ track, run: false }),
      });
      if (!res.ok) throw new Error(await backendErrorMessageForResponse(res, { action: "자료 연결 및 보완 계획 확인" }));
      const body: unknown = await res.json().catch(() => null);
      if (!body || typeof body !== "object" || Array.isArray(body)
        || (body as Record<string, unknown>).projectId !== projectId) {
        throw new Error("현재 프로젝트의 자료 연결 결과를 확인하지 못했습니다.");
      }
      const result = body as Record<string, unknown>;
      const failure = intakeProcessingFailure(result);
      if (failure) throw new Error(failure);
      const nextPage = result.nextStage === "input_review" ? "input-package" : "chief-orchestration";
      router.push(`/project/${encodeURIComponent(projectId)}/${nextPage}?track=${track}`);
    } catch (caught) {
      setFinalizeError(caught instanceof Error ? caught.message : "자료 연결 및 보완 계획을 확인하지 못했습니다.");
    } finally {
      finalizingRef.current = false;
      setFinalizing(false);
    }
  }, [projectId, router, selectedTrack, triage]);

  const enterWorkspace = useCallback(() => {
    if (!projectId || !selectedTrack || intakeWork.pending || finalizing || finalizeError) return;
    void finalizeAndEnter();
  }, [projectId, selectedTrack, intakeWork.pending, finalizing, finalizeError, finalizeAndEnter]);

  return (
    <main className="relative flex min-h-screen flex-col items-center overflow-hidden bg-slate-950 px-4 py-10 text-white sm:px-6 sm:py-12">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_50%_at_50%_0%,rgba(56,189,248,0.12),transparent),radial-gradient(50%_40%_at_80%_100%,rgba(99,102,241,0.12),transparent)]" />
      {phase === "idle" && projectId === null && files.length === 0 ? (
        <Link href="/dashboard" className="absolute left-4 top-4 z-20 inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/70 px-3 text-xs font-semibold text-slate-200 hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-300 sm:left-5 sm:top-5">
          <LayoutDashboard className="h-4 w-4" aria-hidden="true" />
          대시보드 보기
        </Link>
      ) : null}
      <button
        type="button"
        data-testid="switch-account-button"
        disabled={switchingAccount}
        aria-busy={switchingAccount}
        onClick={() => void switchAccount()}
        className="absolute right-5 top-5 z-20 inline-flex h-9 items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/80 px-3 text-xs font-bold text-slate-300 backdrop-blur hover:border-sky-500 hover:text-white disabled:cursor-wait disabled:opacity-60"
      >
        {switchingAccount ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <LogOut className="h-4 w-4" />
        )}
        {switchingAccount ? "현재 세션 종료 중" : "다른 계정으로 로그인"}
      </button>

      {switchAccountError ? (
        <div
          role="alert"
          data-testid="switch-account-error"
          className="absolute right-5 top-16 z-20 max-w-sm rounded-lg border border-rose-500/40 bg-rose-950/95 px-4 py-3 text-xs font-semibold leading-5 text-rose-100 shadow-xl"
        >
          {switchAccountError}
        </div>
      ) : null}

      <div className="relative z-10 w-full max-w-6xl">
        <div className="re-dashboard-intro mb-8 flex flex-col items-center text-center">
          <DashboardAtmosphere />
          <div className="inline-flex items-center gap-2 rounded-full border border-sky-400/30 bg-sky-500/10 px-3 py-1.5 text-xs font-bold tracking-[0.15em] text-sky-300">
            <span aria-hidden="true" className="h-2 w-2 rounded-full bg-sky-400" />
            REALYTICS OS
          </div>
          <h1 className="mt-5 text-3xl font-black tracking-tight sm:text-4xl">
            자료를 올리면 <span className="bg-gradient-to-r from-sky-400 to-indigo-400 bg-clip-text text-transparent">AI가 분석을 시작</span>합니다
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-slate-400">
            사업계획서, 토지조서, 사업수지, 임대내역 등 여러 자료를 한 번에 올리세요.
            AI가 문서를 자동 분류하고 진행할 업무 트랙을 추천합니다.
          </p>
        </div>

        {!canWriteProjects ? (
          <div className="mb-4">
            <ReviewOnlyNotice role={user?.role} />
          </div>
        ) : null}

        {phase === "resume" ? (
          <ResumePanel
            matches={continuationMatches}
            onContinue={(match) => router.push(`/project/${match.projectId}/chief-orchestration`)}
            onNew={() => void start(files, true)}
            canCreate={canWriteProjects}
          />
        ) : phase === "ready" && projectId ? (
          <div className="space-y-4">
            <div className="flex flex-col gap-2 rounded-2xl border border-sky-500/20 bg-slate-900/60 p-4 backdrop-blur sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="text-xs font-bold uppercase tracking-wide text-sky-300">심사 공간 생성됨</p>
                <p className="truncate text-sm font-black">{projectName}</p>
                <div className="mt-2"><ProjectNameEditor key={projectId} projectId={projectId} currentName={projectName} onSaved={setProjectName} /></div>
              </div>
              <div className="text-xs font-semibold text-slate-400">
                {triageLoading ? "AI가 업무 트랙을 추론 중입니다." : triage ? "진행 트랙을 선택하세요." : "자료 분류 후 트랙을 추천합니다."}
              </div>
            </div>
            <TrackTriagePanel
              triage={triage}
              loading={triageLoading}
              error={triageError}
              selectedTrack={selectedTrack}
              onSelect={setSelectedTrack}
              onEnter={enterWorkspace}
              entryDisabled={intakeWork.pending || finalizing || Boolean(finalizeError)}
            />
            {finalizing ? (
              <div className="flex items-center gap-3 rounded-2xl border border-sky-500/30 bg-sky-500/10 p-4 text-sm font-bold text-sky-200">
                <Loader2 className="h-5 w-5 animate-spin" />
                자료를 연결하고 주소·PNU 보강 가능성을 확인 중입니다. 다음 화면에서 부족자료와 보강 순서를 안내합니다.
              </div>
            ) : null}
            {finalizeError ? (
              <div role="alert" className="rounded-2xl border border-amber-400/40 bg-amber-500/10 p-4 text-sm text-amber-100">
                <p className="font-bold">자료 처리는 완료됐지만 연결·보완 계획 확인을 마치지 못했습니다.</p>
                <p className="mt-2 leading-6">{finalizeError}</p>
                <p className="mt-2 text-xs leading-5">현재 프로젝트와 파일별 처리 결과를 유지했습니다. 다시 시도해도 완료된 파일을 재전송하지 않습니다.</p>
                <button type="button" onClick={() => void finalizeAndEnter()} disabled={finalizing || intakeWork.pending}
                  className="mt-3 min-h-10 rounded-lg border border-amber-300 px-3 font-bold disabled:opacity-50">
                  자료 연결 다시 시도
                </button>
              </div>
            ) : null}
            <div className="rounded-2xl bg-white p-1 text-slate-900 dark:bg-slate-900 dark:text-white">
              <SmartIntake
                projectId={projectId}
                initialFiles={files}
                onClassified={(classified) => void runTriage(classified)}
                onProcessed={() => void finalizeAndEnter()}
                onStatusChange={setIntakeWork}
                disabled={finalizing}
              />
            </div>
          </div>
        ) : (
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.75fr)]">
            <RecentWorkPanel
              canCreateAnalysis={canWriteProjects}
              onNewAnalysis={() => inputRef.current?.click()}
            />
            <section aria-labelledby="new-analysis-title" className="rounded-2xl border border-slate-800 bg-slate-900/35 p-4 backdrop-blur sm:p-5">
              <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">New analysis</p>
              <h2 id="new-analysis-title" className="mt-1 text-lg font-black text-white">새 자료로 분석 시작</h2>
              <p className="mt-1 text-xs leading-5 text-slate-400">
                {canWriteProjects
                  ? "기존 작업과 별개의 새 심사 공간을 만들고 원본 자료를 연결합니다."
                  : "심사 전용 역할에서는 기존 작업을 열람할 수 있으며 새 심사 생성과 업로드는 비활성화됩니다."}
              </p>
              <div className="mt-4">
                <label htmlFor={nameInputId} className="block text-sm font-semibold text-slate-200">프로젝트 이름 (선택)</label>
                <input id={nameInputId} value={requestedName} onChange={(event) => setRequestedName(event.target.value)}
                  maxLength={PROJECT_NAME_MAX_LENGTH} disabled={!canWriteProjects || phase === "creating"}
                  placeholder="비워 두면 첫 번째 파일 이름을 사용합니다"
                  className="mb-1 mt-2 min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-400 disabled:opacity-60" />
                <p className="mb-4 text-xs text-slate-400">생성 후에도 이름을 바꿀 수 있습니다.</p>
                <DropZone
                  phase={phase}
                  stage={intakeStage}
                  dragOver={dragOver}
                  inputRef={inputRef}
                  onDragOver={setDragOver}
                  onStart={start}
                  disabled={!canWriteProjects}
                  compact
                />
              </div>
            </section>
          </div>
        )}

        {error ? <p className="mt-4 text-center text-sm font-bold text-rose-400">{error}</p> : null}

        <div className="mt-8 flex items-center justify-center gap-2 text-xs text-slate-500">
          <ShieldCheck className="h-4 w-4" />
          업로드 자료는 세션 기준으로 보관되며, AI는 근거 있는 추출만 수행합니다.
          <Sparkles className="h-4 w-4 text-sky-500/70" />
        </div>
      </div>
    </main>
  );
}

function ResumePanel({
  matches,
  onContinue,
  onNew,
  canCreate,
}: {
  matches: ContinuationMatch[];
  onContinue: (match: ContinuationMatch) => void;
  onNew: () => void;
  canCreate: boolean;
}) {
  return (
    <section className="rounded-2xl border border-amber-400/40 bg-amber-500/10 p-5 text-white">
      <p className="text-xs font-bold uppercase tracking-[0.16em] text-amber-300">기존 자료 확인</p>
      <h2 className="mt-1 text-xl font-black">같은 자료로 진행한 심사가 있습니다.</h2>
      <p className="mt-2 text-sm leading-6 text-amber-100">기존 프로젝트의 계산 결과, 증빙, 미해결 리스크를 이어서 볼지 새 프로젝트로 다시 분석할지 선택하세요.</p>
      <div className="mt-4 space-y-2">
        {matches.map((match) => <button key={match.projectId} type="button" onClick={() => onContinue(match)} className="flex w-full items-center justify-between rounded-xl border border-amber-300/30 bg-slate-950/50 p-3 text-left hover:border-amber-300"><span><span className="block text-sm font-black">{match.projectName}</span><span className="text-xs text-amber-100/70">{match.matchedFiles.map((file) => file.fileName).filter(Boolean).join(", ")}</span></span><ArrowRight className="h-4 w-4 text-amber-300" /></button>)}
      </div>
      <button
        type="button"
        onClick={onNew}
        disabled={!canCreate}
        title={!canCreate ? `필요 권한: ${PROJECT_WRITE_PERMISSION}` : undefined}
        className="mt-4 inline-flex h-10 items-center gap-2 rounded-lg border border-slate-600 bg-slate-950 px-4 text-sm font-black text-white hover:border-sky-400 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500"
      >
        {canCreate ? "새 프로젝트로 분석 시작" : "심사 전용 · 새 프로젝트 생성 불가"}
      </button>
    </section>
  );
}

function DropZone({
  phase,
  stage,
  dragOver,
  inputRef,
  onDragOver,
  onStart,
  disabled = false,
  compact = false,
}: {
  phase: Phase;
  stage: IntakeStage;
  dragOver: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  onDragOver: (value: boolean) => void;
  onStart: (files: File[]) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const stageCopy = INTAKE_STAGE_COPY[stage];
  const interactionDisabled = disabled || phase === "creating";
  return (
    <div
      role="button"
      tabIndex={interactionDisabled ? -1 : 0}
      aria-disabled={interactionDisabled}
      data-testid="new-analysis-dropzone"
      onClick={() => !interactionDisabled && inputRef.current?.click()}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !interactionDisabled) inputRef.current?.click();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (!interactionDisabled) onDragOver(true);
      }}
      onDragLeave={() => onDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        onDragOver(false);
        if (!interactionDisabled && e.dataTransfer.files?.length) void onStart(Array.from(e.dataTransfer.files));
      }}
      className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-5 text-center transition-colors ${compact ? "min-h-[270px] py-10" : "py-16"} ${
        disabled
          ? "cursor-not-allowed border-slate-800 bg-slate-950/40 text-slate-500"
          : phase === "creating"
          ? "cursor-wait border-sky-500/40 bg-sky-500/5"
          : dragOver
            ? "border-sky-400 bg-sky-500/10"
            : "border-slate-700 hover:border-sky-500/50 hover:bg-white/5"
      }`}
    >
      {disabled ? (
        <>
          <ShieldCheck className="h-10 w-10 text-amber-300/70" />
          <p className="text-base font-bold text-slate-300">심사 전용 역할에서는 업로드할 수 없습니다.</p>
          <p className="text-xs text-slate-500">필요 권한 · {PROJECT_WRITE_PERMISSION}</p>
        </>
      ) : phase === "creating" ? (
        <>
          <Loader2 className="h-10 w-10 animate-spin text-sky-400" />
          <p className="text-base font-bold" aria-live="polite">{stageCopy.title}</p>
          <p className="text-xs text-slate-400">{stageCopy.detail}</p>
        </>
      ) : (
        <>
          <UploadCloud className="h-10 w-10 text-slate-500" />
          <p className="text-base font-bold">파일을 끌어다 놓거나 클릭해서 선택하세요. 여러 개도 가능합니다.</p>
          <p className="text-xs text-slate-400">PDF · XLS/XLSX · DOCX · JSON · CSV · 파일당 최대 64MB</p>
        </>
      )}
      <input
        ref={inputRef}
        type="file"
        multiple
        disabled={interactionDisabled}
        className="hidden"
        onChange={(e) => {
          if (!e.target.files?.length) return;
          const selected = Array.from(e.target.files);
          // Allow retrying the exact same file after a recoverable error.
          e.target.value = "";
          void onStart(selected);
        }}
      />
    </div>
  );
}

function TrackTriagePanel({
  triage,
  loading,
  error,
  selectedTrack,
  onSelect,
  onEnter,
  entryDisabled,
}: {
  triage: TriageResult | null;
  loading: boolean;
  error: string | null;
  selectedTrack: WorkflowTrack | null;
  onSelect: (track: WorkflowTrack) => void;
  onEnter: () => void;
  entryDisabled: boolean;
}) {
  const tracks = triage?.tracks ?? [];
  return (
    <section className="rounded-2xl border border-slate-700/70 bg-slate-900/70 p-5 backdrop-blur">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.16em] text-sky-300">AI Track Triage</p>
          <h2 className="mt-1 text-xl font-black">이 자료로 무엇을 진행하시겠습니까?</h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            AI가 문서분류 결과를 근거로 부동산 개발, PF, 부동산 금융, 실물금융, 리츠, 펀드 중 적합한 트랙을 추천합니다.
          </p>
        </div>
        {triage ? (
          <div className="space-y-2 rounded-lg border border-slate-700 px-3 py-2 text-xs font-bold text-slate-300">
            <p>추천 방식: {triage.aiAssisted ? "LLM 보조" : triage.recommendationSource === "local_fallback" ? "로컬 임시 규칙" : "서버 결정 규칙"}</p>
            <p className="font-semibold leading-5 text-slate-400">
              신뢰도 근거: {triage.confidenceBasis ?? "문서유형별 업무트랙 가중치, 자동분류 신뢰도, 파일명 신호 기준입니다. 승인 가능성은 별도 Gate에서 판단합니다."}
            </p>
            {triage.persisted === false ? <p className="font-semibold text-amber-300">DB 저장 전 임시 추천입니다.</p> : null}
          </div>
        ) : null}
      </div>

      {loading ? (
        <div className="mt-5 flex items-center gap-3 rounded-xl border border-slate-700 bg-slate-950/60 p-4 text-sm font-bold text-slate-300">
          <Loader2 className="h-5 w-5 animate-spin text-sky-400" />
          AI가 문서 신호를 비교하고 있습니다.
        </div>
      ) : null}

      {error ? <p className="mt-4 rounded-lg bg-amber-500/10 px-3 py-2 text-sm font-bold text-amber-200">{error}</p> : null}

      {tracks.length ? (
        <>
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            {tracks.map((track) => {
              const Icon = TRACK_ICONS[track.track];
              const selected = selectedTrack === track.track;
              const workflow = getTrackWorkflowDefinition(track.track);
              return (
                <button
                  key={track.track}
                  type="button"
                  onClick={() => onSelect(track.track)}
                  className={`min-h-[152px] rounded-xl border p-4 text-left transition ${
                    selected
                      ? "border-sky-400 bg-sky-500/10 ring-1 ring-sky-400"
                      : "border-slate-700 bg-slate-950/40 hover:border-slate-500"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-white/10 text-sky-300">
                        <Icon className="h-5 w-5" />
                      </div>
                      <div>
                        <p className="text-base font-black">{TRACK_LABELS[track.track]}</p>
                        <p className="text-xs font-semibold text-slate-400">신뢰도 {Math.round(track.confidence * 100)}%</p>
                      </div>
                    </div>
                    {track.recommended ? (
                      <span className="rounded-full bg-sky-400 px-2 py-1 text-[11px] font-black text-slate-950">AI 추천</span>
                    ) : selected ? (
                      <CheckCircle2 className="h-5 w-5 text-sky-300" />
                    ) : null}
                  </div>
                  <p className="mt-3 text-sm leading-6 text-slate-300">{track.description}</p>
                  <div className="mt-3 rounded-lg border border-slate-700/70 bg-slate-950/50 p-3">
                    <p className="text-[11px] font-black uppercase tracking-wide text-sky-300">선택 시 오케스트레이션</p>
                    <p className="mt-1 text-xs leading-5 text-slate-300">{workflow.decisionQuestion}</p>
                    <ol className="mt-2 space-y-1 text-xs leading-5 text-slate-500">
                      {workflow.steps.slice(0, 3).map((step, index) => (
                        <li key={step.key}>
                          {index + 1}. {step.title}
                        </li>
                      ))}
                    </ol>
                  </div>
                  <p className="mt-3 text-xs leading-5 text-slate-500">{track.rationale}</p>
                  {track.reasons.length ? (
                    <ul className="mt-3 space-y-1 text-xs leading-5 text-slate-500">
                      {track.reasons.slice(0, 3).map((reason) => (
                        <li key={reason}>- {reason}</li>
                      ))}
                    </ul>
                  ) : null}
                </button>
              );
            })}
          </div>
          <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs leading-5 text-slate-500">
              {triage?.limitations?.[0] ?? "추천은 업로드 문서 신호 기준이며 최종 판단은 각 에이전트 검토 후 확정됩니다."}
            </p>
            <button
              type="button"
              onClick={onEnter}
              disabled={!selectedTrack || entryDisabled}
              className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-white px-4 text-sm font-black text-slate-950 hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              선택 트랙 워크스페이스로 이동 <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </>
      ) : !loading ? (
        <div className="mt-5 rounded-xl border border-slate-700 bg-slate-950/50 p-4 text-sm text-slate-400">
          문서 자동분류가 완료되면 이곳에 트랙 카드가 표시됩니다.
        </div>
      ) : null}
    </section>
  );
}
