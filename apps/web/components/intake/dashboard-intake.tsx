"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, Loader2, UploadCloud } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { backendErrorMessageForResponse } from "../../lib/backend-error";
import { SmartIntake, type IntakeWorkStatus } from "./smart-intake";
import { validateIntakeFiles } from "../../lib/intake-upload-client";
import { ProjectNameEditor, PROJECT_NAME_MAX_LENGTH } from "../project/project-name-editor";
import { useAuth } from "../auth/auth-context";
import { hasPermission, PROJECT_WRITE_PERMISSION, projectWriteRestrictionMessage } from "../../lib/authorization";

type Phase = "idle" | "creating" | "ready" | "error";

export interface DashboardIntakeSession extends IntakeWorkStatus {
  started: boolean;
}

function deriveProjectName(files: File[]): string {
  const first = files[0]?.name ?? "";
  const base = first.replace(/\.[^.]+$/, "").trim();
  if (base) return base.length > 60 ? base.slice(0, 60) : base;
  const d = new Date();
  const stamp = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return `새 프로젝트 ${stamp}`;
}

/**
 * Dashboard-first intake: drop any documents at once, we create a draft review
 * project on the fly and hand the files to SmartIntake for AI classification.
 */
export function DashboardIntake({ onProjectCreated, onSessionChange }: {
  onProjectCreated?: () => void;
  onSessionChange?: (session: DashboardIntakeSession) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const nameInputId = useId();
  const { permissions, user } = useAuth();
  const canWrite = hasPermission(permissions, PROJECT_WRITE_PERMISSION);
  const [requestedName, setRequestedName] = useState("");
  const creatingRef = useRef(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [dragOver, setDragOver] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string>("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [intakeStatus, setIntakeStatus] = useState<IntakeWorkStatus>({ busy: false, pending: true });
  const busy = phase === "creating" || (phase === "ready" && intakeStatus.busy);
  const pending = phase === "creating" || (phase === "error" && files.length > 0)
    || (phase === "ready" && intakeStatus.pending);
  const started = phase !== "idle";

  useEffect(() => {
    onSessionChange?.({ started, busy, pending });
  }, [started, busy, pending, onSessionChange]);

  useEffect(() => {
    if (!busy && !pending) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [busy, pending]);

  const start = useCallback(
    async (picked: File[]) => {
      if (!picked.length || creatingRef.current) return;
      if (!canWrite) { setError(projectWriteRestrictionMessage(user?.role)); return; }
      creatingRef.current = true;
      const name = requestedName.trim() || deriveProjectName(picked);
      setProjectName(name);
      setPhase("creating");
      setError(null);
      try {
        validateIntakeFiles(picked);
        // Snapshot bytes immediately. The <input> that produced these File handles
        // is unmounted when we switch to the SmartIntake view; an input-backed File
        // can then read as 0 bytes. Buffer into in-memory Files so later uploads are safe.
        const buffered = await Promise.all(
          picked.map(async (f) => new File([await f.arrayBuffer()], f.name, { type: f.type, lastModified: f.lastModified })),
        );
        setFiles(buffered);
        if (inputRef.current) inputRef.current.value = "";
        const res = await fetch(`${apiBase}/projects/`, {
          method: "POST",
          headers: { ...authHeaders("web"), "Content-Type": "application/json" },
          body: JSON.stringify({ name, status: "draft" }),
        });
        if (!res.ok) {
          throw new Error(
            await backendErrorMessageForResponse(res, { action: "프로젝트 생성" }),
          );
        }
        const body = (await res.json()) as { id?: string; project?: { id?: string } };
        const id = body.id ?? body.project?.id;
        if (!id) throw new Error("생성된 프로젝트 ID를 받지 못했습니다.");
        setProjectId(String(id));
        setIntakeStatus({ busy: false, pending: true });
        setPhase("ready");
        onProjectCreated?.();
      } catch (err) {
        setError(err instanceof Error ? err.message : "프로젝트 생성 중 오류가 발생했습니다.");
        setPhase("error");
      } finally {
        creatingRef.current = false;
        if (inputRef.current) inputRef.current.value = "";
      }
    },
    [onProjectCreated, requestedName, canWrite, user?.role],
  );

  const reset = useCallback(() => {
    if (busy || (pending && !window.confirm("처리하지 않은 파일이 남아 있습니다. 이 화면의 파일 선택을 비우고 새 프로젝트를 시작할까요? 이미 저장된 자료는 유지됩니다."))) return;
    setPhase("idle");
    setProjectId(null);
    setProjectName("");
    setRequestedName("");
    setFiles([]);
    setError(null);
    setIntakeStatus({ busy: false, pending: true });
  }, [busy, pending]);

  if (phase === "ready" && projectId) {
    return (
      <section className="space-y-4" aria-label="새 프로젝트 자료 처리">
        <div className="flex flex-col gap-3 rounded-re-control bg-re-subtle p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs text-re-secondary">생성된 프로젝트</p>
            <p className="break-words text-sm font-semibold text-re-text">{projectName}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <ProjectNameEditor key={projectId} projectId={projectId} currentName={projectName} onSaved={(name) => { setProjectName(name); onProjectCreated?.(); }} />
            <Link
              href={`/project/${encodeURIComponent(projectId)}/input-package`}
              aria-disabled={busy}
              onClick={(event) => {
                if (busy || (pending && !window.confirm("아직 처리하지 않은 파일이 있습니다. 이동하면 이 화면의 파일 선택은 사라집니다. 이미 저장된 자료를 확인하러 이동할까요?"))) event.preventDefault();
              }}
              className={`inline-flex min-h-10 items-center gap-1.5 rounded-re-control border border-re-border bg-re-surface px-3 text-sm font-medium text-re-action ${busy ? "cursor-not-allowed opacity-50" : "hover:bg-re-subtle"}`}
            >
              프로젝트 자료 보기 <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Link>
            <button
              type="button"
              onClick={reset}
              disabled={busy}
              className="min-h-10 rounded-re-control px-3 text-sm text-re-secondary hover:bg-re-surface disabled:cursor-not-allowed disabled:opacity-50"
            >
              다른 프로젝트 시작
            </button>
          </div>
        </div>
        <SmartIntake projectId={projectId} initialFiles={files} onProcessed={onProjectCreated} onStatusChange={setIntakeStatus} />
      </section>
    );
  }

  return (
    <section className="space-y-4" aria-label="프로젝트 자료 업로드">
      <p className="text-sm leading-6 text-re-secondary">사업계획서·토지조서·사업수지 등 보유 자료를 올려 주세요. 자료를 분류한 뒤 처리할 내용을 확인할 수 있습니다.</p>

      <div>
        <label htmlFor={nameInputId} className="block text-sm font-semibold text-re-text">프로젝트 이름 (선택)</label>
        <input id={nameInputId} value={requestedName} onChange={(event) => setRequestedName(event.target.value)}
          maxLength={PROJECT_NAME_MAX_LENGTH} disabled={phase === "creating" || !canWrite}
          placeholder="비워 두면 첫 번째 파일 이름을 사용합니다"
          className="mt-2 min-h-11 w-full rounded-re-control border border-re-border bg-re-surface px-3 text-sm text-re-text focus:outline-none focus:ring-2 focus:ring-re-action disabled:opacity-60" />
        <p className="mt-1 text-xs text-re-secondary">생성 후에도 이름을 바꿀 수 있습니다.</p>
      </div>

      <div
        role="button"
        tabIndex={0}
        aria-label="프로젝트 자료 파일 선택"
        aria-disabled={phase === "creating"}
        onClick={() => !creatingRef.current && inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            if (!creatingRef.current) inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!creatingRef.current) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files?.length) void start(Array.from(e.dataTransfer.files));
        }}
        className={`flex min-h-64 flex-col items-center justify-center gap-3 rounded-re-panel border-2 border-dashed px-5 py-10 text-center ${
          phase === "creating"
            ? "cursor-wait border-re-border bg-re-subtle"
            : dragOver
              ? "border-re-action bg-re-subtle"
              : "cursor-pointer border-re-border hover:bg-re-subtle"
        }`}
      >
        {phase === "creating" ? (
          <>
            <Loader2 className="h-8 w-8 animate-spin text-re-action" aria-hidden="true" />
            <p className="text-sm font-medium text-re-text">프로젝트를 준비하고 있습니다…</p>
          </>
        ) : (
          <>
            <UploadCloud className="h-8 w-8 text-re-secondary" aria-hidden="true" />
            <p className="text-sm font-medium text-re-text">파일을 끌어다 놓거나 클릭해서 선택하세요.</p>
            <p className="text-xs text-re-secondary">PDF · XLS/XLSX · DOCX · JSON · CSV · 파일당 최대 64MB · 여러 파일 선택 가능</p>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            // Capture File handles before any reset; start() buffers their bytes.
            if (e.target.files?.length) void start(Array.from(e.target.files));
          }}
        />
      </div>

      {error ? <p className="text-sm text-re-danger" role="alert">{error}</p> : null}
    </section>
  );
}
