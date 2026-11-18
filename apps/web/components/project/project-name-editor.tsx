"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Loader2, Pencil, X } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { hasPermission, PROJECT_WRITE_PERMISSION } from "../../lib/authorization";
import { backendErrorMessageForResponse } from "../../lib/backend-error";
import { useAuth } from "../auth/auth-context";

const NAME_CHANGED = "realytics-project-name-changed";
export const PROJECT_NAME_MAX_LENGTH = 200;

// Update only the displayed name; a rename must not reload an unsaved review
// form, discard selected files, or restart document classification.
export function useProjectName(projectId: string, savedName: string) {
  const [changed, setChanged] = useState<{ projectId: string; name: string } | null>(null);
  useEffect(() => {
    const update = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (!detail || typeof detail !== "object") return;
      const value = detail as Record<string, unknown>;
      if (value.projectId === projectId && typeof value.name === "string" && value.name.trim()) {
        setChanged({ projectId, name: value.name });
      }
    };
    window.addEventListener(NAME_CHANGED, update);
    return () => window.removeEventListener(NAME_CHANGED, update);
  }, [projectId]);
  return changed?.projectId === projectId ? changed.name : savedName;
}

export function ProjectNameEditor({ projectId, currentName, onSaved }: {
  projectId: string;
  currentName: string;
  onSaved?: (name: string) => void;
}) {
  const { status, permissions } = useAuth();
  const canWrite = status === "authenticated" && hasPermission(permissions, PROJECT_WRITE_PERMISSION);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const requestRef = useRef<AbortController | null>(null);
  const titleId = useId();
  const descriptionId = useId();
  const inputId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(currentName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => () => requestRef.current?.abort(), [projectId]);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) { dialog.showModal(); inputRef.current?.select(); }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  if (!canWrite) return null;

  const save = async () => {
    if (requestRef.current) return;
    const trimmed = name.trim();
    if (!trimmed) { setError("프로젝트 이름을 입력해 주세요."); inputRef.current?.focus(); return; }
    if (trimmed.length > PROJECT_NAME_MAX_LENGTH) { setError("프로젝트 이름은 200자 이내로 입력해 주세요."); return; }
    if (trimmed === currentName) { setOpen(false); return; }
    const controller = new AbortController();
    requestRef.current = controller;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}`, {
        method: "PUT", headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ project: { name: trimmed } }), signal: controller.signal,
      });
      if (!response.ok) throw new Error(await backendErrorMessageForResponse(response, { action: "프로젝트 이름 저장" }));
      const body = await response.json() as { project?: { id?: unknown; name?: unknown } };
      if (String(body.project?.id ?? "") !== projectId || body.project?.name !== trimmed) {
        throw new Error("저장된 프로젝트 이름을 확인하지 못했습니다. 프로젝트를 다시 열어 확인해 주세요.");
      }
      if (controller.signal.aborted) return;
      window.dispatchEvent(new CustomEvent(NAME_CHANGED, { detail: { projectId, name: trimmed } }));
      onSaved?.(trimmed);
      setSaved(true);
      setOpen(false);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "이름을 저장하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  return <>
    <button type="button" onClick={() => { setName(currentName); setError(null); setSaved(false); setOpen(true); }}
      className="inline-flex min-h-9 shrink-0 items-center gap-1 rounded-re-control border border-re-border bg-re-surface px-2 text-xs font-semibold text-re-secondary hover:bg-re-subtle">
      <Pencil className="h-3.5 w-3.5" aria-hidden="true" />이름 변경
    </button>
    {saved ? <span className="sr-only" role="status">프로젝트 이름을 저장했습니다.</span> : null}
    <dialog ref={dialogRef} aria-labelledby={titleId} aria-describedby={descriptionId}
      onCancel={(event) => { if (busy) event.preventDefault(); else setOpen(false); }}
      onClose={() => setOpen(false)}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button, input"))
          .filter((element) => !element.matches(":disabled") && element.getClientRects().length > 0);
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first && last) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last && first) { event.preventDefault(); first.focus(); }
      }}
      className="m-auto w-[calc(100%_-_2rem)] max-w-lg rounded-re-panel border border-re-border bg-re-surface p-5 text-re-text shadow-xl backdrop:bg-slate-950/50">
      <div className="flex items-center justify-between gap-3">
        <h2 id={titleId} className="text-lg font-semibold">프로젝트 이름 변경</h2>
        <button type="button" aria-label="이름 변경 창 닫기" disabled={busy} onClick={() => setOpen(false)} className="inline-flex min-h-10 min-w-10 items-center justify-center rounded-re-control hover:bg-re-subtle disabled:opacity-50"><X className="h-5 w-5" aria-hidden="true" /></button>
      </div>
      <p id={descriptionId} className="mt-2 text-sm leading-6 text-re-secondary">프로젝트의 표시 이름을 바꿉니다. 업로드한 원본 파일명과 분석 자료는 유지됩니다.</p>
      <form onSubmit={(event) => { event.preventDefault(); void save(); }} className="mt-4 space-y-4">
        <div>
          <label htmlFor={inputId} className="block text-sm font-semibold">프로젝트 이름</label>
          <input ref={inputRef} id={inputId} value={name} onChange={(event) => setName(event.target.value)} maxLength={PROJECT_NAME_MAX_LENGTH}
            disabled={busy} className="mt-2 min-h-11 w-full rounded-re-control border border-re-border bg-re-surface px-3 text-sm text-re-text focus:outline-none focus:ring-2 focus:ring-re-action disabled:opacity-60" />
        </div>
        {error ? <p role="alert" className="text-sm leading-6 text-re-danger">{error}</p> : null}
        <div className="flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={() => setOpen(false)} className="min-h-10 rounded-re-control border border-re-border px-4 text-sm font-semibold disabled:opacity-50">취소</button>
          <button type="submit" disabled={busy} className="inline-flex min-h-10 items-center gap-2 rounded-re-control bg-re-brand px-4 text-sm font-semibold text-white disabled:opacity-60">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}{busy ? "저장 중…" : "이름 저장"}
          </button>
        </div>
      </form>
    </dialog>
  </>;
}
