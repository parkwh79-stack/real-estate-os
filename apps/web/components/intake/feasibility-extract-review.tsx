"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { authHeaders, apiBase } from "../../lib/auth-client";
import { eokToWon, wonToEok, termsDraft, termsHaveChanges, reviewedTermsPayload, workbookSourceCell, workbookSourceCells, canLoadWorkbookFinancialField, workbookFinancialSourceCells,
  type FieldReview as Review, type ReviewSource as Source, type ReviewTerms, type TermsDraft, type WorkbookFinancingReview, type WorkbookFinancialReview } from "../../lib/feasibility-review";
import { fetchWorkbookCellPreview, type WorkbookCellPreview } from "../../lib/document-review-workspace";
import { FeasibilityTermsReview } from "./feasibility-terms-review";
import { WorkbookFinancialReviewPanel } from "./workbook-financial-review";
import type { CashFlowTimingSource } from "../../lib/cash-flow-timing";

const FIELDS = [
  ["total_revenue", "총매출"], ["total_cost", "총사업비"], ["land_cost", "토지비"],
  ["construction_cost", "공사비"], ["finance_cost", "금융비용"], ["other_cost", "기타사업비"],
  ["development_profit", "문서상 개발이익"], ["pf_amount", "PF 총액"],
] as const;
type Row = Review & { key: string; label: string; eok: string; dirty: boolean; sourceCells?: string[] };
type ReviewState = {
  values: Record<string, number | null>;
  fieldReviews: Record<string, Review>;
  reviewHash: string;
  sourceDocuments?: Source[];
  terms?: ReviewTerms;
  termReviews?: Partial<TermsDraft["reviews"]>;
  workbookFinancingReview?: WorkbookFinancingReview | null;
  workbookFinancialReview?: WorkbookFinancialReview | null;
  cashFlowTimingSource?: CashFlowTimingSource | null;
};
const control = "rounded-md border border-slate-300 bg-white px-2 py-2 text-sm dark:border-slate-700 dark:bg-slate-900";
const button = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40 dark:border-slate-700";

async function readResponse<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : `요청에 실패했습니다 (${response.status}).`);
  }
  return body as T;
}

function rowsFromState(state: ReviewState): Row[] {
  return FIELDS.map(([key, label]) => ({
    key, label, eok: wonToEok(state.values[key]), dirty: false,
    origin: state.fieldReviews[key]?.origin ?? "user_assumption",
    reason: state.fieldReviews[key]?.reason ?? "",
    sourceCell: state.fieldReviews[key]?.sourceCell ?? "",
    sourceDocument: state.fieldReviews[key]?.sourceDocument,
  }));
}

export function FeasibilityExtractReview({ projectId, onApplied }: { projectId: string; onApplied?: () => void }) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const savingRef = useRef(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [terms, setTerms] = useState<TermsDraft>(() => termsDraft());
  const [workbookFinancing, setWorkbookFinancing] = useState<WorkbookFinancingReview | null>(null);
  const [workbookFinancial, setWorkbookFinancial] = useState<WorkbookFinancialReview | null>(null);
  const [cashFlowTimingSource, setCashFlowTimingSource] = useState<CashFlowTimingSource | null>(null);
  const [reviewHash, setReviewHash] = useState("");
  const [documents, setDocuments] = useState<Source[]>([]);
  const [archiveId, setArchiveId] = useState("");
  const [useAI, setUseAI] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState<WorkbookCellPreview | null>(null);
  const [sourceChoices, setSourceChoices] = useState<Review[]>([]);
  const endpoint = `${apiBase}/projects/${encodeURIComponent(projectId)}/feasibility-workbook`;
  const activeEndpointRef = useRef<string | null>(null);

  const restore = useCallback((state: ReviewState) => {
    setRows(rowsFromState(state));
    setTerms(termsDraft(state.terms, state.termReviews));
    setWorkbookFinancing(state.workbookFinancingReview ?? null);
    setWorkbookFinancial(state.workbookFinancialReview ?? null);
    setCashFlowTimingSource(state.cashFlowTimingSource ?? null);
    setReviewHash(state.reviewHash);
    if (state.sourceDocuments) setDocuments(state.sourceDocuments);
  }, []);

  useEffect(() => {
    let active = true;
    activeEndpointRef.current = endpoint;
    setBusy(true); setReviewHash(""); setRows([]); setTerms(termsDraft()); setArchiveId("");
    setPreview(null); setSourceChoices([]); setError(null); setNotice(null); setWorkbookFinancing(null); setWorkbookFinancial(null); setCashFlowTimingSource(null);
    void fetch(`${endpoint}/review`, { cache: "no-store", headers: authHeaders("web") })
      .then(readResponse<ReviewState>)
      .then((state) => { if (active) restore(state); })
      .catch((err) => { if (active) setError(String(err.message)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; activeEndpointRef.current = null; };
  }, [endpoint, restore]);

  const reload = async () => {
    setBusy(true); setError(null); setNotice(null); setPreview(null); setSourceChoices([]);
    try {
      restore(await readResponse<ReviewState>(await fetch(`${endpoint}/review`, { cache: "no-store", headers: authHeaders("web") })));
    } catch (err) { setError(err instanceof Error ? err.message : "저장값을 불러오지 못했습니다."); }
    finally { setBusy(false); }
  };

  const edit = (key: string, patch: Partial<Row>) => {
    setRows((current) => current.map((row) => row.key === key ? { ...row, ...patch, dirty: true } : row));
    setNotice(null);
  };

  const extract = async (file: File) => {
    setBusy(true); setError(null); setNotice(null); setPreview(null); setSourceChoices([]);
    try {
      const body = new FormData(); body.append("file", file);
      const data = await readResponse<{
        upload: Source; parsingSource: string; note?: string;
        fields?: Record<string, { value_krw?: number | null; source?: string }>;
      }>(await fetch(`${endpoint}/extract?manual_review=${!useAI}`, { method: "POST", headers: authHeaders("web"), body }));
      setDocuments((current) => [...current.filter((item) => item.archiveId !== data.upload.archiveId), data.upload]);
      setArchiveId(data.upload.archiveId);
      if (["llm", "llm_chunked"].includes(data.parsingSource)) {
        setRows((current) => current.map((row) => {
          const field = data.fields?.[row.key];
          if (row.dirty || typeof field?.value_krw !== "number") return row;
          return { ...row, eok: wonToEok(field.value_krw), sourceCell: field.source ?? "",
            sourceDocument: data.upload, origin: "document_correction", reason: "", dirty: true };
        }));
      }
      setNotice(data.parsingSource === "llm_unavailable"
        ? "AI 추출을 사용할 수 없습니다. 원본은 보관했으므로 아래에서 직접 확인·입력할 수 있습니다."
        : data.note ?? "원본을 보관했습니다. 추출되지 않은 값은 직접 확인해 입력하세요.");
    } catch (err) { setError(err instanceof Error ? err.message : "파일을 처리하지 못했습니다."); }
    finally { setBusy(false); if (inputRef.current) inputRef.current.value = ""; }
  };

  const showSource = async (row: Review & { sourceCells?: string[] }) => {
    setBusy(true); setError(null); setPreview(null); setSourceChoices([]);
    try {
      const source = row.sourceDocument ?? documents.find((item) => item.archiveId === archiveId);
      const declaredCells = row.sourceCells ?? workbookSourceCells(row.sourceCell);
      if (declaredCells.length > 1) {
        setSourceChoices(declaredCells.map((cell) => ({ ...row, sourceCell: cell, sourceCells: undefined })));
        return;
      }
      const locator = workbookSourceCell(row.sourceCell);
      if (!source || !locator) throw new Error("원본 문서를 선택하고 출처를 시트명!셀 형식으로 입력하세요. 예: 손익!I2");
      setPreview(await fetchWorkbookCellPreview({ projectId, archiveId: source.archiveId, ...locator }));
    } catch (err) { setError(err instanceof Error ? err.message : "원본 셀을 열지 못했습니다."); }
    finally { setBusy(false); }
  };

  const loadFinancialFields = (keys: string[]) => {
    if (busy || !workbookFinancial) return;
    const source = workbookFinancial.sourceDocument;
    const applicable = new Set(keys.filter((key) => canLoadWorkbookFinancialField(workbookFinancial, key)));
    const preserved = rows.filter((row) => applicable.has(row.key) && row.dirty).length;
    const loadable = rows.filter((row) => applicable.has(row.key) && !row.dirty).length;
    setRows((current) => current.map((row) => {
      const field = workbookFinancial.sourceFields[row.key];
      if (!applicable.has(row.key) || row.dirty || !field) return row;
      return { ...row, eok: wonToEok(field.value), sourceCell: field.sourceCell, sourceCells: workbookFinancialSourceCells(field), sourceDocument: source,
        origin: "document_correction", reason: "", dirty: true };
    }));
    setError(null);
    setNotice(`${loadable}개 항목을 검토 입력에 불러왔습니다. 원본과 금액 기준을 확인하고 검토 이유를 입력한 뒤 저장하세요.${preserved ? ` 저장 전 수정 중인 ${preserved}개 항목은 유지했습니다.` : ""}`);
  };

  const save = async (): Promise<boolean> => {
    if (savingRef.current) return false;
    savingRef.current = true;
    setBusy(true); setError(null); setNotice(null);
    try {
      const changed = rows.filter((row) => row.dirty);
      const values: Record<string, number> = {};
      const reviews: Record<string, Review> = {};
      for (const row of changed) {
        if (!row.reason.trim()) throw new Error(`${row.label}: 수정 또는 가정의 이유를 입력하세요.`);
        values[row.key] = eokToWon(row.eok);
        if (row.key !== "development_profit" && values[row.key]! < 0) throw new Error(`${row.label}: 음수를 입력할 수 없습니다.`);
        reviews[row.key] = { origin: row.origin, reason: row.reason.trim(), sourceCell: row.sourceCell.trim() };
      }
      const termPayload = reviewedTermsPayload(terms, documents.find((item) => item.archiveId === archiveId), cashFlowTimingSource);
      if (!changed.length && !termsHaveChanges(terms)) throw new Error("수정한 항목이 없습니다.");
      const result = await readResponse<{ reviewState: ReviewState }>(await fetch(`${endpoint}/apply-input`, {
        method: "POST", headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ feasibility_input: values, fieldReviews: reviews, expectedReviewHash: reviewHash, ...termPayload,
          fieldSourceDocuments: Object.fromEntries(changed.filter((row) => row.origin === "document_correction")
            .map((row) => [row.key, row.sourceDocument ?? documents.find((item) => item.archiveId === archiveId) ?? null])) }),
      }));
      if (activeEndpointRef.current !== endpoint) return false;
      restore(result.reviewState);
      setNotice("입력과 검토 이유를 저장했습니다. 다시 열어 이어갈 수 있습니다. 변경된 값으로 사업수지를 다시 계산하세요.");
      onApplied?.();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "입력을 저장하지 못했습니다.");
      return false;
    } finally { savingRef.current = false; setBusy(false); }
  };

  const hasChanges = rows.some((row) => row.dirty) || termsHaveChanges(terms);
  const continueTo = async (destination: "documents/review" | "feasibility") => {
    if (busy || savingRef.current || !reviewHash || activeEndpointRef.current !== endpoint) return;
    if (hasChanges && !await save()) return;
    if (activeEndpointRef.current !== endpoint) return;
    router.push(`/project/${encodeURIComponent(projectId)}/${destination}`);
  };
  return (
    <section aria-label="사업수지 입력 검토" className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h2 className="font-black text-slate-950 dark:text-white">사업수지 숫자·기간·대출 조건 확인·수정·저장</h2>
      <p className="mt-1 text-sm text-slate-500">AI 연결 없이 직접 입력할 수 있습니다. 금액은 억원 단위이며 원 단위까지 보존합니다. 빈 항목은 미입력으로 남습니다.</p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" className={button} disabled={busy || !reviewHash} onClick={() => inputRef.current?.click()}>사업수지 Excel 선택</button>
        <input ref={inputRef} type="file" accept=".xlsx,.xlsm" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void extract(file); }} />
        <label className="text-xs"><input type="checkbox" checked={useAI} disabled={busy} onChange={(event) => setUseAI(event.target.checked)} /> 연결된 AI로 추출 요청 (제공자에 따라 비용 발생)</label>
        <button type="button" className={button} disabled={busy} onClick={() => void reload()}>저장값 다시 불러오기{hasChanges ? " (수정 취소)" : ""}</button>
      </div>
      <label className="mt-3 flex flex-wrap items-center gap-2 text-sm">확인할 원본
        <select aria-label="확인할 원본" value={archiveId} disabled={busy} className={`${control} max-w-full`} onChange={(event) => { setArchiveId(event.target.value); setPreview(null); setSourceChoices([]); }}>
          <option value="">문서 선택</option>
          {documents.map((item) => <option key={item.archiveId} value={item.archiveId}>{item.originalFileName}</option>)}
        </select>
      </label>
      {busy ? <p role="status" className="mt-3 text-sm">처리 중입니다.</p> : null}
      {error ? <p role="alert" className="mt-3 text-sm font-bold text-rose-600">{error}</p> : null}
      {notice ? <p role="status" className="mt-3 text-sm font-bold text-emerald-700">{notice}</p> : null}
      {workbookFinancial ? <WorkbookFinancialReviewPanel key={`${workbookFinancial.sourceDocument?.archiveId}:${workbookFinancial.profile}:${reviewHash}`} review={workbookFinancial}
        disabled={busy} onLoad={loadFinancialFields} onShowSource={(source) => void showSource(source)} /> : null}
      <div className="mt-4 grid gap-3 xl:grid-cols-2">
        {rows.map((row) => <fieldset key={row.key} disabled={busy} className="min-w-0 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
          <legend className="px-1 text-sm font-bold">{row.label}{row.dirty ? " · 저장 전" : ""}</legend>
          <div className="flex flex-wrap items-center gap-2">
            <input aria-label={`${row.label} (억원)`} value={row.eok} onChange={(event) => edit(row.key, { eok: event.target.value })} inputMode="decimal" placeholder="미입력" className={`${control} w-40 text-right`} />
            <span className="text-xs">억원</span>
            <select aria-label={`${row.label} 입력 구분`} value={row.origin} onChange={(event) => edit(row.key, { origin: event.target.value as Review["origin"] })} className={control}>
              <option value="user_assumption">사용자 가정</option><option value="document_correction">원본 대조·수정</option>
            </select>
          </div>
          {row.origin === "document_correction" ? <div className="mt-2 flex flex-wrap gap-2">
            <input aria-label={`${row.label} 출처 셀`} value={row.sourceCell} onChange={(event) => edit(row.key, { sourceCell: event.target.value, sourceCells: undefined,
              sourceDocument: documents.find((item) => item.archiveId === archiveId) ?? row.sourceDocument })} placeholder="시트명!셀 (예: 손익!I2)" className={`${control} min-w-0 flex-1`} />
            <button type="button" className={button} onClick={() => void showSource(row)}>원본 보기</button>
            {row.sourceDocument ? <p className="w-full break-all text-xs text-slate-500">{row.sourceDocument.originalFileName}</p> : null}
          </div> : null}
          <input aria-label={`${row.label} 검토 이유`} value={row.reason} onChange={(event) => edit(row.key, { reason: event.target.value })} placeholder="수정 이유 또는 가정의 근거" className={`${control} mt-2 w-full`} />
        </fieldset>)}
      </div>
      <FeasibilityTermsReview draft={terms} disabled={busy} workbookFinancing={workbookFinancing} cashFlowTimingSource={cashFlowTimingSource} selectedSource={documents.find((item) => item.archiveId === archiveId)}
        onChange={(next) => { setTerms(next); setNotice(null); }} onShowSource={(review) => void showSource(review)} />
      {sourceChoices.length ? <div aria-label="합계 원본 셀 선택" className="mt-4 rounded-lg border border-indigo-200 p-3 dark:border-indigo-800">
        <p className="text-sm font-bold">합계를 구성하는 원본 셀을 선택하세요.</p><div className="mt-2 flex flex-wrap gap-2">{sourceChoices.map((source) => <button key={source.sourceCell} type="button" className={button} disabled={busy}
          onClick={() => void showSource(source)}>{source.sourceCell} 원본 보기</button>)}</div>
      </div> : null}
      {preview ? <div aria-label="원본 셀 미리보기" className="mt-4 overflow-x-auto rounded-lg border p-3">
        <p className="text-sm font-bold">{preview.sheetName}!{preview.focusRange} · 원본 셀</p>
        <p className="text-xs text-slate-500">수식은 실행하지 않고 보관된 값과 수식만 표시합니다.</p>
        <table className="mt-2 text-xs"><tbody>{preview.rows.map((row) => <tr key={row.rowNumber}>{row.cells.map((cell) => <td key={cell.coordinate} className={`border p-2 ${cell.focused ? "bg-amber-100 text-slate-900" : ""}`}>
          <span className="block text-slate-500">{cell.coordinate}</span>{String(cell.displayValue ?? "—")}{cell.formula ? <span className="block">{cell.formula}</span> : null}
        </td>)}</tr>)}</tbody></table>
      </div> : null}
      <p className="mt-4 text-xs text-slate-500">이 저장은 분석용 입력과 사용자 검토 기록입니다. 원본 문서와 정식 승인값을 바꾸지 않으며, 승인된 사업수지가 있는 경우 해당 기준값이 우선합니다. 저장 후 분석 화면에서 사업수지를 다시 계산하세요.</p>
      {hasChanges ? <p className="mt-2 text-sm text-amber-700">저장 전 수정값이 있습니다. 아래 버튼으로 다음 화면에 이동하면 입력과 검토 이유를 먼저 저장합니다.</p> : null}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" disabled={busy || !reviewHash || !hasChanges} onClick={() => void save()} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-bold text-white disabled:opacity-40">검토 입력 저장</button>
        {hasChanges || busy || !reviewHash ? <>
          <button type="button" disabled={busy || !reviewHash} onClick={() => void continueTo("documents/review")} className="text-sm font-bold text-indigo-600 disabled:opacity-40">{hasChanges ? "저장하고 문서·출처 검토" : "문서·출처 검토"}</button>
          <button type="button" disabled={busy || !reviewHash} onClick={() => void continueTo("feasibility")} className="text-sm font-bold text-indigo-600 disabled:opacity-40">{hasChanges ? "저장하고 사업수지 분석으로 이동" : "사업수지 분석으로 이동"}</button>
        </> : <>
          <Link href={`/project/${encodeURIComponent(projectId)}/documents/review`} className="text-sm font-bold text-indigo-600">문서·출처 검토</Link>
          <Link href={`/project/${encodeURIComponent(projectId)}/feasibility`} className="text-sm font-bold text-indigo-600">사업수지 분석으로 이동</Link>
        </>}
      </div>
    </section>
  );
}
