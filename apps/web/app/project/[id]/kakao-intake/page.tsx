"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  FileSpreadsheet,
  FileText,
  GitBranch,
  Loader2,
  MessageCircle,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";

type BusyAction = "business" | "land" | "workbook" | "run" | null;

interface SourceDocument {
  documentType?: string;
  originalFileName?: string;
}

interface RunResponse {
  status?: string;
  run?: {
    run_id?: string;
  };
  run_id?: string;
}

const intakeSteps = [
  "카카오톡에는 파일 원문과 민감정보를 저장하지 않습니다.",
  "파일은 REALYTICS 서버에 보관되고 parser/agent가 분석합니다.",
  "카카오톡 답변은 요약과 링크만 제공합니다.",
];

export default function KakaoIntakePage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const projectId = String(params.id);
  const eventType = searchParams.get("event") || "document_upload_requested";

  const [businessPlan, setBusinessPlan] = useState<File | null>(null);
  const [landLedger, setLandLedger] = useState<File | null>(null);
  const [workbook, setWorkbook] = useState<File | null>(null);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [latestRunId, setLatestRunId] = useState<string | null>(null);

  const refreshDocuments = useCallback(async () => {
    const res = await fetch(`${apiBase}/projects/${projectId}/source-documents`, {
      cache: "no-store",
      headers: authHeaders("kakao-intake"),
    });
    if (res.ok) setDocuments((await res.json()) as SourceDocument[]);
  }, [projectId]);

  useEffect(() => {
    void refreshDocuments();
  }, [refreshDocuments]);

  const counts = useMemo(() => {
    return documents.reduce<Record<string, number>>((acc, doc) => {
      const type = doc.documentType || "source_document";
      acc[type] = (acc[type] ?? 0) + 1;
      return acc;
    }, {});
  }, [documents]);

  const readyCount = [
    counts.business_plan,
    counts.land_ledger,
    counts.feasibility_workbook,
  ].filter(Boolean).length;

  async function runAction(action: BusyAction, work: () => Promise<string>) {
    setBusyAction(action);
    setError(null);
    setMessage(null);
    try {
      const result = await work();
      setMessage(result);
      await refreshDocuments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "요청 처리에 실패했습니다.");
    } finally {
      setBusyAction(null);
    }
  }

  async function postFile(endpoint: string, file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${apiBase}/projects/${projectId}/${endpoint}`, {
      method: "POST",
      headers: authHeaders("kakao-intake"),
      body: formData,
    });
    if (!res.ok) throw new Error(await responseError(res));
    return res.json() as Promise<Record<string, unknown>>;
  }

  async function postJson(endpoint: string, payload: unknown) {
    const res = await fetch(`${apiBase}/projects/${projectId}/${endpoint}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders("kakao-intake"),
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await responseError(res));
    return res.json() as Promise<Record<string, unknown>>;
  }

  const uploadBusinessPlan = () =>
    runAction("business", async () => {
      if (!businessPlan) return "사업계획서 파일을 먼저 선택하세요.";
      const body = await postFile("upload", businessPlan);
      return body.status === "host_llm_required"
        ? "사업계획서가 보관되었습니다. 추가 AI 추출 단계가 필요합니다."
        : "사업계획서가 업로드되고 parser 분석이 완료되었습니다.";
    });

  const uploadLandLedger = () =>
    runAction("land", async () => {
      if (!landLedger) return "토지조서 Excel을 먼저 선택하세요.";
      const analyzed = await postFile("land-ledger/analyze", landLedger);
      await postJson("land-ledger/enrich", {
        landLedger: analyzed.landLedger,
        persist: true,
        persistEvidence: true,
      });
      return "토지조서가 분석되고 토지 검토 데이터로 저장되었습니다.";
    });

  const uploadWorkbook = () =>
    runAction("workbook", async () => {
      if (!workbook) return "사업수지 Excel을 먼저 선택하세요.";
      const body = await postFile("feasibility-workbook/validate", workbook);
      const validation = body.validation as { status?: string } | undefined;
      return `사업수지 Excel이 보관되었습니다. 검증 상태: ${validation?.status ?? "확인 필요"}`;
    });

  const runAnalysis = () =>
    runAction("run", async () => {
      const body = await postJson("orchestrations/pf-review", {
        requestedBy: "kakao-intake",
        source: "kakao-mobile-intake",
        eventType,
      }) as RunResponse;
      const runId = body.run?.run_id ?? body.run_id ?? null;
      setLatestRunId(runId);
      return runId
        ? `AI 분석이 완료되었습니다. 실행 ID: ${runId}`
        : "AI 분석이 완료되었습니다.";
    });

  return (
    <main className="mx-auto max-w-2xl space-y-4 pb-20">
      <section className="rounded-b-3xl bg-slate-950 px-5 pb-6 pt-5 text-white shadow-sm dark:bg-slate-900">
        <div className="flex items-center gap-2">
          <MessageCircle className="h-5 w-5 text-amber-300" />
          <p className="text-xs font-bold text-slate-300">카카오 접수</p>
        </div>
        <h1 className="mt-3 text-2xl font-black leading-tight">
          사업자료 업로드 후 AI 분석
        </h1>
        <p className="mt-2 text-sm leading-6 text-slate-300">
          카카오톡에서 받은 링크로 사업계획서와 Excel을 안전하게 접수하고, REALYTICS Agent가 분석한 뒤 요약 결과를 확인합니다.
        </p>
        <div className="mt-4 grid grid-cols-3 gap-2">
          <Mini label="사업계획서" ready={Boolean(counts.business_plan)} />
          <Mini label="토지조서" ready={Boolean(counts.land_ledger)} />
          <Mini label="사업수지" ready={Boolean(counts.feasibility_workbook)} />
        </div>
      </section>

      <section className="space-y-2 px-4">
        {intakeSteps.map((step, index) => (
          <div key={step} className="flex items-start gap-2 rounded-2xl bg-slate-100 p-3 text-sm font-semibold text-slate-700 dark:bg-slate-900 dark:text-slate-200">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-white text-xs font-black text-slate-950 dark:bg-slate-800 dark:text-white">
              {index + 1}
            </span>
            {step}
          </div>
        ))}
      </section>

      {message ? <Notice tone="good" message={message} /> : null}
      {error ? <Notice tone="bad" message={error} /> : null}

      <section className="space-y-3 px-4">
        <UploadPanel
          title="1. 사업계획서 / 건축개요 PDF"
          desc="사업명, 위치, 상품구성, PF 조건, 보고서 문단 후보를 추출합니다."
          icon={<FileText className="h-5 w-5" />}
          file={businessPlan}
          accept=".pdf"
          onFile={setBusinessPlan}
          buttonLabel="사업계획서 업로드"
          busy={busyAction === "business"}
          disabled={!businessPlan || busyAction !== null}
          onClick={() => void uploadBusinessPlan()}
        />
        <UploadPanel
          title="2. 토지조서 Excel"
          desc="필지, 소유자, 면적, 매입금액, 지급일정, PNU 보완 필요 여부를 분석합니다."
          icon={<FileSpreadsheet className="h-5 w-5" />}
          file={landLedger}
          accept=".xls,.xlsx"
          onFile={setLandLedger}
          buttonLabel="토지조서 분석"
          busy={busyAction === "land"}
          disabled={!landLedger || busyAction !== null}
          onClick={() => void uploadLandLedger()}
        />
        <UploadPanel
          title="3. 사업수지 Excel"
          desc="수식 보존, named range, 수입·비용 합계, Cash Flow 변환 가능성을 검증합니다."
          icon={<FileSpreadsheet className="h-5 w-5" />}
          file={workbook}
          accept=".xls,.xlsx,.xlsm"
          onFile={setWorkbook}
          buttonLabel="사업수지 검증"
          busy={busyAction === "workbook"}
          disabled={!workbook || busyAction !== null}
          onClick={() => void uploadWorkbook()}
        />
      </section>

      <section className="sticky bottom-0 border-t border-slate-200 bg-white/95 px-4 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-950/95">
        <button
          type="button"
          onClick={() => void runAnalysis()}
          disabled={busyAction !== null || readyCount === 0}
          className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 text-sm font-black text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-slate-950"
        >
          {busyAction === "run" ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}
          AI 통합심사 실행
        </button>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <Link
            href={`/project/${projectId}/mobile?source=kakao&event=document_analysis_completed`}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-slate-200 text-xs font-bold text-slate-700 dark:border-slate-700 dark:text-slate-200"
          >
            <ShieldCheck className="h-4 w-4" />
            결과 요약
          </Link>
          <Link
            href={latestRunId ? `/project/${projectId}/orchestration` : `/project/${projectId}/input-package`}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-slate-200 text-xs font-bold text-slate-700 dark:border-slate-700 dark:text-slate-200"
          >
            상세 검토
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </section>
    </main>
  );
}

function Mini({ label, ready }: { label: string; ready: boolean }) {
  return (
    <div className="rounded-2xl bg-white/10 p-3">
      <p className="text-[11px] font-bold text-slate-300">{label}</p>
      <div className="mt-2 flex items-center gap-1 text-sm font-black">
        {ready ? <CheckCircle2 className="h-4 w-4 text-emerald-300" /> : <AlertCircle className="h-4 w-4 text-amber-300" />}
        {ready ? "확보" : "대기"}
      </div>
    </div>
  );
}

function UploadPanel({
  title,
  desc,
  icon,
  file,
  accept,
  onFile,
  buttonLabel,
  busy,
  disabled,
  onClick,
}: {
  title: string;
  desc: string;
  icon: ReactNode;
  file: File | null;
  accept: string;
  onFile: (file: File | null) => void;
  buttonLabel: string;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-black text-slate-950 dark:text-white">{title}</p>
          <p className="mt-1 text-sm font-semibold leading-6 text-slate-500">{desc}</p>
        </div>
      </div>
      <label className="mt-4 block rounded-2xl border border-dashed border-slate-300 p-4 text-center dark:border-slate-700">
        <input
          type="file"
          accept={accept}
          className="sr-only"
          onChange={(event) => onFile(event.target.files?.[0] ?? null)}
        />
        <Upload className="mx-auto h-5 w-5 text-slate-400" />
        <p className="mt-2 truncate text-sm font-bold text-slate-700 dark:text-slate-200">
          {file?.name ?? "파일 선택"}
        </p>
      </label>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className="mt-3 inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 text-sm font-black text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-slate-950"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
        {buttonLabel}
      </button>
    </section>
  );
}

function Notice({ tone, message }: { tone: "good" | "bad"; message: string }) {
  const cls = tone === "good"
    ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-200"
    : "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-200";
  return (
    <div className={`mx-4 rounded-2xl border p-4 text-sm font-bold leading-6 ${cls}`}>
      {message}
    </div>
  );
}

async function responseError(res: Response) {
  const body = await res.json().catch(() => ({})) as { detail?: unknown };
  if (typeof body.detail === "string") return body.detail;
  return `Request failed with ${res.status}`;
}
