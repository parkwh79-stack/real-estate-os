"use client";

import { useEffect, useId, useRef, useState } from "react";
import { FileText, Loader2, X } from "lucide-react";
import { fetchDocumentPageReading, generateDocumentPageReading, type DocumentPageReading } from "../../lib/document-page-reading";

export function PdfPageReadingPanel({ projectId, archiveId, pageNumber }: {
  projectId: string; archiveId: string; pageNumber: number;
}) {
  const panelId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const request = useRef<AbortController | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DocumentPageReading | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => () => request.current?.abort(), []);

  async function readPage(retry = false) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setOpen(true);
    setBusy(true);
    setResult(null);
    setError(null);
    const target = { projectId, archiveId, pageNumber, signal: controller.signal };
    try {
      const cached = retry ? null : await fetchDocumentPageReading(target);
      if (controller.signal.aborted) return;
      // This handler runs only after an explicit click. Mounting the viewer,
      // changing pages, or opening a source document never starts OCR.
      const page = cached ?? await generateDocumentPageReading(target);
      if (!controller.signal.aborted && request.current === controller) setResult(page);
    } catch (caught) {
      if (!controller.signal.aborted && request.current === controller) {
        setError(caught instanceof Error ? caught.message : "페이지 글자를 읽지 못했습니다. 다시 시도해 주세요.");
      }
    } finally {
      if (!controller.signal.aborted && request.current === controller) setBusy(false);
    }
  }

  function close() {
    request.current?.abort();
    request.current = null;
    setOpen(false);
    setBusy(false);
    setResult(null);
    setError(null);
    trigger.current?.focus();
  }

  return <section className="border-t border-slate-200 p-4 dark:border-slate-800" aria-label={`PDF ${pageNumber}페이지 글자 읽기`}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p className="text-sm font-bold text-slate-900 dark:text-slate-100">선택한 페이지의 글자 확인</p>
        <p className="mt-1 text-xs text-slate-500">원본과 대조할 수 있도록 PDF 원문 또는 이미지의 글자를 읽습니다.</p>
      </div>
      <button ref={trigger} type="button" aria-expanded={open} aria-controls={panelId}
        onClick={() => { if (open) close(); else void readPage(); }}
        className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-indigo-300 px-3 text-xs font-bold text-indigo-700 hover:bg-indigo-50 dark:border-indigo-700 dark:text-indigo-200 dark:hover:bg-indigo-950">
        <FileText className="h-4 w-4" /> {open ? "읽은 글자 접기" : "글자 읽기"}
      </button>
    </div>
    {open ? <div id={panelId} role="region" aria-label={`읽은 글자 · ${pageNumber}페이지`} className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-900"
      onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } }}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-bold text-slate-800 dark:text-slate-200">원본 {pageNumber}페이지 · 검토용 읽기 결과</p>
        <button type="button" onClick={close} aria-label="읽은 글자 닫기" className="-mr-2 -mt-2 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-200 dark:hover:bg-slate-800"><X className="h-4 w-4" /></button>
      </div>
      {busy ? <p role="status" className="mt-3 flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300"><Loader2 className="h-4 w-4 animate-spin" /> 선택한 페이지의 글자를 읽는 중입니다.</p> : null}
      {error ? <p role="alert" className="mt-3 text-sm text-rose-700 dark:text-rose-300">{error}</p> : null}
      {result ? <ReadingResult result={result} /> : null}
      {result?.stored === false ? <p role="status" className="mt-3 text-xs leading-5 text-amber-800 dark:text-amber-200">현재 읽기 결과는 저장되지 않았습니다. 다시 열면 글자를 다시 읽어야 할 수 있습니다.</p> : null}
      {!busy && (error || (result && result.status !== "completed")) ? <button type="button" onClick={() => void readPage(true)} className="mt-3 min-h-9 rounded-lg border border-slate-300 px-3 text-xs font-bold text-slate-700 hover:bg-white dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800">다시 읽기</button> : null}
      <p className="mt-3 text-xs leading-5 text-amber-800 dark:text-amber-200">원본 대조 검토가 필요합니다. 읽은 글자와 숫자는 계산·보고서에 자동 반영되지 않습니다.</p>
    </div> : null}
  </section>;
}

function ReadingResult({ result }: { result: DocumentPageReading }) {
  if (result.status === "completed") return <>
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      <span className="rounded bg-indigo-100 px-2 py-1 font-bold text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200">{result.sourceKind === "native" ? "PDF 원문" : "이미지 글자 인식(OCR)"}</span>
      <span className="font-bold text-amber-700 dark:text-amber-300">검토 필요</span>
    </div>
    <div className="mt-3 max-h-96 overflow-y-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-3 text-sm leading-6 text-slate-800 [overflow-wrap:anywhere] dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">{result.text}</div>
  </>;
  const message = result.status === "empty"
    ? "이 페이지에서 읽을 수 있는 글자를 찾지 못했습니다. 원본 이미지를 직접 확인해 주세요."
    : result.status === "unavailable"
      ? result.errorCode === "OCR_EXECUTOR_UNAVAILABLE"
        ? "서버의 이미지 글자 인식(OCR) 엔진을 사용할 수 없습니다. 원본 페이지를 직접 확인해 주세요."
        : result.errorCode === "OCR_DATA_UNAVAILABLE"
          ? "서버에 필요한 글자 인식 자료가 없어 OCR을 실행하지 못했습니다. 원본 페이지를 직접 확인해 주세요."
          : "서버의 글자 인식 기능을 사용할 수 없습니다. 원본 페이지를 직접 확인해 주세요."
      : result.status === "timeout"
        ? "이 페이지를 읽는 데 시간이 오래 걸려 중단했습니다. 원본을 확인하거나 다시 시도해 주세요."
        : "이 페이지의 글자를 읽지 못했습니다. 원본을 확인하거나 다시 시도해 주세요.";
  return <p role="status" className="mt-3 text-sm leading-6 text-slate-700 dark:text-slate-200">{message}</p>;
}
