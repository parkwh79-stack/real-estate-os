"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Database,
  FileArchive,
  FileJson,
  FileSpreadsheet,
  FileText,
  GitBranch,
  Loader2,
  PackageCheck,
  RefreshCw,
  Upload,
} from "lucide-react";
import { authHeaders } from "../../../../lib/auth-client";
import { SmartIntake } from "../../../../components/intake/smart-intake";

const apiBase = "http://127.0.0.1:8000";

type BusyAction =
  | "business-upload"
  | "business-run"
  | "land-ledger"
  | "workbook"
  | "land-evidence"
  | "override"
  | "pf-review"
  | null;

interface SourceDocument {
  originalFileName?: string;
  documentType?: string;
  contentType?: string;
  fileSizeBytes?: number;
  uploadedAt?: string;
  validationStatus?: string;
  archiveIntegrityStatus?: string;
  downloadUrl?: string;
}

interface LandEvidenceList {
  records?: Array<Record<string, unknown>>;
}

interface AssumptionOverrideList {
  records?: Array<Record<string, unknown>>;
}

interface UploadRunResponse {
  status?: string;
  run?: {
    run_id?: string;
  };
}

const defaultLandEvidenceJson = `{
  "records": [
    {
      "pnu": "2729011700101230004",
      "evidenceType": "official_land_price",
      "baseYear": 2024,
      "baseDate": "2024-01-01",
      "pricePerSqm": 0,
      "sourceName": "manual official land price evidence",
      "sourceType": "manual_upload",
      "sourceUrlOrEvidenceId": "replace-with-evidence-id",
      "confidence": 0.9
    }
  ]
}`;

const defaultOverrideJson = `{
  "checkKey": "sales_revenue_consistency",
  "canonicalField": "feasibility_input.total_revenue",
  "approvedValue": 0,
  "unit": "KRW",
  "reason": "Reviewer selected the signed workbook as the authoritative value.",
  "sourceLocators": [
    {
      "type": "excel_cell",
      "sheet": "Summary",
      "cell": "D8"
    }
  ]
}`;

export default function InputPackagePage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = String(params.id);

  const [businessPlan, setBusinessPlan] = useState<File | null>(null);
  const [landLedger, setLandLedger] = useState<File | null>(null);
  const [workbook, setWorkbook] = useState<File | null>(null);
  const [landEvidenceJson, setLandEvidenceJson] = useState(defaultLandEvidenceJson);
  const [overrideJson, setOverrideJson] = useState(defaultOverrideJson);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [landEvidenceCount, setLandEvidenceCount] = useState(0);
  const [overrideCount, setOverrideCount] = useState(0);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshPackageState = useCallback(async () => {
    const [docsRes, evidenceRes, overrideRes] = await Promise.all([
      fetch(`${apiBase}/projects/${projectId}/source-documents`, { cache: "no-store", headers: authHeaders("web") }),
      fetch(`${apiBase}/projects/${projectId}/land-evidence`, {
        cache: "no-store",
        headers: authHeaders("web"),
      }),
      fetch(`${apiBase}/projects/${projectId}/assumption-overrides`, {
        cache: "no-store",
        headers: authHeaders("web"),
      }),
    ]);

    if (docsRes.ok) {
      setDocuments((await docsRes.json()) as SourceDocument[]);
    }
    if (evidenceRes.ok) {
      const body = (await evidenceRes.json()) as LandEvidenceList;
      setLandEvidenceCount(body.records?.length ?? 0);
    }
    if (overrideRes.ok) {
      const body = (await overrideRes.json()) as AssumptionOverrideList;
      setOverrideCount(body.records?.length ?? 0);
    }
  }, [projectId]);

  useEffect(() => {
    void refreshPackageState();
  }, [refreshPackageState]);

  const documentTypeCounts = useMemo(() => {
    return documents.reduce<Record<string, number>>((acc, doc) => {
      const type = doc.documentType || "source_document";
      acc[type] = (acc[type] ?? 0) + 1;
      return acc;
    }, {});
  }, [documents]);

  const packageSlots = [
    {
      label: "사업계획서",
      value: documentTypeCounts.business_plan ?? 0,
      icon: FileText,
    },
    {
      label: "토지대장",
      value: documentTypeCounts.land_ledger ?? 0,
      icon: FileSpreadsheet,
    },
    {
      label: "사업수지 워크북",
      value: documentTypeCounts.feasibility_workbook ?? 0,
      icon: FileSpreadsheet,
    },
    {
      label: "토지 증빙",
      value: landEvidenceCount,
      icon: Database,
    },
    {
      label: "승인 오버라이드",
      value: overrideCount,
      icon: FileJson,
    },
  ];

  async function postFile(endpoint: string, file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${apiBase}/projects/${projectId}/${endpoint}`, {
      method: "POST",
      headers: authHeaders("web"),
      body: formData,
    });
    if (!res.ok) {
      throw new Error(await responseError(res));
    }
    return res.json() as Promise<Record<string, unknown>>;
  }

  async function postJson(endpoint: string, payload: unknown) {
    const res = await fetch(`${apiBase}/projects/${projectId}/${endpoint}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders("web"),
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      throw new Error(await responseError(res));
    }
    return res.json() as Promise<Record<string, unknown>>;
  }

  async function runAction(action: BusyAction, work: () => Promise<string>) {
    setBusyAction(action);
    setError(null);
    setMessage(null);
    try {
      const resultMessage = await work();
      setMessage(resultMessage);
      await refreshPackageState();
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed.");
    } finally {
      setBusyAction(null);
    }
  }

  const uploadBusinessPlan = (runAfterUpload: boolean) =>
    runAction(runAfterUpload ? "business-run" : "business-upload", async () => {
      if (!businessPlan) return "사업계획서 PDF를 먼저 선택하세요.";
      const body = (await postFile(runAfterUpload ? "upload-and-run" : "upload", businessPlan)) as UploadRunResponse;
      if (body.status === "host_llm_required") {
        return "업로드·보관 완료. PF 심사 실행 전 호스트 LLM 추출이 필요합니다.";
      }
      if (body.run?.run_id) {
        return `업로드·파싱 완료, PF 심사 실행 시작 (run ${body.run.run_id}).`;
      }
      return "사업계획서 업로드·파싱 완료.";
    });

  const uploadLandLedger = () =>
    runAction("land-ledger", async () => {
      if (!landLedger) return "토지대장 워크북을 먼저 선택하세요.";
      const analyzed = await postFile("land-ledger/analyze", landLedger);
      await postJson("land-ledger/enrich", {
        landLedger: analyzed.landLedger,
        persist: true,
        persistEvidence: true,
      });
      return "토지대장 분석·보강·저장 완료.";
    });

  const uploadWorkbook = () =>
    runAction("workbook", async () => {
      if (!workbook) return "사업수지 워크북을 먼저 선택하세요.";
      const body = await postFile("feasibility-workbook/validate", workbook);
      const validation = body.validation as { status?: string } | undefined;
      return `사업수지 워크북 보관 완료. 검증 상태: ${validation?.status ?? "알 수 없음"}.`;
    });

  const saveLandEvidence = () =>
    runAction("land-evidence", async () => {
      await postJson("land-evidence", parseJson(landEvidenceJson));
      return "토지 증빙 레코드 저장 완료.";
    });

  const saveOverride = () =>
    runAction("override", async () => {
      await postJson("assumption-overrides", parseJson(overrideJson));
      return "승인된 가정 오버라이드 저장 완료.";
    });

  const runPfReview = () =>
    runAction("pf-review", async () => {
      const body = await postJson("orchestrations/pf-review", {
        requestedBy: "web",
        source: "pilot-input-package-ui",
      });
      const run = body.run as { run_id?: string } | undefined;
      const runId = run?.run_id ?? (body.run_id as string | undefined);
      return runId ? `PF 심사 실행 완료: ${runId}.` : "PF 심사 실행 완료.";
    });

  return (
    <div className="space-y-6 pb-8">
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
          <h2 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            <PackageCheck className="h-7 w-7 text-indigo-600" />
            자료 입력 (Input Package)
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            가진 자료를 한 번에 올리면 AI 파서가 자동 분류·보관합니다. PF 심사 실행 전 사업계획서·토지대장·사업수지·토지증빙을 모읍니다.
          </p>
        </div>
      </div>

      <SmartIntake projectId={projectId} onProcessed={refreshPackageState} />

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {packageSlots.map((slot) => {
          const Icon = slot.icon;
          const ready = slot.value > 0;
          return (
            <div
              key={slot.label}
              className={`rounded-lg border p-4 ${
                ready
                  ? "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100"
                  : "border-slate-200 bg-white text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200"
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <Icon className="h-5 w-5" />
                {ready ? <CheckCircle2 className="h-5 w-5" /> : <AlertCircle className="h-5 w-5 text-amber-500" />}
              </div>
              <p className="mt-3 text-xs font-semibold">{slot.label}</p>
              <p className="mt-1 text-2xl font-black">{slot.value}</p>
            </div>
          );
        })}
      </section>

      {message ? <Notice tone="good" message={message} /> : null}
      {error ? <Notice tone="bad" message={error} /> : null}

      <div className="flex items-center gap-3 pt-2">
        <div className="h-px flex-1 bg-slate-200 dark:bg-slate-800" />
        <p className="text-xs font-bold uppercase tracking-wide text-slate-400">개별 업로드 (고급) · 유형이 분명할 때</p>
        <div className="h-px flex-1 bg-slate-200 dark:bg-slate-800" />
      </div>

      <section className="grid gap-4 xl:grid-cols-2">
        <UploadCard
          icon={<FileText className="h-5 w-5" />}
          title="1. 사업계획서 / 기본계획 PDF"
          description="파서 원본 자료를 업로드하고, 선택적으로 PF 심사 실행을 시작합니다."
          file={businessPlan}
          accept=".pdf"
          onFile={setBusinessPlan}
        >
          <div className="flex flex-col gap-2 sm:flex-row">
            <ActionButton
              icon={<Upload className="h-4 w-4" />}
              label="업로드 및 파싱"
              busy={busyAction === "business-upload"}
              disabled={!businessPlan || busyAction !== null}
              onClick={() => void uploadBusinessPlan(false)}
            />
            <ActionButton
              icon={<GitBranch className="h-4 w-4" />}
              label="업로드 후 심사 실행"
              busy={busyAction === "business-run"}
              disabled={!businessPlan || busyAction !== null}
              primary
              onClick={() => void uploadBusinessPlan(true)}
            />
          </div>
        </UploadCard>

        <UploadCard
          icon={<FileSpreadsheet className="h-5 w-5" />}
          title="2. 토지대장 / 토지조서 워크북"
          description="필지·소유자·지급 기록을 파싱하고 보강 가능한 토지 데이터로 저장합니다."
          file={landLedger}
          accept=".xls,.xlsx"
          onFile={setLandLedger}
        >
          <ActionButton
            icon={<Database className="h-4 w-4" />}
            label="분석 및 저장"
            busy={busyAction === "land-ledger"}
            disabled={!landLedger || busyAction !== null}
            primary
            onClick={() => void uploadLandLedger()}
          />
        </UploadCard>

        <UploadCard
          icon={<FileSpreadsheet className="h-5 w-5" />}
          title="3. 사업수지 워크북"
          description="수식 기반 사업수지 워크북을 보관·검증한 뒤 정식 임포트합니다."
          file={workbook}
          accept=".xlsx,.xlsm"
          onFile={setWorkbook}
        >
          <ActionButton
            icon={<FileArchive className="h-4 w-4" />}
            label="워크북 검증"
            busy={busyAction === "workbook"}
            disabled={!workbook || busyAction !== null}
            primary
            onClick={() => void uploadWorkbook()}
          />
        </UploadCard>

        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <PanelHeader icon={<Database className="h-5 w-5" />} title="4. 토지 증빙 JSON" />
          <textarea
            value={landEvidenceJson}
            onChange={(event) => setLandEvidenceJson(event.target.value)}
            className="mt-4 min-h-64 w-full rounded-lg border border-slate-200 bg-white p-3 font-mono text-xs text-slate-800 outline-none focus:border-indigo-400 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            spellCheck={false}
          />
          <div className="mt-3">
            <ActionButton
              icon={<FileJson className="h-4 w-4" />}
              label="증빙 저장"
              busy={busyAction === "land-evidence"}
              disabled={busyAction !== null}
              primary
              onClick={() => void saveLandEvidence()}
            />
          </div>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 xl:col-span-2">
          <PanelHeader icon={<FileJson className="h-5 w-5" />} title="5. 데이터 품질 오버라이드" />
          <textarea
            value={overrideJson}
            onChange={(event) => setOverrideJson(event.target.value)}
            className="mt-4 min-h-44 w-full rounded-lg border border-slate-200 bg-white p-3 font-mono text-xs text-slate-800 outline-none focus:border-indigo-400 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            spellCheck={false}
          />
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <ActionButton
              icon={<FileJson className="h-4 w-4" />}
              label="오버라이드 저장"
              busy={busyAction === "override"}
              disabled={busyAction !== null}
              onClick={() => void saveOverride()}
            />
            <ActionButton
              icon={<GitBranch className="h-4 w-4" />}
              label="PF 심사 실행"
              busy={busyAction === "pf-review"}
              disabled={busyAction !== null}
              primary
              onClick={() => void runPfReview()}
            />
            <button
              type="button"
              onClick={() => void refreshPackageState()}
              disabled={busyAction !== null}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            >
              <RefreshCw className="h-4 w-4" />
              새로고침
            </button>
          </div>
        </section>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <PanelHeader icon={<FileArchive className="h-5 w-5" />} title="보관된 패키지 파일" />
        {documents.length ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase text-slate-500 dark:border-slate-800">
                <tr>
                  <th className="px-3 py-2">유형</th>
                  <th className="px-3 py-2">파일</th>
                  <th className="px-3 py-2">크기</th>
                  <th className="px-3 py-2">검증</th>
                  <th className="px-3 py-2">무결성</th>
                  <th className="px-3 py-2">업로드</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {documents.map((doc, index) => (
                  <tr key={`${doc.originalFileName}-${index}`}>
                    <td className="px-3 py-3 font-semibold text-slate-950 dark:text-white">{doc.documentType ?? "source_document"}</td>
                    <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{doc.originalFileName ?? "Unnamed file"}</td>
                    <td className="px-3 py-3 text-slate-500">{formatBytes(doc.fileSizeBytes)}</td>
                    <td className="px-3 py-3 text-slate-500">{doc.validationStatus ?? "-"}</td>
                    <td className="px-3 py-3 text-slate-500">{doc.archiveIntegrityStatus ?? "-"}</td>
                    <td className="px-3 py-3 text-slate-500">{formatDate(doc.uploadedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-500 dark:bg-slate-800/70">
            아직 보관된 원본 문서가 없습니다.
          </p>
        )}
      </section>
    </div>
  );
}

function UploadCard({
  icon,
  title,
  description,
  file,
  accept,
  onFile,
  children,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  file: File | null;
  accept: string;
  onFile: (file: File | null) => void;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <PanelHeader icon={icon} title={title} />
      <p className="mt-1 text-sm text-slate-500">{description}</p>
      <label className="mt-4 block">
        <input
          type="file"
          accept={accept}
          onChange={(event) => onFile(event.target.files?.[0] ?? null)}
          className="block w-full text-sm text-slate-500 file:mr-4 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-4 file:py-2.5 file:text-sm file:font-semibold file:text-indigo-700 hover:file:bg-indigo-100 dark:file:bg-indigo-950/30 dark:file:text-indigo-300"
        />
      </label>
      <div className="mt-2 min-h-5 text-xs font-semibold text-slate-500">{file ? file.name : "선택된 파일 없음"}</div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function PanelHeader({ icon, title }: { icon: ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
        {icon}
      </div>
      <h3 className="font-bold text-slate-950 dark:text-white">{title}</h3>
    </div>
  );
}

function ActionButton({
  icon,
  label,
  busy,
  disabled,
  primary = false,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  busy: boolean;
  disabled: boolean;
  primary?: boolean;
  onClick: () => void;
}) {
  const className = primary
    ? "inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
    : "inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

  return (
    <button type="button" onClick={onClick} disabled={disabled} className={className}>
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {label}
    </button>
  );
}

function Notice({ tone, message }: { tone: "good" | "bad"; message: string }) {
  const className =
    tone === "good"
      ? "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100"
      : "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-100";
  return (
    <div className={`flex items-start gap-2 rounded-lg border p-4 text-sm font-semibold ${className}`}>
      {tone === "good" ? <CheckCircle2 className="mt-0.5 h-4 w-4" /> : <AlertCircle className="mt-0.5 h-4 w-4" />}
      <span>{message}</span>
    </div>
  );
}

function parseJson(value: string) {
  try {
    return JSON.parse(value) as unknown;
  } catch (err) {
    throw new Error(err instanceof Error ? `Invalid JSON: ${err.message}` : "Invalid JSON.");
  }
}

async function responseError(res: Response) {
  try {
    const body = (await res.json()) as { detail?: string };
    return body.detail || `Request failed with ${res.status}`;
  } catch {
    return `Request failed with ${res.status}`;
  }
}

function formatBytes(value?: number) {
  if (!value) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR");
}
