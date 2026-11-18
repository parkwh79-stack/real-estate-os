"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { apiBase, authHeaders } from "../../lib/auth-client";
import type { CashflowResult } from "../../lib/project-client";

type Evidence = { label: string; sourceType: string; status: string; value?: number | string; locator?: Record<string, unknown> };
export type StatutoryCostItem = { key: string; label: string; status: string; amountKrw: number | null; legalLookupStatus?: string; missingInputs: string[]; evidence: Evidence[] };
type Basis = { calculationRunId: string; financialInputHash: string; createdAt: string; totalProjectCost: number; lastMonthIndex: number };
type Review = { items: StatutoryCostItem[]; basisRuns: Basis[]; documents: { archiveId: string; fileName: string }[];
  workbookSubtotal?: { amountKrw: number | null; label: string } | null };
type Choice = { amount: string; amountKind: "estimate" | "notified" | "paid"; inclusion: "included" | "additional" | "not_applicable";
  archiveId: string; location: string; reason: string; month: string };
type SavedItem = Omit<Choice, "amount" | "amountKind" | "archiveId" | "location" | "month"> & {
  key: string; label: string; amountKrw: number | null; amountKind: Choice["amountKind"] | null; paymentMonthIndex: number | null;
  sourceLocator: { archiveId: string; fileName: string; location: string };
};
type SavedSelection = { schemaVersion: string; baseCalculationRunId: string; items: SavedItem[]; additionalCostKrw: number };
type RunReceipt = { calculationRunId: string };
const FIELD = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-950";
const EMPTY_CHOICE: Choice = { amount: "", amountKind: "estimate", inclusion: "included", archiveId: "", location: "", reason: "", month: "" };
const LEGAL_LOOKUP: Record<string, string> = {
  unavailable: "법령 조회에 연결하지 못했습니다.",
  no_match: "조회 결과에서 해당 법령을 확인하지 못했습니다.",
  reference_found: "공식 법령 출처를 확인했습니다.",
};
const AMOUNT_KIND = { estimate: "검토 추정액", notified: "고지액", paid: "실납부액" };

function won(value: number): string {
  return value.toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + "원";
}

function savedSelection(cashflow: CashflowResult | null): SavedSelection | undefined {
  const selection = cashflow?.inputSnapshot?.statutoryCostSelection as SavedSelection | undefined;
  return selection?.schemaVersion === "realytics-statutory-cost-selection-v1" && Array.isArray(selection.items)
    ? selection : undefined;
}

function officialUrl(locator?: Record<string, unknown>): string | undefined {
  const raw = locator?.url ?? locator?.sourceUrl ?? locator?.source_url;
  if (typeof raw !== "string") return;
  try {
    const url = new URL(raw);
    if (url.protocol === "https:" && (url.hostname === "law.go.kr" || url.hostname.endsWith(".law.go.kr"))) return url.href;
  } catch {
    return;
  }
}

export function StatutoryCostEvidence({ items }: { items: StatutoryCostItem[] }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">{items.map((item) => <article key={item.key} className="rounded-lg border border-slate-200 p-4 text-sm">
            <h3 className="font-bold text-slate-950">{item.label}</h3>
            <p className="mt-1 text-slate-700">{item.amountKrw === null ? "산정액 미확인" : "산정 후보 " + won(item.amountKrw)}</p>
            {item.legalLookupStatus && LEGAL_LOOKUP[item.legalLookupStatus] ? <p className="mt-1 text-xs text-slate-600">
            {LEGAL_LOOKUP[item.legalLookupStatus]}
          </p> : null}
          {item.evidence.length > 0 ? <ul className="mt-2 space-y-1 text-slate-600">{item.evidence.map((evidence, index) => {
              const url = officialUrl(evidence.locator);
              return <li key={evidence.sourceType + "-" + index}>
                {url ? <a href={url} target="_blank" rel="noreferrer" className="font-medium text-blue-700 underline">{evidence.label}</a> : evidence.label}
                {evidence.value === undefined ? null : " · " + evidence.value.toLocaleString("ko-KR")}
              </li>;
            })}</ul> : null}
            {item.missingInputs.length > 0 ? <details className="mt-2 text-slate-600">
              <summary className="cursor-pointer font-medium">확인할 항목 {item.missingInputs.length}개</summary>
              <ul className="mt-1 list-inside list-disc">{item.missingInputs.map((field) => <li key={field}>{field}</li>)}</ul>
            </details> : null}
          </article>)}</div>
  );
}

async function bodyOrError(response: Response, fallback: string) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  return body;
}

export function StatutoryCostWorkspace({ projectId, cashflow, onSaved }: {
  projectId: string; cashflow: CashflowResult | null; onSaved: (result: RunReceipt) => Promise<void>;
}) {
  const [opened, setOpened] = useState(false);
  const [review, setReview] = useState<Review>();
  const [error, setError] = useState("");
  const [requestVersion, setRequestVersion] = useState(0);
  const baseUrl = apiBase + "/projects/" + encodeURIComponent(projectId) + "/statutory-cost-review";
  const saved = savedSelection(cashflow);

  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController();
    void fetch(baseUrl, { headers: authHeaders("web"), cache: "no-store", signal: controller.signal })
      .then((response) => bodyOrError(response, "법정 비용 검토 자료를 불러오지 못했습니다."))
      .then((body) => { if (!controller.signal.aborted) setReview(body); })
      .catch((caught: unknown) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "조회에 실패했습니다."); });
    return () => controller.abort();
  }, [baseUrl, opened, requestVersion]);

  return (
    <details className="rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm" onToggle={(event) => {
      if (event.currentTarget.open) setOpened(true);
    }}>
      <summary className="cursor-pointer font-bold text-slate-950">
        법정 비용 검토 {saved ? <span className="ml-2 text-sm font-medium text-indigo-700">이 실행에 {saved.items.length}건 반영</span> : null}
      </summary>
      <div className="mt-4 space-y-4">
        <p className="text-sm text-slate-600">공식 근거와 기존 사업비를 확인한 뒤 채택할 금액을 선택하세요.</p>
        {saved ? <div className="rounded-lg bg-indigo-50 p-4 text-sm text-indigo-950">
          <p className="font-bold">이 실행의 법정 비용 · 추가 {won(saved.additionalCostKrw)}</p>
          <ul className="mt-2 space-y-1">{saved.items.map((item) => <li key={item.key}>
            {item.label} · {item.inclusion === "not_applicable" ? "비적용 검토" : [
              item.amountKrw === null ? "금액 미확인" : won(item.amountKrw),
              item.amountKind ? AMOUNT_KIND[item.amountKind] : "",
              item.inclusion === "included" ? "기존 사업비 포함" : "M" + item.paymentMonthIndex + " 추가",
            ].filter(Boolean).join(" · ")} · {item.sourceLocator.fileName} {item.sourceLocator.location}
          </li>)}</ul>
        </div> : null}
        {error ? <div className="text-sm text-rose-700" role="alert">{error}{" "}
          <button type="button" className="underline" onClick={() => { setError(""); setRequestVersion(requestVersion + 1); }}>다시 조회</button>
        </div> : opened && !review ? <p role="status" className="text-sm text-slate-500">검토 자료를 불러오는 중…</p> : null}
        {review ? <>
          {review.workbookSubtotal ? <p className="text-sm text-slate-700">
            원본 {review.workbookSubtotal.label} · {review.workbookSubtotal.amountKrw === null ? "금액 미확인" : won(review.workbookSubtotal.amountKrw)}
          </p> : null}
          <StatutoryCostEvidence items={review.items} />
          <Link className="text-sm font-medium text-blue-700 underline" href={"/project/" + encodeURIComponent(projectId) + "/chief-orchestration"}>AI 딜룸에서 근거 사전 검토</Link>
          <CostSelectionForm key={cashflow?.runId ?? "no-run"} review={review} runId={cashflow?.runId} saved={saved}
            endpoint={baseUrl + "/calculate"} onSaved={onSaved} />
        </> : null}
      </div>
    </details>
  );
}

function CostSelectionForm({ review, runId, saved, endpoint, onSaved }: {
  review: Review; runId?: string; saved?: SavedSelection; endpoint: string; onSaved: (result: RunReceipt) => Promise<void>;
}) {
  const [basisId, setBasisId] = useState(saved?.baseCalculationRunId ?? runId ?? "");
  const [choices, setChoices] = useState<Record<string, Choice>>(() => Object.fromEntries((saved?.items ?? []).map((item) => (
    [item.key, { amount: String(item.amountKrw ?? ""), amountKind: item.amountKind ?? "estimate", inclusion: item.inclusion,
      archiveId: item.sourceLocator.archiveId, location: item.sourceLocator.location, reason: item.reason, month: String(item.paymentMonthIndex ?? "") }]
  ))));
  const [status, setStatus] = useState<{ kind: "error" | "success"; message: string }>();
  const [saving, setSaving] = useState(false);
  const basis = review.basisRuns.find((row) => row.calculationRunId === basisId) ?? (basisId === "" && !runId ? review.basisRuns[0] : undefined);

  function change(key: string, patch: Partial<Choice>) {
    setChoices((rows) => ({ ...rows, [key]: { ...(rows[key] ?? EMPTY_CHOICE), ...patch } }));
  }

  function select(key: string, checked: boolean) {
    setChoices((rows) => Object.fromEntries(checked ? [...Object.entries(rows), [key, { ...EMPTY_CHOICE }]] : Object.entries(rows).filter(([current]) => current !== key)));
  }

  async function calculate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!basis || saving) return;
    setSaving(true);
    setStatus(undefined);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({
          baseCalculationRunId: basis.calculationRunId,
          expectedFinancialInputHash: basis.financialInputHash,
          items: Object.entries(choices).map(([key, choice]) => ({
            key, amountKrw: choice.inclusion === "not_applicable" ? null : Number(choice.amount),
            amountKind: choice.inclusion === "not_applicable" ? null : choice.amountKind, inclusion: choice.inclusion,
            archiveId: choice.archiveId, location: choice.location, reason: choice.reason,
            paymentMonthIndex: choice.inclusion === "additional" ? Number(choice.month) : null,
          })),
        }),
      });
      const result = await bodyOrError(response, "금액·월차·근거를 확인한 뒤 다시 계산하세요.");
      if (typeof result.calculationRunId !== "string" || !result.calculationRunId) throw new Error("계산 저장 결과를 확인하지 못했습니다.");
      setStatus({ kind: "success", message: "법정 비용을 반영한 검토 계산을 저장했습니다. 비교에서 기준안과 변경안을 확인할 수 있습니다." });
      await onSaved(result);
    } catch (caught) {
      setStatus({ kind: "error", message: caught instanceof Error ? caught.message : "계산을 저장하지 못했습니다." });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="space-y-4 border-t border-slate-200 pt-4" onSubmit={(event) => void calculate(event)}>
      <label className="block max-w-xl text-sm font-medium text-slate-800">
        기준 현금흐름
        <select className={FIELD} value={basis?.calculationRunId ?? ""} onChange={(event) => {
          setBasisId(event.target.value);
          setChoices({});
        }} disabled={saving} required>
          <option value="">기준 계산 선택</option>
          {review.basisRuns.map((row) => <option key={row.calculationRunId} value={row.calculationRunId}>
            {row.createdAt ? new Date(row.createdAt).toLocaleString("ko-KR") + " · " : ""}{won(row.totalProjectCost)} · {row.calculationRunId.slice(-8)}
          </option>)}
        </select>
      </label>
      {review.basisRuns.length === 0 ? <p className="text-sm text-slate-600">실효 입력이 저장된 월별 현금흐름을 먼저 계산하세요. 원본 행 순서를 보존하는 실행은 기준으로 선택할 수 없습니다.</p> : null}
      {review.documents.length === 0 ? <p className="text-sm text-slate-600">산출 근거, 고지서 또는 납부 증빙을 이 프로젝트에 먼저 보관하세요.</p> : null}
      {basis && review.documents.length > 0 ? review.items.map((item) => {
        const choice = choices[item.key];
        return <fieldset key={item.key} disabled={saving} className="rounded-lg border border-slate-200 p-3">
          <label className="flex items-center gap-2 text-sm font-bold text-slate-900">
            <input type="checkbox" checked={!!choice} onChange={(event) => select(item.key, event.target.checked)} />{item.label} 채택 검토
          </label>
          {choice ? <div className="mt-3 grid gap-3 text-sm text-slate-800 md:grid-cols-2 xl:grid-cols-3">
            <label>기준 사업비 반영<select className={FIELD} value={choice.inclusion} onChange={(event) => change(item.key, { inclusion: event.target.value as Choice["inclusion"] })}>
              <option value="included">이미 포함되어 있음</option><option value="additional">미포함 확인 · 추가 반영</option><option value="not_applicable">비적용 근거 기록</option>
            </select></label>
            {choice.inclusion !== "not_applicable" ? <>
              <label>금액(원)<input aria-label={item.label + " 금액(원)"} className={FIELD} type="number" min="1" max="1000000000000000" step="1" required value={choice.amount} onChange={(event) => change(item.key, { amount: event.target.value })} /></label>
            <label>금액 구분<select className={FIELD} value={choice.amountKind} onChange={(event) => change(item.key, { amountKind: event.target.value as Choice["amountKind"] })}>
              <option value="estimate">검토 추정액</option><option value="notified">고지액</option><option value="paid">실납부액</option>
            </select></label>
            </> : null}
            {choice.inclusion === "additional" ? <label>납부 월차 (M0~M{basis.lastMonthIndex})<input className={FIELD} type="number" min="0" max={basis.lastMonthIndex} step="1" required value={choice.month} onChange={(event) => change(item.key, { month: event.target.value })} /></label> : null}
            <label>근거 문서<select className={FIELD} value={choice.archiveId} onChange={(event) => change(item.key, { archiveId: event.target.value })} required>
              <option value="">보관 원본 선택</option>{review.documents.map((document) => <option key={document.archiveId} value={document.archiveId}>{document.fileName}</option>)}
            </select></label>
            <label>문서 위치<input className={FIELD} placeholder="예: 고지서 2쪽 또는 사업수지!D52" required maxLength={200} value={choice.location} onChange={(event) => change(item.key, { location: event.target.value })} /></label>
            <label className="md:col-span-2">포함 여부를 확인한 근거<input className={FIELD} required maxLength={600} value={choice.reason} onChange={(event) => change(item.key, { reason: event.target.value })} /></label>
          </div> : null}
        </fieldset>;
      }) : null}
      <button type="submit" disabled={saving || !basis || !Object.keys(choices).length} className="rounded-lg bg-slate-950 px-4 py-2 text-sm font-bold text-white disabled:opacity-40">
        {saving ? "저장 중…" : "법정 비용 반영·계산 저장"}
      </button>
      {status ? <p role={status.kind === "error" ? "alert" : "status"} className={status.kind === "error" ? "text-sm text-rose-700" : "text-sm text-emerald-800"}>{status.message}</p> : null}
    </form>
  );
}
