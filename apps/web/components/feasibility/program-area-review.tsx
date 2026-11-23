"use client";

import { useState } from "react";
import Link from "next/link";
import { apiBase, authHeaders } from "../../lib/auth-client";
import type { AreaDrawing, ProgramAreaBasis } from "../../lib/program-area-basis";

const format = (value: number | null | undefined, unit: string) =>
  value == null ? "미확인" : value.toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + unit;
const control = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-950";

export function DrawingAreaForm({ projectId, disabled, onSave }: {
  projectId: string; disabled: boolean; onSave: (drawing: AreaDrawing) => Promise<void>;
}) {
  const [documents, setDocuments] = useState<{ archiveId: string; fileName: string }[]>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [area, setArea] = useState("");
  const [archiveId, setArchiveId] = useState("");
  const [revision, setRevision] = useState("");
  const [location, setLocation] = useState("");

  async function listDocuments() {
    if (documents || loading) return;
    setLoading(true);
    setError("");
    try {
      const response = await fetch(apiBase + "/projects/" + encodeURIComponent(projectId) + "/statutory-cost-review", {
        headers: authHeaders("web"), cache: "no-store",
      });
      if (!response.ok) throw new Error("보관 문서 목록을 조회하지 못했습니다.");
      const data = await response.json();
      setDocuments(Array.isArray(data.documents) ? data.documents : []);
    } catch {
      setError("보관 문서 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  return <details className="rounded-lg border border-slate-200 bg-white p-4 text-sm"
    onToggle={(event) => { if (event.currentTarget.open) void listDocuments(); }}>
    <summary className="cursor-pointer font-bold">도면으로 시공 연면적 지정</summary>
    <p className="mt-2 text-slate-600">보관 도면의 시공 연면적을 확인해 입력하세요. 면적 기반 공사비 추정에 사용합니다.</p>
    {error ? <div className="mt-2 text-rose-700" role="alert">{error} <button type="button" onClick={() => void listDocuments()} className="underline">다시 조회</button></div> : null}
    {documents?.length === 0 ? <p className="mt-2 text-slate-600">프로젝트 자료 입력에서 도면을 먼저 보관하세요.</p> : null}
    <form className="mt-3" onSubmit={(event) => {
      event.preventDefault();
      if (disabled || loading) return;
      void onSave({ grossFloorAreaSqm: Number(area), archiveId, revision, location });
    }}>
      <fieldset disabled={disabled || loading || !documents?.length} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <label>보관 도면
          <select className={control} value={archiveId} onChange={(event) => setArchiveId(event.target.value)} required>
            <option value="">문서 선택</option>
            {documents?.map((file) => <option key={file.archiveId} value={file.archiveId}>{file.fileName}</option>)}
          </select>
        </label>
        <label>시공 연면적(㎡)<input className={control} type="number" min="0.000001" max="1000000000" step="any" required value={area} onChange={(event) => setArea(event.target.value)} /></label>
        <label>도면 판본<input className={control} value={revision} required maxLength={120} onChange={(event) => setRevision(event.target.value)} /></label>
        <label>도면 위치<input className={control} value={location} required maxLength={200} onChange={(event) => setLocation(event.target.value)} /></label>
        <button type="submit" className="w-fit rounded-lg bg-slate-900 px-4 py-2 font-bold text-white disabled:opacity-50">
          도면 근거로 사업수지 구성
        </button>
      </fieldset>
    </form>
  </details>;
}

export function ProgramAreaBasisPanel({ basis, projectId, saved }: {
  basis: ProgramAreaBasis; projectId: string; saved: boolean;
}) {
  const { land, construction } = basis;
  if (!land && !construction) return null;
  const source = construction?.sourceLocator;
  return <details className="rounded-lg border border-slate-200 bg-white p-4 text-sm" open>
    <summary className="cursor-pointer font-bold">{saved ? "저장된 계산의 면적 근거" : "사업수지 구성의 면적 근거"}</summary>
    <dl className="mt-3 grid gap-3 sm:grid-cols-2">
      {land ? <div><dt className="font-semibold">대지면적과 토지비 추정</dt>
        <dd>{format(land.includedAreaSqm, "㎡")} · {format(land.amountKrw, "원")}</dd></div> : null}
      {construction ? <div><dt className="font-semibold">시공 연면적과 공사비 추정</dt>
        <dd>{format(construction.grossFloorAreaSqm, "㎡")} · {format(construction.amountKrw, "원")}</dd>
        {source?.archiveId ? <dd>
          <Link className="underline text-blue-700" href={"/project/" + encodeURIComponent(projectId) + "/documents/review?archiveId=" + encodeURIComponent(source.archiveId)}>
            {source.fileName ?? "보관 도면"}
          </Link>{" · "}{construction.revision} · {source.location}
        </dd> : construction.grossFloorAreaSqm ? <dd className="text-slate-600">기존 건축계획 입력 · 도면 판본 미연결</dd> : null}
      </div> : null}
    </dl>
    {land?.parcels?.length ? <div className="mt-3 overflow-x-auto">
      <table className="w-full min-w-[32rem] text-left">
        <thead><tr className="border-b border-slate-200"><th scope="col">필지 PNU</th><th scope="col">검토 범위</th><th scope="col">원장 면적</th><th scope="col">반영 면적</th><th scope="col">토지비 추정</th></tr></thead>
        <tbody>{land.parcels.map((row) => <tr key={row.parcelIndex} className="border-b border-slate-100">
          <td>{row.pnu ?? "미확인"}</td><td>{row.excluded ? "제외" : "원장 편입 후보"}</td>
          <td>{format(row.ledgerAreaSqm, "㎡")}</td>
          <td>{row.excluded ? "—" : format(row.includedAreaSqm, "㎡")}</td>
          <td>{row.excluded ? "—" : format(row.estimatedCostKrw, "원")}</td>
        </tr>)}</tbody>
      </table>
    </div> : null}
  </details>;
}
