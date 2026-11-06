"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { backendErrorMessage, parseBackendErrorResponse } from "../../lib/backend-error";
import { locatorSummary, type DealStateRecord } from "../../lib/deal-state-records";
import { displayText } from "../../lib/workspace-context";

function savedComment(review: Record<string, unknown>) {
  return typeof review.comment === "string" ? review.comment
    : typeof review.reviewComment === "string" ? review.reviewComment : "";
}

export function RiskSupplementReview({ projectId, record, onSaved, onBusyChange, onClose }: {
  projectId: string; record: DealStateRecord;
  onSaved: (record: DealStateRecord) => void;
  onBusyChange: (busy: boolean) => void;
  onClose: () => void;
}) {
  const [comment, setComment] = useState(savedComment(record));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const active = useRef(false);
  const inFlight = useRef(false);
  const editor = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    active.current = true;
    editor.current?.focus();
    return () => { active.current = false; onBusyChange(false); };
  }, [onBusyChange]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || !record.recordId) return;
    const trimmed = comment.trim();
    if (!trimmed) { setError("보완이 필요한 이유와 다음 할 일을 입력해 주세요."); return; }
    inFlight.current = true; setBusy(true); onBusyChange(true); setError(null); setNotice(null);
    try {
      const response = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/deal-state-records/${encodeURIComponent(record.recordId)}/review`, {
        method: "PATCH", headers: { "Content-Type": "application/json", ...authHeaders("risk-reviewer") },
        body: JSON.stringify({ reviewStatus: "needs_revision", comment: trimmed }),
      });
      if (!response.ok) throw new Error(backendErrorMessage(await parseBackendErrorResponse(response), { action: "보완요청 저장" }));
      const result = await response.json() as { projectId?: string; record?: DealStateRecord };
      if (!active.current) return;
      if (result.projectId !== projectId || result.record?.recordId !== record.recordId || result.record.reviewStatus !== "needs_revision") {
        throw new Error("저장 응답의 위험 기록을 확인하지 못했습니다. 입력을 유지한 채 다시 확인해 주세요.");
      }
      onSaved(result.record);
      setComment(savedComment(result.record) || trimmed);
      setNotice("보완요청을 저장했습니다. 이 위험을 다시 열면 내용과 검토 이력을 확인할 수 있습니다.");
    } catch (failure) {
      if (active.current) setError(failure instanceof Error ? failure.message : "보완요청을 저장하지 못했습니다.");
    } finally {
      inFlight.current = false;
      if (active.current) { setBusy(false); onBusyChange(false); }
    }
  }

  const history = Array.isArray(record.reviewHistory) ? record.reviewHistory.filter((entry): entry is Record<string, unknown> =>
    Boolean(entry) && typeof entry === "object" && !Array.isArray(entry)) : [];

  return <section aria-label="보완요청 작성" className="min-w-0 rounded-lg border border-indigo-200 bg-white p-5 dark:border-indigo-800 dark:bg-slate-900">
    <h2 className="text-lg font-black">보완요청 작성</h2>
    <p className="mt-2 break-words text-sm font-bold">{displayText(record.title ?? record.item ?? record.riskCode ?? record.key, "선택한 위험")}</p>
    <p className="mt-1 break-words text-xs text-slate-500">근거 위치: {locatorSummary(record)}</p>
    <form onSubmit={(event) => void save(event)} className="mt-4 space-y-3">
      <label className="block space-y-2 text-sm font-bold">
        <span>보완 사유·다음 할 일</span>
        <textarea ref={editor} value={comment} onChange={(event) => setComment(event.target.value)} disabled={busy} rows={5}
          aria-describedby="risk-supplement-help" className="w-full rounded-lg border border-slate-300 bg-transparent p-3 font-normal disabled:opacity-60 dark:border-slate-700" />
      </label>
      <p id="risk-supplement-help" className="text-xs leading-5 text-slate-500">필요한 자료와 보완이 필요한 이유, 다음 할 일을 함께 적어주세요. 이 요청은 선택한 위험의 검토 기록에 저장되며 승인이나 해결 완료를 뜻하지 않습니다.</p>
      <p className="text-xs leading-5 text-slate-500">다른 검토는 이 입력란을 닫은 뒤 진행하세요. 저장하지 않은 내용은 닫으면 사라집니다.</p>
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy || !record.recordId} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{busy ? "저장 중…" : "보완요청 저장"}</button>
        <button type="button" disabled={busy} onClick={onClose} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-bold disabled:opacity-50 dark:border-slate-700">닫기</button>
      </div>
    </form>
    {error ? <p role="alert" className="mt-3 text-sm font-bold text-rose-600">{error}</p> : null}
    {notice ? <p role="status" className="mt-3 text-sm font-bold text-emerald-700">{notice}</p> : null}
    <details className="mt-4 text-sm">
      <summary className="cursor-pointer font-bold">저장된 검토 이력</summary>
      {history.length ? <ol className="mt-3 space-y-3">{history.map((entry, index) => <li key={index} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800">
        <p className="break-words text-xs text-slate-500">{displayText(entry.reviewStatus)} · {displayText(entry.reviewedBy)} · {displayText(entry.reviewedAt)}</p>
        <p className="mt-2 whitespace-pre-wrap break-words">{displayText(savedComment(entry), "검토 내용 미기록")}</p>
      </li>)}</ol> : <p className="mt-2 text-slate-500">저장된 검토 이력이 없습니다.</p>}
    </details>
  </section>;
}
