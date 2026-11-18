"use client";

import { useEffect, useState } from "react";
import { FileSpreadsheet, Loader2, RefreshCw } from "lucide-react";
import {
  errorMessage, fetchWorkbookCellPreview, fetchWorkbookOriginalSheets,
  type SourceLocatorRecord, type WorkbookCellPreview, type WorkbookOriginalSheets, type WorkbookPreviewCell,
} from "../../lib/document-review-workspace";

type WindowSelection = { sheetName: string; range: string; evidence: boolean };
type Bounds = { firstColumn: number; firstRow: number; lastColumn: number; lastRow: number };

function columnNumber(label: string) {
  return [...label].reduce((value, character) => value * 26 + character.charCodeAt(0) - 64, 0);
}
function columnLabel(value: number) {
  let label = "";
  for (let column = value; column > 0; column = Math.floor((column - 1) / 26)) label = String.fromCharCode(65 + (column - 1) % 26) + label;
  return label;
}
function rangeBounds(value: string): Bounds | null {
  const match = value.trim().toUpperCase().match(/^\$?([A-Z]{1,3})\$?([1-9]\d{0,4})(?::\$?([A-Z]{1,3})\$?([1-9]\d{0,4}))?$/);
  if (!match) return null;
  const firstColumn = columnNumber(match[1]!); const firstRow = Number(match[2]);
  const lastColumn = columnNumber(match[3] ?? match[1]!); const lastRow = Number(match[4] ?? match[2]);
  return lastColumn >= firstColumn && lastRow >= firstRow ? { firstColumn, firstRow, lastColumn, lastRow } : null;
}
function boundsRange(value: Bounds) {
  return `${columnLabel(value.firstColumn)}${value.firstRow}:${columnLabel(value.lastColumn)}${value.lastRow}`;
}
function cellValue(value: unknown, numberFormat = "") {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") {
    // Percentages stay percentages; display precision never changes source data.
    const percentage = numberFormat.replace(/"[^"]*"|\\./g, "").includes("%");
    return (percentage ? value * 100 : value).toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + (percentage ? "%" : "");
  }
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return typeof value === "string" ? value : JSON.stringify(value);
}
function cellState(cell: WorkbookPreviewCell) {
  if (cell.valueState === "formula_cache_missing" || (cell.formula && (cell.displayValue === null || cell.displayValue === undefined))) return "수식 · 저장 결과 없음";
  if (cell.valueState === "formula_cache_error") return "수식 · 저장 결과 오류";
  if (cell.formula) return "수식 · 원본 저장 결과";
  if (cell.valueState === "error" || cell.dataType === "e") return "원본 오류";
  if (cell.valueState === "empty" || cell.displayValue === null || cell.displayValue === undefined || cell.displayValue === "") return "빈 셀";
  return "원본 입력값";
}

export function WorkbookOriginalPreview({ projectId, archiveId, locator, onSelectSourceLocator }: {
  projectId: string; archiveId: string; locator?: SourceLocatorRecord;
  onSelectSourceLocator: (locator: SourceLocatorRecord) => void;
}) {
  const [sheets, setSheets] = useState<WorkbookOriginalSheets | null>(null);
  const [selection, setSelection] = useState<WindowSelection | null>(null);
  const [rangeInput, setRangeInput] = useState("");
  const [preview, setPreview] = useState<WorkbookCellPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rangeError, setRangeError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [selectedCoordinate, setSelectedCoordinate] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null); setSheets(null); setPreview(null); setSelection(null);
    void fetchWorkbookOriginalSheets({ projectId, archiveId, signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      setSheets(result);
      const sheetName = String(locator?.sheetName ?? locator?.sheet ?? "");
      const range = String(locator?.cellReference ?? locator?.cellRange ?? locator?.cell ?? "");
      const evidenceBounds = rangeBounds(range);
      const evidence = Boolean(sheetName && evidenceBounds
        && evidenceBounds.lastRow <= result.limits.maxRows && evidenceBounds.lastColumn <= result.limits.maxColumns
        && (evidenceBounds.lastRow - evidenceBounds.firstRow + 7) * (evidenceBounds.lastColumn - evidenceBounds.firstColumn + 7) <= result.limits.maxCells
        && result.sheets.some((sheet) => sheet.sheetName === sheetName));
      const initialSheet = evidence ? sheetName : result.defaultSheetName ?? result.sheets.at(0)?.sheetName;
      const initialRange = evidence ? range : result.defaultRange ?? "A1:T20";
      if (initialSheet) { setSelection({ sheetName: initialSheet, range: initialRange, evidence }); setRangeInput(initialRange); }
      else setLoading(false);
    }).catch((caught) => {
      if (!controller.signal.aborted) { setError(errorMessage(caught)); setLoading(false); }
    });
    return () => controller.abort();
  }, [archiveId, attempt, locator, projectId]);

  useEffect(() => {
    if (!selection || !sheets) return;
    const controller = new AbortController();
    setLoading(true); setError(null); setPreview(null); setSelectedCoordinate(null);
    void fetchWorkbookCellPreview({ projectId, archiveId, sheetName: selection.sheetName, cellReference: selection.range,
      contextRows: selection.evidence ? 3 : 0, contextColumns: selection.evidence ? 3 : 0, signal: controller.signal,
    }).then((result) => {
      if (controller.signal.aborted) return;
      if (result.archiveId !== archiveId || result.sourceSha256 !== sheets.sourceSha256
        || result.archiveIntegrityStatus !== "verified" || result.analysisOnly !== true || result.approvalEligible !== false
        || result.sheetName !== selection.sheetName || result.formulaPolicy !== "display_only_never_executed"
        || (sheets.archiveVersionId && result.archiveVersionId !== sheets.archiveVersionId)
        || (sheets.archiveETag && result.archiveETag !== sheets.archiveETag)
        || !Array.isArray(result.rows) || !Array.isArray(result.columns)
        || result.rows.reduce((total, row) => total + (Array.isArray(row.cells) ? row.cells.length : 401), 0) > sheets.limits.maxCells) {
        throw new Error("선택한 원본과 셀 응답의 버전·무결성을 확인할 수 없습니다.");
      }
      setPreview(result);
    }).catch((caught) => { if (!controller.signal.aborted) setError(errorMessage(caught)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [archiveId, projectId, selection, sheets]);

  const activeSheet = sheets?.sheets.find((sheet) => sheet.sheetName === selection?.sheetName);
  const bounds = selection ? rangeBounds(selection.range) : null;
  const selectedCell = preview?.rows.flatMap((row) => row.cells).find((cell) => cell.coordinate === selectedCoordinate);
  function showRange(range: string) {
    const requested = rangeBounds(range);
    if (!sheets || !selection || !requested || requested.lastRow > sheets.limits.maxRows || requested.lastColumn > sheets.limits.maxColumns
      || (requested.lastRow - requested.firstRow + 1) * (requested.lastColumn - requested.firstColumn + 1) > sheets.limits.maxCells) {
      setRangeError(`A1 형식으로 최대 ${sheets?.limits.maxCells ?? 400}셀을 선택해 주세요. 예: A1:T20`); return;
    }
    const normalized = boundsRange(requested);
    setRangeError(null); setRangeInput(normalized); setSelection({ ...selection, range: normalized, evidence: false });
  }
  function moveWindow(rowStep: number, columnStep: number) {
    if (!bounds || !sheets) return;
    const height = bounds.lastRow - bounds.firstRow + 1; const width = bounds.lastColumn - bounds.firstColumn + 1;
    const lastAllowedRow = Math.max(height, Math.min(sheets.limits.maxRows, activeSheet?.rowCount || sheets.limits.maxRows));
    const lastAllowedColumn = Math.max(width, Math.min(sheets.limits.maxColumns, activeSheet?.columnCount || sheets.limits.maxColumns));
    const firstRow = Math.max(1, Math.min(lastAllowedRow - height + 1, bounds.firstRow + rowStep * height));
    const firstColumn = Math.max(1, Math.min(lastAllowedColumn - width + 1, bounds.firstColumn + columnStep * width));
    showRange(boundsRange({ firstRow, firstColumn, lastRow: firstRow + height - 1, lastColumn: firstColumn + width - 1 }));
  }

  return <section aria-label="Excel 원본 미리보기" className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-950">
    <div className="space-y-3 border-b border-slate-200 p-4 dark:border-slate-800">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div><h2 className="flex items-center gap-2 text-sm font-bold text-slate-950 dark:text-white"><FileSpreadsheet className="h-4 w-4" /> Excel 원본 읽기</h2>
          <p className="mt-1 text-xs leading-5 text-slate-500">시트와 셀을 직접 확인하세요. 수식은 실행하지 않으며, 저장된 결과와 입력값을 구분합니다. 숫자는 정수로 표시합니다.</p></div>
        <button type="button" onClick={() => setAttempt((value) => value + 1)} disabled={loading} className="inline-flex min-h-9 items-center gap-1 rounded-lg border px-3 text-xs font-semibold disabled:opacity-40"><RefreshCw className="h-3 w-3" /> 원본 다시 읽기</button>
      </div>
      {sheets && selection ? <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-36 flex-1 text-xs font-semibold text-slate-600 dark:text-slate-300">시트 선택<select aria-label="Excel 시트 선택" value={selection.sheetName} disabled={loading} className="mt-1 block min-h-10 w-full rounded-lg border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-950" onChange={(event) => { const sheet = sheets.sheets.find((item) => item.sheetName === event.target.value);
            const range = `A1:${columnLabel(Math.max(1, Math.min(20, sheet?.columnCount || 20)))}${Math.max(1, Math.min(20, sheet?.rowCount || 20))}`;
            setSelection({ sheetName: event.target.value, range, evidence: false }); setRangeInput(range); setRangeError(null); }}>
          {sheets.sheets.map((sheet) => <option key={sheet.sheetIndex} value={sheet.sheetName}>{sheet.sheetName}{sheet.state === "visible" ? "" : " (숨김 시트)"}</option>)}</select></label>
        <form className="flex items-end gap-2" onSubmit={(event) => { event.preventDefault(); showRange(rangeInput); }}>
          <label className="text-xs font-semibold text-slate-600 dark:text-slate-300">셀 범위<input aria-label="Excel 셀 범위" value={rangeInput} onChange={(event) => setRangeInput(event.target.value)} className="mt-1 block min-h-10 w-32 rounded-lg border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-950" /></label>
          <button type="submit" disabled={loading} className="min-h-10 rounded-lg bg-blue-700 px-3 text-xs font-semibold text-white disabled:opacity-40">범위 보기</button>
        </form>
      </div> : null}
      {rangeError ? <p role="alert" className="text-xs text-rose-700 dark:text-rose-300">{rangeError}</p> : null}
      {activeSheet ? <p className="text-xs text-slate-500">원본 시트 {activeSheet.declaredRowCount === null ? "행 수 미확인" : `${activeSheet.declaredRowCount.toLocaleString("ko-KR")}행`} × {activeSheet.declaredColumnCount === null ? "열 수 미확인" : `${activeSheet.declaredColumnCount.toLocaleString("ko-KR")}열`}{activeSheet.dimensionsTruncated ? " · 일부 범위만 탐색 가능" : ""}. 한 번에 최대 {sheets?.limits.maxCells}셀을 표시합니다.</p> : null}
    </div>
    {loading ? <div role="status" className="flex min-h-52 items-center justify-center text-sm text-slate-500"><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Excel 시트와 셀을 읽고 있습니다.</div>
      : error ? <p role="alert" className="m-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800 dark:bg-rose-950/30 dark:text-rose-200">{error} 원본 다운로드로도 확인할 수 있습니다.</p>
      : preview ? <>
        {!preview.rows.some((row) => row.cells.some((cell) => cell.formula || (cell.displayValue !== null && cell.displayValue !== undefined && cell.displayValue !== ""))) ? <p role="status" className="border-b border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">이 범위에 저장된 값이 없습니다. 다른 시트나 셀 범위를 선택해 주세요. 전체 원본이 비어 있다는 뜻은 아닙니다.</p> : null}
        <div className="max-h-[560px] overflow-auto bg-slate-100 p-3 dark:bg-slate-900">
          <table className="min-w-full border-separate border-spacing-0 text-xs" aria-label={`${preview.sheetName} ${preview.previewRange}`}><thead><tr>
            <th scope="col" className="sticky left-0 top-0 z-20 border border-slate-300 bg-slate-200 px-2 py-2 dark:border-slate-700 dark:bg-slate-800">행</th>
            {preview.columns.map((column) => <th scope="col" key={column.columnNumber} className="sticky top-0 z-10 min-w-24 border border-l-0 border-slate-300 bg-slate-200 px-2 py-2 font-bold dark:border-slate-700 dark:bg-slate-800">{column.label}</th>)}
          </tr></thead><tbody>{preview.rows.map((row) => <tr key={row.rowNumber}>
            <th scope="row" className="sticky left-0 z-10 border border-t-0 border-slate-300 bg-slate-200 px-2 py-2 dark:border-slate-700 dark:bg-slate-800">{row.rowNumber}</th>
            {row.cells.map((cell) => <td key={cell.coordinate} className={`max-w-64 border border-l-0 border-t-0 border-slate-300 px-2 py-2 align-top dark:border-slate-700 ${selection?.evidence && cell.focused ? "bg-amber-100 ring-2 ring-inset ring-amber-500 dark:bg-amber-950/40" : "bg-white dark:bg-slate-950"}`}>
              <button type="button" onClick={() => setSelectedCoordinate(cell.coordinate)} aria-label={`${cell.coordinate} 셀 상세`} className="block min-h-7 w-full text-left focus:outline-none focus:ring-2 focus:ring-blue-500">
                <span className={`block whitespace-pre-wrap break-words font-medium ${cell.formula ? "text-indigo-700 dark:text-indigo-300" : "text-slate-800 dark:text-slate-200"}`}>{cellValue(cell.displayValue, cell.numberFormat)}</span>
                {cell.formula ? <span className={`mt-1 block text-[10px] ${cell.valueState?.includes("missing") || cell.valueState?.includes("error") ? "text-amber-700 dark:text-amber-300" : "text-slate-500"}`}>{cellState(cell)}</span> : null}
              </button>
            </td>)}
          </tr>)}</tbody></table>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-4 py-3 text-xs dark:border-slate-800">
          <span>표시 범위 {preview.sheetName}!{preview.previewRange}{selection?.evidence ? " · 선택한 근거 위치 강조" : ""}</span>
          <div className="flex flex-wrap gap-1">
            <button type="button" aria-label="이전 Excel 열" disabled={loading || !bounds || bounds.firstColumn <= 1} onClick={() => moveWindow(0, -1)} className="min-h-9 rounded border px-2 disabled:opacity-40">← 열</button>
            <button type="button" aria-label="다음 Excel 열" disabled={loading || !bounds || !sheets || bounds.lastColumn >= Math.min(sheets.limits.maxColumns, activeSheet?.columnCount || sheets.limits.maxColumns)} onClick={() => moveWindow(0, 1)} className="min-h-9 rounded border px-2 disabled:opacity-40">열 →</button>
            <button type="button" aria-label="이전 Excel 행" disabled={loading || !bounds || bounds.firstRow <= 1} onClick={() => moveWindow(-1, 0)} className="min-h-9 rounded border px-2 disabled:opacity-40">↑ 행</button>
            <button type="button" aria-label="다음 Excel 행" disabled={loading || !bounds || !sheets || bounds.lastRow >= Math.min(sheets.limits.maxRows, activeSheet?.rowCount || sheets.limits.maxRows)} onClick={() => moveWindow(1, 0)} className="min-h-9 rounded border px-2 disabled:opacity-40">행 ↓</button>
          </div>
        </div>
        {selectedCell ? <div className="space-y-2 border-t border-slate-200 bg-slate-50 p-4 text-xs dark:border-slate-800 dark:bg-slate-900" aria-label="선택한 Excel 셀 상세">
          <p className="font-bold">{preview.sheetName}!{selectedCell.coordinate} · {cellState(selectedCell)}</p>
          <p>표시 값 {cellValue(selectedCell.displayValue, selectedCell.numberFormat)}</p>
          {selectedCell.formula ? <><code className="block break-all text-indigo-700 dark:text-indigo-300">{selectedCell.formula}</code><p>원본 저장 결과 {cellValue(selectedCell.cachedValue, selectedCell.numberFormat)} · 재계산하지 않음</p></> : null}
          <p className="text-slate-500">원본 형식 {selectedCell.numberFormat || "일반"}</p>
          {selection?.evidence && selectedCell.focused && locator ? <button type="button" onClick={() => onSelectSourceLocator(locator)} className="min-h-9 rounded border border-indigo-200 px-3 font-semibold text-indigo-700">이 셀과 연결된 파싱값 보기</button> : <p className="text-slate-500">원본 열람용 셀입니다. 검토·승인된 입력값으로 자동 저장되지 않습니다.</p>}
        </div> : null}
      </> : <p className="p-4 text-sm text-slate-600 dark:text-slate-300">열람 가능한 시트가 없습니다. 원본 다운로드로 내용을 확인해 주세요.</p>}
    {sheets ? <details className="border-t border-slate-200 px-4 py-3 text-xs text-slate-500 dark:border-slate-800"><summary className="cursor-pointer">원본 출처와 표시 범위</summary>
      <p className="mt-2 break-words">{sheets.fileName}</p><p className="mt-1 break-all font-mono">SHA-256 {sheets.sourceSha256}</p>
      {sheets.archiveVersionId ? <p className="mt-1 break-all">보관 버전 {sheets.archiveVersionId}</p> : null}
      <p className="mt-2">원본 무결성 확인 완료. 입력값·수식·저장 결과를 그대로 읽으며, 계산과 승인 기록은 별도로 관리합니다.</p>
      {sheets.truncated || sheets.warnings.length ? <p className="mt-2 text-amber-700 dark:text-amber-300">시트 목록·원본 탐색 범위가 제한될 수 있습니다. 첫 범위가 비어 있으면 다른 시트나 셀 범위를 선택하세요.</p> : null}
    </details> : null}
  </section>;
}
