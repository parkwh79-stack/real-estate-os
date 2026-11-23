import { displayText } from "../../lib/workspace-context";

export interface PermitHeightAnalysis {
  lawSources?: Array<{ name?: string; url?: string; article?: string; sourceType?: string }>;
  permitRouteReview?: {
    useClassification?: string;
    routes?: Array<{ code?: string; label?: string; lawBasis?: string }>;
    requiredChecks?: string[];
    plannedDate?: string | number | null;
    approvalEvidenceStatus?: string;
    approvalDocumentLocators?: Array<{ route?: string; sourceDocumentId?: string; pageNumber?: number; sheetName?: string; cell?: string }>;
  };
  heightReview?: {
    jurisdictionCandidate?: string | null;
    plannedHeightM?: number | null;
    candidateLimitM?: number | null;
    confirmedLimitM?: number | null;
    findings?: string[];
    parcelReviews?: Array<{
      parcelIndex?: number;
      parcelLabel?: string;
      pnu?: string;
      zoning?: string;
      districtUnitPlan?: string;
      designations?: string[];
      sourceLocator?: { sourceType?: string; sourceUrl?: string; apiRowId?: string; sourceName?: string };
    }>;
  };
}

function meters(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "미확인";
  const prefix = Number.isInteger(value) ? "" : "약 ";
  return `${prefix}${value.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}m`;
}

const useLabels: Record<string, string> = {
  housing: "주택",
  officetel: "오피스텔",
  mixed_use: "복합용도",
  non_housing: "비주택",
};

export function PermitHeightReview({ analysis }: { analysis?: PermitHeightAnalysis }) {
  const permit = analysis?.permitRouteReview;
  const height = analysis?.heightReview;
  const citations = analysis?.lawSources?.filter((source) => source.sourceType && source.url && /^https?:\/\//i.test(source.url));
  if (!permit && !height) return null;

  return (
    <div className="space-y-3 text-sm text-slate-700 dark:text-slate-200">
      <div className="grid gap-3 md:grid-cols-2">
        {permit ? (
          <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
            <h3 className="font-black text-slate-950 dark:text-white">인허가 경로</h3>
            <p className="mt-1">계획 용도 · {useLabels[permit.useClassification ?? ""] ?? "미확인"}</p>
            {permit.routes?.length ? (
              <ul className="mt-2 space-y-1">
                {permit.routes.map((route, index) => (
                  <li key={route.code ?? index} className="font-semibold">
                    {route.label}{route.lawBasis ? <span className="ml-2 font-normal text-slate-500">{route.lawBasis}</span> : null}
                  </li>
                ))}
              </ul>
            ) : null}
            <p className="mt-2">계획 일정 · {typeof permit.plannedDate === "number" ? `사업 ${permit.plannedDate}개월차` : displayText(permit.plannedDate, "미확인")}</p>
            <p className="mt-1 font-semibold text-amber-700 dark:text-amber-300">
              {permit.approvalEvidenceStatus === "source_review_required" ? "승인 문서의 효력과 대상 필지 검토 필요" : "발급된 승인·허가 문서 미확인"}
            </p>
            {permit.approvalDocumentLocators?.map((item, index) => (
              <p key={index} className="mt-1 text-xs text-slate-500">
                {permit.routes?.find((route) => route.code === item.route)?.label ?? "인허가 문서"} 근거 · {item.pageNumber ? `${item.pageNumber}쪽` : [item.sheetName, item.cell].filter(Boolean).join(" · ")}
              </p>
            ))}
            {permit.requiredChecks?.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">{permit.requiredChecks.map((check) => <li key={check}>{check}</li>)}</ul> : null}
          </div>
        ) : null}
        {height ? (
          <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
            <h3 className="font-black text-slate-950 dark:text-white">최고 허용 높이</h3>
            <dl className="mt-1 space-y-1">
              <div className="flex justify-between gap-3"><dt>조례 검색 관할 후보</dt><dd>{displayText(height.jurisdictionCandidate, "미확인")}</dd></div>
              <div className="flex justify-between gap-3"><dt>확인된 허용 높이</dt><dd className="font-semibold">{meters(height.confirmedLimitM)}</dd></div>
              <div className="flex justify-between gap-3"><dt>계획 높이</dt><dd>{meters(height.plannedHeightM)}</dd></div>
              {height.candidateLimitM != null ? <div className="flex justify-between gap-3"><dt>검증 대기 높이 기준</dt><dd>{meters(height.candidateLimitM)}</dd></div> : null}
            </dl>
            {height.findings?.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">{height.findings.map((finding) => <li key={finding}>{finding}</li>)}</ul> : null}
          </div>
        ) : null}
      </div>
      {height?.parcelReviews?.length ? (
        <details className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
          <summary className="cursor-pointer font-bold">필지별 지구단위계획 근거 {height.parcelReviews.length}건</summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-slate-500"><tr><th className="p-2">필지</th><th className="p-2">PNU</th><th className="p-2">용도지역</th><th className="p-2">지구단위계획</th><th className="p-2">토지이용 지정</th><th className="p-2">출처</th></tr></thead>
              <tbody>
                {height.parcelReviews.map((parcel, index) => {
                  const url = parcel.sourceLocator?.sourceUrl;
                  const source = parcel.sourceLocator?.sourceName ?? "토지이용계획 근거";
                  return (
                    <tr key={parcel.parcelIndex ?? index} className="border-t border-slate-200 dark:border-slate-700">
                      <th scope="row" className="p-2">{displayText(parcel.parcelLabel, `필지 ${parcel.parcelIndex ?? index + 1}`)}</th>
                      <td className="p-2">{displayText(parcel.pnu, "미확인")}</td>
                      <td className="p-2">{displayText(parcel.zoning, "미확인")}</td>
                      <td className="p-2">{displayText(parcel.districtUnitPlan, "미확인")}</td>
                      <td className="p-2">{parcel.designations?.join(" · ") || "미확인"}</td>
                      <td className="p-2">{url && /^https?:\/\//i.test(url) ? <a href={url} target="_blank" rel="noreferrer" className="font-semibold text-indigo-700 underline dark:text-indigo-300">{source}</a> : parcel.sourceLocator?.apiRowId ? source : "미확인"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
      {citations?.length ? (
        <details className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
          <summary className="cursor-pointer font-bold">조회된 법령·조례 근거</summary>
          <ul className="mt-2 space-y-1 text-xs">
            {citations.map((source, index) => (
              <li key={index}><a href={source.url} target="_blank" rel="noreferrer" className="font-semibold text-indigo-700 underline dark:text-indigo-300">{displayText(source.name, "법령 원문")} {source.article}</a></li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
