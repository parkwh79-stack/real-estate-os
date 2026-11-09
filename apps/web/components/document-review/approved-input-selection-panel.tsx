"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ProjectWorkspaceLink } from "../layout/project-workspace-link";
import {
  applyParserV2ApprovedInputs, approveParserV2InputSnapshot, fetchParserInputApplicationPreview,
  ParserReviewSaveError, selectParserV2CanonicalInput,
  type DocumentReviewData, type ParsedFactCandidate, type ParserInputApplicationPreview,
} from "../../lib/document-review-workspace";

const LABELS: Record<string, string> = { "project.name": "사업명", "land.address": "사업지 주소", "land.area_sqm": "전체 대지면적" };
const PURPOSE = "project-site-inputs";
const id = (value: unknown) => String(value ?? "").trim();
const format = (value: unknown, unit?: string) => value == null ? "미기록"
  : typeof value === "number" ? `${Math.round(value).toLocaleString("ko-KR")}${unit === "sqm" ? " ㎡" : unit ? ` ${unit}` : ""}` : String(value);

function supported(candidate: ParsedFactCandidate) {
  const metadata = candidate.metadata as Record<string, unknown> | undefined;
  return Boolean(LABELS[id(candidate.canonicalField ?? candidate.fieldKey)]) && !candidate.entityId
    && !metadata?.referenceOnly && !metadata?.noFuturePfProjection
    && !candidate.validationCodes?.includes("FACT_BASIS_REQUIRED");
}

export function ApprovedInputSelectionPanel({ projectId, data, candidate, disabled, onRefresh, onBusyChange }: {
  projectId: string; data: DocumentReviewData; candidate?: ParsedFactCandidate; disabled: boolean;
  onRefresh: () => Promise<boolean>;
  onBusyChange: (busy: boolean) => void;
}) {
  const all = useMemo(() => data.packages.flatMap((item) => item.candidates), [data]);
  const selections = useMemo(() => [...new Map(data.packages.flatMap((item) => item.canonicalSelections)
    .map((item) => [id(item.selectionId), item])).values()], [data]);
  const snapshots = useMemo(() => [...new Map(data.packages.flatMap((item) => item.approvedInputSnapshots)
    .map((item) => [id(item.snapshotId), item])).values()], [data]);
  const decisions = candidate ? data.packages.flatMap((item) => item.reviewDecisions)
    .filter((item) => item.candidateId === candidate.candidateId)
    .sort((left, right) => Number(left.decisionSequence) - Number(right.decisionSequence)) : [];
  const decision = decisions.at(-1);
  const field = id(candidate?.canonicalField ?? candidate?.fieldKey);
  const candidateSelections = selections.filter((item) => id(item.canonicalField ?? item.fieldKey) === field && !item.entityId)
    .sort((left, right) => Number(left.selectionVersion) - Number(right.selectionVersion));
  const previousSelection = candidateSelections.at(-1);
  const alreadySelected = previousSelection?.validityStatus === "active" && previousSelection.candidateId === candidate?.candidateId
    && previousSelection.reviewDecisionId === decision?.reviewDecisionId;
  const approvedSelections = useMemo(() => selections.filter((item) => item.validityStatus === "active" && item.approvalStatus === "approved"
    && all.some((value) => value.candidateId === item.candidateId && supported(value))), [all, selections]);
  const latestSnapshot = snapshots.filter((item) => item.purpose === PURPOSE)
    .sort((left, right) => Number(left.snapshotVersion) - Number(right.snapshotVersion)).at(-1);
  const activeSnapshots = useMemo(() => snapshots.filter((item) => item.validityStatus === "active" && item.approvalStatus === "approved"
    && item.inputItems?.some((value) => LABELS[id(value.fieldKey)] && !value.entityId)), [snapshots]);
  const [selectionConsent, setSelectionConsent] = useState("");
  const [chosenSelections, setChosenSelections] = useState<string[]>([]);
  const [snapshotConsent, setSnapshotConsent] = useState(false);
  const [chosenSnapshot, setChosenSnapshot] = useState("");
  const [preview, setPreview] = useState<ParserInputApplicationPreview | null>(null);
  const [chosenFields, setChosenFields] = useState<string[]>([]);
  const [applicationConsent, setApplicationConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<{ kind: "selection" | "snapshot" | "application"; id: string; before: DocumentReviewData; allowMissing?: boolean } | null>(null);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;
  const locked = disabled || busy || Boolean(pending);
  const snapshotId = chosenSnapshot || id(activeSnapshots.at(-1)?.snapshotId);

  useEffect(() => { onBusyChange(busy || Boolean(pending)); return () => onBusyChange(false); }, [busy, onBusyChange, pending]);

  useEffect(() => {
    if (chosenSelections.some((value) => !approvedSelections.some((item) => item.selectionId === value))) {
      setChosenSelections((values) => values.filter((value) => approvedSelections.some((item) => item.selectionId === value)));
      setSnapshotConsent(false);
    }
    if (chosenSnapshot && !activeSnapshots.some((item) => item.snapshotId === chosenSnapshot)) {
      setChosenSnapshot(""); setPreview(null); setChosenFields([]); setApplicationConsent(false);
    }
  }, [activeSnapshots, approvedSelections, chosenSelections, chosenSnapshot]);

  useEffect(() => {
    if (!pending || pending.kind === "application" || data === pending.before) return;
    const found = pending.kind === "selection" ? selections.some((item) => item.selectionId === pending.id)
      : snapshots.some((item) => item.snapshotId === pending.id);
    if (found) {
      setMessage(pending.kind === "selection" ? "선택한 값을 최신 원장에서 확인했습니다. 승인본에 포함할 값은 아래에서 직접 선택하세요."
        : "승인본 저장을 확인했습니다. 프로젝트 입력 반영은 현재 값과 비교한 뒤 별도로 실행하세요.");
      setError(null); setPending(null); setSnapshotConsent(false);
    } else if (pending.allowMissing) {
      setPending(null); setSelectionConsent(""); setSnapshotConsent(false);
      setMessage("충돌 이후 최신 기록을 확인했습니다. 변경된 값과 상태를 다시 비교한 뒤 선택하세요.");
    } else setError("최신 원장에서 저장 결과를 찾지 못했습니다. 다시 저장하지 말고 기록을 재확인해 주세요.");
  }, [data, pending, selections, snapshots]);

  async function save(kind: "selection" | "snapshot", mutationId: string, request: () => Promise<unknown>) {
    if (locked || busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(null); setMessage(null);
    try {
      await request();
      if (activeProject.current !== projectId) return;
      setPending({kind, id: mutationId, before: data});
      if (!await onRefresh()) setError("저장 응답은 받았지만 최신 원장을 불러오지 못했습니다. 저장 기록을 다시 확인해 주세요.");
    } catch (caught) {
      if (activeProject.current !== projectId) return;
      setError(caught instanceof Error ? caught.message : "승인 입력 저장에 실패했습니다.");
      if (caught instanceof ParserReviewSaveError && caught.requiresReload) setPending({kind, id: mutationId, before: data, allowMissing: caught.status === 409});
    } finally { busyRef.current = false; setBusy(false); }
  }

  async function compare() {
    if (!snapshotId || busyRef.current || locked) return;
    busyRef.current = true; setBusy(true); setError(null); setMessage(null); setPreview(null);
    try {
      const result = await fetchParserInputApplicationPreview(projectId, snapshotId);
      if (activeProject.current !== projectId) return;
      setPreview(result); setChosenFields([]); setApplicationConsent(false);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "현재 입력을 비교하지 못했습니다."); }
    finally { busyRef.current = false; setBusy(false); }
  }

  async function verifyApplication(applicationId: string, savedPreview: ParserInputApplicationPreview, allowMissing = false) {
    const current = await fetchParserInputApplicationPreview(projectId, savedPreview.snapshotId);
    if (activeProject.current !== projectId) return;
    if (allowMissing && current.lastApplication?.applicationId !== applicationId) {
      setPreview(current); setPending(null); setChosenFields([]); setApplicationConsent(false); setError(null);
      setMessage("갱신된 현재 입력을 확인했습니다. 반영할 값을 다시 선택해 주세요.");
      return;
    }
    if (current.lastApplication?.applicationId !== applicationId
      || chosenFields.some((key) => !current.items.some((item) => item.fieldKey === key && item.currentValue === item.approvedValue))) {
      throw new Error("현재 프로젝트에서 반영 결과를 확인하지 못했습니다. 입력을 다시 비교해 주세요.");
    }
    setPreview(current); setPending(null); setError(null); setMessage("선택한 승인 입력의 프로젝트 반영과 출처 기록을 다시 조회해 확인했습니다.");
    setApplicationConsent(false); await onRefresh();
    const name = current.items.find((item) => item.fieldKey === "project.name" && chosenFields.includes(item.fieldKey))?.currentValue;
    if (typeof name === "string") window.dispatchEvent(new CustomEvent("realytics-project-name-changed", { detail: { projectId, name } }));
  }

  async function apply() {
    if (!preview || !chosenFields.length || !applicationConsent || locked || busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(null); setMessage(null);
    const applicationId = `parser-apply-${crypto.randomUUID()}`;
    try {
      await applyParserV2ApprovedInputs(projectId, preview.snapshotId, {
        applicationId, fieldKeys: chosenFields, expectedSnapshotHash: preview.snapshotHash,
        expectedStateVersion: preview.expectedStateVersion, expectedPreviewHash: preview.previewHash,
      });
      setPending({kind: "application", id: applicationId, before: data});
      await verifyApplication(applicationId, preview);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "프로젝트 입력 반영에 실패했습니다.");
      if (!(caught instanceof ParserReviewSaveError) || caught.requiresReload) setPending({kind: "application", id: applicationId, before: data, allowMissing: caught instanceof ParserReviewSaveError && caught.status === 409});
    } finally { busyRef.current = false; setBusy(false); }
  }

  const available = candidate && supported(candidate);
  const blockedReason = !available ? "이 항목은 이번 기본 입력 적용 범위가 아닙니다. 사업명·사업지 주소·전체 대지면적만 지원하며 참조용 브리지는 제외합니다."
    : decision?.action !== "approve" ? "별도 사람 검토자의 승인 결정이 먼저 필요합니다."
      : candidate.conflictStatus === "open" || candidate.validationStatus === "blocked" ? "열린 충돌이나 차단 검증 항목을 먼저 확인하세요." : null;
  const buttonClass = "min-h-10 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-xs font-bold text-indigo-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-900 dark:bg-slate-950 dark:text-indigo-200";
  return <section aria-label="승인 입력 선택과 프로젝트 반영" className="mt-4 space-y-4 border-t border-slate-200 pt-4 dark:border-slate-800">
    <div><h3 className="text-sm font-black">승인 입력 선택과 반영</h3>
      <p className="mt-1 text-xs leading-5 text-slate-500">사람 검토 → 사용할 값 선택 → 승인본 저장 → 현재 입력과 비교 → 선택 반영 순서입니다.</p>
      <ProjectWorkspaceLink projectId={projectId} path="input-package" className="mt-2 inline-block text-xs font-bold text-indigo-600 underline">분석용 가정은 자료 입력에서 계속 검토</ProjectWorkspaceLink></div>
    {error ? <p role="alert" className="rounded-lg bg-rose-50 p-3 text-xs leading-5 text-rose-800">{error}</p> : null}
    {message ? <p role="status" className="rounded-lg bg-emerald-50 p-3 text-xs leading-5 text-emerald-800">{message}</p> : null}
    {pending ? <button type="button" className={buttonClass} disabled={busy} onClick={() => {
      if (pending.kind === "application" && preview) void verifyApplication(pending.id, preview, pending.allowMissing).catch((caught) => setError(String(caught.message)));
      else void onRefresh();
    }}>저장 기록 다시 확인</button> : null}
    <div className="space-y-2 rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-900">
      <p className="font-black">1. 선택한 후보를 입력값으로 지정</p>
      {candidate ? <p>{LABELS[field] || "지원 범위 밖 항목"} · {format(candidate.normalizedValue, candidate.unit)}</p> : null}
      {previousSelection ? <p>기존 선택값: {format(previousSelection.normalizedValue, previousSelection.unit)} · 버전 {previousSelection.selectionVersion}</p> : null}
      {candidate?.sourceLocator ? <p className="break-words text-slate-500">원본: {id(candidate.sourceLocator.sheetName) || id(candidate.documentVersionId)} {id(candidate.sourceLocator.cellReference) || id(candidate.sourceLocator.pageNumber)}</p> : null}
      {blockedReason ? <p className="leading-5 text-amber-800">{blockedReason}</p> : <>
        <label className="flex gap-2 leading-5"><input type="checkbox" checked={selectionConsent === candidate?.candidateId} disabled={locked || alreadySelected}
          onChange={(event) => setSelectionConsent(event.target.checked ? id(candidate?.candidateId) : "")} />값·단위·원본과 기존 선택값의 차이를 확인했습니다.</label>
        <button type="button" className={buttonClass} disabled={locked || alreadySelected || selectionConsent !== candidate?.candidateId}
          onClick={() => { if (!candidate || !decision) return; const selectionId = `web-selection-${crypto.randomUUID()}`;
            void save("selection", selectionId, () => selectParserV2CanonicalInput(projectId, { selectionId,
              candidateId: id(candidate.candidateId), reviewDecisionId: id(decision.reviewDecisionId), expectedDecisionSequence: decisions.length,
              selectionVersion: Number(previousSelection?.selectionVersion || 0) + 1, supersedesSelectionId: previousSelection ? id(previousSelection.selectionId) : undefined }));
          }}>{alreadySelected ? "입력값 선택됨" : "이 값을 승인 입력으로 선택"}</button>
      </>}
    </div>
    <div className="space-y-2 rounded-lg border border-slate-200 p-3 text-xs dark:border-slate-800">
      <p className="font-black">2. 승인본에 포함할 값 선택</p>
      {approvedSelections.length ? approvedSelections.map((selection) => <label key={id(selection.selectionId)} className="flex gap-2 leading-5">
        <input type="checkbox" checked={chosenSelections.includes(id(selection.selectionId))} disabled={locked} onChange={(event) => {
          setChosenSelections((values) => event.target.checked ? [...values, id(selection.selectionId)] : values.filter((value) => value !== selection.selectionId)); setSnapshotConsent(false);
        }} />{LABELS[id(selection.fieldKey ?? selection.canonicalField)]} · {format(selection.normalizedValue, selection.unit)}
      </label>) : <p className="text-slate-500">선택된 기본 입력이 없습니다. 위에서 검토된 값을 직접 선택하세요.</p>}
      <label className="flex gap-2 leading-5"><input type="checkbox" checked={snapshotConsent} disabled={locked || !chosenSelections.length}
        onChange={(event) => setSnapshotConsent(event.target.checked)} />선택한 값만 승인본으로 저장합니다. 프로젝트 입력 반영은 다음 단계입니다.</label>
      <button type="button" className={buttonClass} disabled={locked || !snapshotConsent || !chosenSelections.length
        || chosenSelections.some((value) => !approvedSelections.some((item) => item.selectionId === value))}
        onClick={() => { const snapshotId = `web-snapshot-${crypto.randomUUID()}`;
          void save("snapshot", snapshotId, () => approveParserV2InputSnapshot(projectId, { snapshotId,
            selectionIds: chosenSelections, expectedSelectionVersions: Object.fromEntries(approvedSelections.filter((item) => chosenSelections.includes(id(item.selectionId)))
              .map((item) => [id(item.selectionId), Number(item.selectionVersion)])), purpose: PURPOSE,
            supersedesSnapshotId: latestSnapshot ? id(latestSnapshot.snapshotId) : undefined }));
        }}>선택한 값으로 승인본 저장</button>
    </div>
    <div className="space-y-2 rounded-lg border border-slate-200 p-3 text-xs dark:border-slate-800">
      <p className="font-black">3. 현재 입력과 비교한 뒤 반영</p>
      <label className="block">사용할 승인본<select aria-label="반영할 승인본" value={snapshotId} disabled={locked} className="mt-1 min-h-10 w-full rounded-lg border bg-transparent p-2"
        onChange={(event) => {setChosenSnapshot(event.target.value); setPreview(null); setChosenFields([]); setApplicationConsent(false);}}>
        {!activeSnapshots.length ? <option value="">활성 승인본 없음</option> : activeSnapshots.map((snapshot) => <option key={id(snapshot.snapshotId)} value={id(snapshot.snapshotId)}>
          {snapshot.purpose || "승인 입력"} · 버전 {snapshot.snapshotVersion || 1}</option>)}
      </select></label>
      <button type="button" className={buttonClass} disabled={locked || !snapshotId} onClick={() => void compare()}>현재 입력과 비교</button>
      {preview?.snapshotId === snapshotId ? <>
        {preview.items.map((item) => <label key={item.fieldKey} className="flex items-start gap-2 rounded-lg bg-slate-50 p-3 leading-5 dark:bg-slate-900">
          <input type="checkbox" aria-label={`${LABELS[item.fieldKey]} 프로젝트 입력 반영 선택`} className="mt-1" checked={chosenFields.includes(item.fieldKey)} disabled={locked} onChange={(event) => {
            setChosenFields((values) => event.target.checked ? [...values, item.fieldKey] : values.filter((value) => value !== item.fieldKey)); setApplicationConsent(false);
          }} /><span className="min-w-0"><strong>{LABELS[item.fieldKey]}</strong><br />현재: {format(item.currentValue, item.unit)}<br />승인본: {format(item.approvedValue, item.unit)}
            <br /><span className="break-all text-slate-500">반영 위치: {item.targetPath} · 원본 {item.sourceLocator.sheetName || item.documentVersionId} {item.sourceLocator.cellReference || item.sourceLocator.pageNumber}</span></span>
        </label>)}
        {preview.limitations.map((text) => <p key={text} className="leading-5 text-amber-800">{text}</p>)}
        <label className="flex gap-2 leading-5"><input type="checkbox" checked={applicationConsent} disabled={locked || !chosenFields.length}
          onChange={(event) => setApplicationConsent(event.target.checked)} />선택한 현재 입력을 승인본 값으로 변경하는 데 동의합니다.</label>
        <button type="button" className={buttonClass} disabled={locked || !applicationConsent || !chosenFields.length} onClick={() => void apply()}>선택한 승인 입력을 프로젝트에 반영</button>
        <ProjectWorkspaceLink projectId={projectId} path="land" className="block font-bold text-indigo-600 underline">토지 화면에서 반영값 확인</ProjectWorkspaceLink>
      </> : null}
    </div>
  </section>;
}
