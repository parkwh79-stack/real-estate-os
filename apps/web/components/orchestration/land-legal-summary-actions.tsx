"use client";

import { useState } from "react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { displayText } from "../../lib/workspace-context";
import { PermitHeightReview, type PermitHeightAnalysis } from "./permit-height-review";

type SummaryRecord = {
  recordId: string;
  updatedAt?: string;
  summary: {
    permit?: PermitHeightAnalysis["permitRouteReview"];
    height?: PermitHeightAnalysis["heightReview"];
    parcels?: NonNullable<PermitHeightAnalysis["heightReview"]>["parcelReviews"];
  };
};

export function LandLegalSummaryActions({ projectId, previewHash, analysis }: {
  projectId: string;
  previewHash?: string;
  analysis: PermitHeightAnalysis;
}) {
  const [permit, setPermit] = useState(false);
  const [height, setHeight] = useState(false);
  const [parcelIndexes, setParcelIndexes] = useState<number[]>([]);
  const [saved, setSaved] = useState<SummaryRecord[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const endpoint = `${apiBase}/projects/${projectId}/legal-pod/preview-summary`;

  async function request(save: boolean) {
    setBusy(true);
    setError("");
    try {
      const sections = [permit && "permit", height && "height", parcelIndexes.length > 0 && "parcels"].filter(Boolean);
      const response = await fetch(endpoint, {
        method: save ? "POST" : "GET",
        cache: "no-store",
        headers: save ? { ...authHeaders("web"), "Content-Type": "application/json" } : authHeaders("web"),
        body: save ? JSON.stringify({ sections, parcelIndexes, expectedPreviewHash: previewHash }) : undefined,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "요약을 처리하지 못했습니다.");
      if (save) {
        const record = result as SummaryRecord;
        setSaved((previous) => [record, ...(previous ?? []).filter((item) => item.recordId !== record.recordId)]);
      } else {
        setSaved(Array.isArray(result.items) ? result.items : []);
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "요약을 처리하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  const actionClass = "rounded-md border border-slate-300 px-3 py-2 text-xs font-bold text-slate-800 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200";
  return (
    <div className="mt-4 space-y-3 border-t border-slate-200 pt-3 dark:border-slate-700">
      {previewHash ? (
        <fieldset className="space-y-2 text-sm text-slate-700 dark:text-slate-200">
          <legend className="font-bold">보관할 항목 선택</legend>
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2"><input type="checkbox" checked={permit} onChange={(event) => setPermit(event.target.checked)} /> 인허가 경로</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={height} onChange={(event) => setHeight(event.target.checked)} /> 높이 요약</label>
          </div>
          {analysis.heightReview?.parcelReviews?.length ? (
            <details>
              <summary className="cursor-pointer">보관할 필지 선택</summary>
              <div className="mt-2 flex max-h-40 flex-wrap gap-3 overflow-auto">
                {analysis.heightReview.parcelReviews.map(({ parcelIndex, parcelLabel }) => parcelIndex ? (
                  <label key={parcelIndex} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={parcelIndexes.includes(parcelIndex)}
                      disabled={parcelIndexes.length >= 50 && !parcelIndexes.includes(parcelIndex)}
                      onChange={(event) => setParcelIndexes(event.target.checked ? [...parcelIndexes, parcelIndex] : parcelIndexes.filter((index) => index !== parcelIndex))}
                    />
                    {displayText(parcelLabel, `필지 ${parcelIndex}`)}
                  </label>
                ) : null)}
              </div>
            </details>
          ) : null}
          <button type="button" className={actionClass} onClick={() => void request(true)} disabled={busy || (!permit && !height && !parcelIndexes.length)}>선택 항목만 저장</button>
        </fieldset>
      ) : null}
      <button type="button" className={actionClass} onClick={() => void request(false)} disabled={busy}>저장한 요약 보기</button>
      {error ? <p role="alert" className="text-sm text-rose-700 dark:text-rose-300">{error}</p> : null}
      {saved?.length === 0 ? <p className="text-sm text-slate-500">저장한 요약이 없습니다.</p> : null}
      {saved?.map((item, index) => (
        <details key={item.recordId} className="rounded-md border border-slate-200 p-3 dark:border-slate-700">
          <summary className="cursor-pointer text-sm font-bold">저장한 예비 검토 {item.updatedAt ? new Date(item.updatedAt).toLocaleString("ko-KR") : index + 1}</summary>
          <div className="mt-2">
            <PermitHeightReview analysis={{
              permitRouteReview: item.summary.permit,
              heightReview: item.summary.height ? { ...item.summary.height, parcelReviews: item.summary.parcels } : undefined,
            }} />
            {!item.summary.height && item.summary.parcels?.length ? (
              <ul className="list-disc space-y-1 pl-5 text-sm">{item.summary.parcels.map((parcel) => (
                <li key={parcel.parcelIndex}>{displayText(parcel.parcelLabel, `필지 ${parcel.parcelIndex}`)} · PNU {displayText(parcel.pnu, "미확인")} · {displayText(parcel.zoning, "용도지역 미확인")} · {displayText(parcel.districtUnitPlan, "지구단위계획 미확인")}</li>
              ))}</ul>
            ) : null}
          </div>
        </details>
      ))}
    </div>
  );
}
