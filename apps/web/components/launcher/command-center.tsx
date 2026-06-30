"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  BarChart3,
  Building2,
  CheckCircle2,
  Landmark,
  Layers3,
  Loader2,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  Wallet,
} from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { setStoredWorkflowTrack, type WorkflowTrack } from "../../lib/workflow-track";
import { SmartIntake } from "../intake/smart-intake";

type Phase = "idle" | "creating" | "ready" | "error";

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

const TRACK_META: Record<WorkflowTrack, Pick<TriageTrack, "label" | "title" | "description" | "entryHref">> = {
  development: {
    label: "부동산개발",
    title: "부동산개발",
    description: "토지, 인허가, 사업수지, 분양수입, 개발가능성을 먼저 검토합니다.",
    entryHref: "input-package",
  },
  pf: {
    label: "부동산 PF",
    title: "부동산 PF",
    description: "PF 구조, Tranche, 상환재원, Exit 분양률, 심사보고서를 검토합니다.",
    entryHref: "orchestration",
  },
  finance: {
    label: "부동산금융",
    title: "부동산금융",
    description: "담보대출, 리파이낸싱, 대출구조, 회수 가능성을 검토합니다.",
    entryHref: "input-package",
  },
  real_asset_finance: {
    label: "부동산 실물금융",
    title: "부동산 실물금융",
    description: "임대료, NOI, Cap Rate, 실물자산 인수와 담보가치를 검토합니다.",
    entryHref: "input-package",
  },
  reit: {
    label: "리츠",
    title: "리츠",
    description: "편입자산, 배당재원, 감정가, 운용구조와 공시 리스크를 검토합니다.",
    entryHref: "input-package",
  },
  fund: {
    label: "부동산 펀드",
    title: "부동산 펀드",
    description: "펀드 설정, 투자대상, 대출약정, 운용과 회수 리스크를 검토합니다.",
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
      reasons[track].push(`${item.label || item.documentType}: 문서유형 가중치 ${weight} x 분류 신뢰도 ${Math.round(confidence * 100)}%`);
    }

    const name = item.filename.toLowerCase();
    if (name.includes("pf")) {
      scores.pf += 2;
      reasons.pf.push("파일명에 PF 포함");
    }
    if (name.includes("reit") || name.includes("리츠")) {
      scores.reit += 3;
      reasons.reit.push("파일명에 리츠/REIT 포함");
    }
    if (name.includes("fund") || name.includes("펀드")) {
      scores.fund += 3;
      reasons.fund.push("파일명에 펀드/Fund 포함");
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
            ? "업로드된 문서유형과 파일명 신호 기준으로 가장 관련성이 높습니다."
            : "일부 관련 신호가 있으나 추천 트랙보다 점수가 낮습니다.",
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
    confidenceBasis: "문서유형별 업무트랙 가중치 x 문서분류 신뢰도 + 파일명 키워드 신호",
    tracks,
    limitations: [
      "서버 트랙 저장이 실패해 브라우저에서 임시 추천을 계산했습니다. 워크스페이스 진입 후 다시 실행하면 DB에 저장됩니다.",
      "신뢰도는 라우팅 신뢰도이며 사업성 또는 승인 가능성 신뢰도가 아닙니다.",
    ],
  };
}

export function CommandCenter() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
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

  const start = useCallback(async (picked: File[]) => {
    if (!picked.length) return;
    const name = deriveProjectName(picked);
    setProjectName(name);
    setPhase("creating");
    setError(null);
    setTriage(null);
    setTriageError(null);
    try {
      const buffered = await Promise.all(picked.map(async (f) => new File([await f.arrayBuffer()], f.name, { type: f.type })));
      setFiles(buffered);
      const res = await fetch(`${apiBase}/projects/`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ project: { name, status: "draft" } }),
      });
      if (!res.ok) throw new Error(`심사 공간 생성 실패 (${res.status})`);
      const body = (await res.json()) as { id?: string; project?: { id?: string } };
      const id = body.id ?? body.project?.id;
      if (!id) throw new Error("생성된 심사 ID를 받지 못했습니다.");
      setProjectId(String(id));
      setPhase("ready");
    } catch (e) {
      setError(e instanceof Error ? e.message : "심사 공간 생성 중 오류가 발생했습니다.");
      setPhase("error");
    }
  }, []);

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
        setTriageError(`트랙 저장은 아직 완료되지 않았습니다. 현재 화면은 업로드 문서 신호를 기준으로 만든 임시 추천입니다. (${reason})`);
      } finally {
        setTriageLoading(false);
      }
    },
    [projectId],
  );

  const enterWorkspace = useCallback(() => {
    if (!projectId || !selectedTrack) return;
    const track = triage?.tracks.find((item) => item.track === selectedTrack);
    const entry = track?.entryHref || "input-package";
    setStoredWorkflowTrack(selectedTrack);
    router.push(`/project/${projectId}/${entry}?track=${selectedTrack}`);
  }, [projectId, router, selectedTrack, triage]);

  // "분류대로 처리" completed -> chain the rest automatically: resolve address->PNU,
  // and (for development/PF) run the PF review, then land on the result.
  const finalizeAndEnter = useCallback(async () => {
    if (!projectId) return;
    const track = (selectedTrack ?? triage?.recommendedTrack ?? "development") as WorkflowTrack;
    const entry = triage?.tracks.find((item) => item.track === track)?.entryHref || "input-package";
    setStoredWorkflowTrack(track);
    setFinalizing(true);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/intake/finalize`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ track, run: true }),
      });
      const body = (await res.json().catch(() => ({}))) as { analysisRun?: { run_id?: string } };
      if (body?.analysisRun?.run_id) {
        router.push(`/project/${projectId}/orchestration?track=${track}`);
        return;
      }
      router.push(`/project/${projectId}/${entry}?track=${track}`);
    } catch {
      router.push(`/project/${projectId}/${entry}?track=${track}`);
    } finally {
      setFinalizing(false);
    }
  }, [projectId, router, selectedTrack, triage]);

  return (
    <main className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-slate-950 px-5 py-12 text-white">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_50%_at_50%_0%,rgba(56,189,248,0.12),transparent),radial-gradient(50%_40%_at_80%_100%,rgba(99,102,241,0.12),transparent)]" />

      <div className="relative z-10 w-full max-w-3xl">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="inline-flex items-center gap-2 rounded-full border border-sky-400/30 bg-sky-500/10 px-3 py-1.5 text-xs font-bold tracking-[0.15em] text-sky-300">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-500" />
            </span>
            REALYTICS OS
          </div>
          <h1 className="mt-5 text-3xl font-black tracking-tight sm:text-4xl">
            자료를 올리면 <span className="bg-gradient-to-r from-sky-400 to-indigo-400 bg-clip-text text-transparent">AI가 분석을 시작</span>합니다
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-slate-400">
            사업계획서, 토지조서, 사업수지, 임대내역 등 여러 자료를 한 번에 올리세요. AI가 문서를 자동 분류하고 진행할 업무를 추천합니다.
          </p>
        </div>

        {phase === "ready" && projectId ? (
          <div className="space-y-4">
            <div className="flex flex-col gap-2 rounded-2xl border border-sky-500/20 bg-slate-900/60 p-4 backdrop-blur sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="text-xs font-bold uppercase tracking-wide text-sky-300">심사 공간 생성됨</p>
                <p className="truncate text-sm font-black">{projectName}</p>
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
            />
            {finalizing ? (
              <div className="flex items-center gap-3 rounded-2xl border border-sky-500/30 bg-sky-500/10 p-4 text-sm font-bold text-sky-200">
                <Loader2 className="h-5 w-5 animate-spin" />
                자료를 연결하고 (주소→PNU·공시지가) 선택한 트랙의 분석을 실행 중입니다. 잠시 후 결과 화면으로 이동합니다.
              </div>
            ) : null}
            <div className="rounded-2xl bg-white p-1 text-slate-900 dark:bg-slate-900 dark:text-white">
              <SmartIntake
                projectId={projectId}
                initialFiles={files}
                onClassified={(classified) => void runTriage(classified)}
                onProcessed={() => void finalizeAndEnter()}
              />
            </div>
          </div>
        ) : (
          <DropZone
            phase={phase}
            dragOver={dragOver}
            inputRef={inputRef}
            onDragOver={setDragOver}
            onStart={start}
          />
        )}

        {error ? <p className="mt-4 text-center text-sm font-bold text-rose-400">{error}</p> : null}

        <div className="mt-8 flex items-center justify-center gap-2 text-xs text-slate-500">
          <ShieldCheck className="h-4 w-4" />
          업로드 자료는 세션 기준으로 안전하게 보관되며, AI는 근거 있는 추출만 수행합니다.
          <Sparkles className="h-4 w-4 text-sky-500/70" />
        </div>
      </div>
    </main>
  );
}

function DropZone({
  phase,
  dragOver,
  inputRef,
  onDragOver,
  onStart,
}: {
  phase: Phase;
  dragOver: boolean;
  inputRef: React.RefObject<HTMLInputElement | null>;
  onDragOver: (value: boolean) => void;
  onStart: (files: File[]) => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-disabled={phase === "creating"}
      onClick={() => phase !== "creating" && inputRef.current?.click()}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && phase !== "creating") inputRef.current?.click();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (phase !== "creating") onDragOver(true);
      }}
      onDragLeave={() => onDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        onDragOver(false);
        if (phase !== "creating" && e.dataTransfer.files?.length) void onStart(Array.from(e.dataTransfer.files));
      }}
      className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-3xl border-2 border-dashed px-6 py-16 text-center transition-colors ${
        phase === "creating"
          ? "cursor-wait border-sky-500/40 bg-sky-500/5"
          : dragOver
            ? "border-sky-400 bg-sky-500/10"
            : "border-slate-700 hover:border-sky-500/50 hover:bg-white/5"
      }`}
    >
      {phase === "creating" ? (
        <>
          <Loader2 className="h-10 w-10 animate-spin text-sky-400" />
          <p className="text-base font-bold">심사 공간을 준비하고 있습니다</p>
          <p className="text-xs text-slate-400">생성되면 올린 자료를 AI가 바로 분류합니다.</p>
        </>
      ) : (
        <>
          <UploadCloud className="h-10 w-10 text-slate-500" />
          <p className="text-base font-bold">파일을 끌어다 놓거나 클릭해서 선택하세요. 여러 개도 가능합니다.</p>
          <p className="text-xs text-slate-500">PDF · XLS/XLSX · DOCX · JSON · CSV</p>
        </>
      )}
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) void onStart(Array.from(e.target.files));
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
}: {
  triage: TriageResult | null;
  loading: boolean;
  error: string | null;
  selectedTrack: WorkflowTrack | null;
  onSelect: (track: WorkflowTrack) => void;
  onEnter: () => void;
}) {
  const tracks = triage?.tracks ?? [];
  return (
    <section className="rounded-2xl border border-slate-700/70 bg-slate-900/70 p-5 backdrop-blur">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.16em] text-sky-300">AI Track Triage</p>
          <h2 className="mt-1 text-xl font-black">이 자료로 무엇을 진행하시겠습니까?</h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            AI가 문서분류 결과를 근거로 부동산개발, PF, 부동산금융, 실물금융, 리츠, 펀드 중 적합한 트랙을 추천합니다.
          </p>
        </div>
        {triage ? (
          <div className="space-y-2 rounded-lg border border-slate-700 px-3 py-2 text-xs font-bold text-slate-300">
            <p>추천 방식: {triage.aiAssisted ? "LLM 보조" : triage.recommendationSource === "local_fallback" ? "로컬 임시 규칙" : "서버 결정론 규칙"}</p>
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
                        <p className="text-base font-black">{track.title}</p>
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
              disabled={!selectedTrack}
              className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-white px-4 text-sm font-black text-slate-950 hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              선택 트랙으로 입장 <ArrowRight className="h-4 w-4" />
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
