"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Plus, UploadCloud, X } from "lucide-react";
import { DashboardIntake, type DashboardIntakeSession } from "./dashboard-intake";

export function NewProjectDialog({ onProjectCreated, firstProject = false }: {
  onProjectCreated?: () => void;
  firstProject?: boolean;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const [open, setOpen] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const [session, setSession] = useState<DashboardIntakeSession>({ started: false, busy: false, pending: false });
  const [navigationNotice, setNavigationNotice] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [open]);

  useEffect(() => {
    if (open || (!session.busy && !session.pending)) return;
    // Same-tab SPA links do not trigger the browser's beforeunload warning.
    // History navigation and programmatic router changes are outside this guard.
    const protectPendingFiles = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const anchor = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[href]") : null;
      if (!anchor || dialogRef.current?.contains(anchor) || anchor.hasAttribute("download")
        || (anchor.target && anchor.target !== "_self")) return;
      const destination = new URL(anchor.href, window.location.href);
      // External navigation already uses the native beforeunload warning. A
      // same-page hash/query change keeps this dashboard instance mounted.
      if (destination.origin !== window.location.origin || destination.pathname === window.location.pathname) return;
      if (session.busy) {
        event.preventDefault();
        event.stopPropagation();
        setNavigationNotice("자료 처리 중에는 다른 화면으로 이동할 수 없습니다. 처리가 끝난 뒤 이동해 주세요.");
        setOpen(true);
        return;
      }
      if (!window.confirm("아직 처리하지 않은 파일이 있습니다. 다른 화면으로 이동하면 이 화면의 파일 선택은 사라집니다. 이동할까요? 이미 저장된 자료는 유지됩니다.")) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    document.addEventListener("click", protectPendingFiles, true);
    return () => document.removeEventListener("click", protectPendingFiles, true);
  }, [open, session.busy, session.pending]);

  const label = session.started ? "자료 업로드 이어가기" : firstProject ? "첫 프로젝트 시작" : "새 프로젝트";

  return (
    <>
      <button type="button" onClick={() => { setHasOpened(true); setNavigationNotice(null); setOpen(true); }} className="inline-flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-re-control bg-re-brand px-4 text-sm font-semibold text-white hover:opacity-90">
        {session.started ? <UploadCloud className="h-4 w-4" aria-hidden="true" /> : <Plus className="h-4 w-4" aria-hidden="true" />}
        {label}
      </button>
      <dialog
        ref={dialogRef}
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onCancel={() => setOpen(false)}
        onClose={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key !== "Tab") return;
          const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("a[href], button, input, select, textarea, [tabindex]"))
            .filter((element) => element.tabIndex >= 0 && !element.matches(":disabled, [aria-disabled='true']") && element.getClientRects().length > 0);
          const first = controls[0];
          const last = controls.at(-1);
          if (event.shiftKey && document.activeElement === first && last) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last && first) {
            event.preventDefault();
            first.focus();
          }
        }}
        className="m-auto max-h-[calc(100dvh_-_2rem)] w-[calc(100%_-_2rem)] max-w-5xl overflow-y-auto rounded-re-panel border border-re-border bg-re-surface p-0 text-re-text shadow-xl backdrop:bg-slate-950/50"
      >
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-re-border bg-re-surface px-5 py-4 sm:px-6">
          <div>
            <h2 id={titleId} className="text-lg font-semibold">{session.started ? "프로젝트 자료 업로드" : "새 프로젝트"}</h2>
            <p id={descriptionId} className="mt-1 text-xs leading-5 text-re-secondary">창을 닫아도 현재 페이지에서 파일 선택과 진행 상태가 유지됩니다.</p>
          </div>
          <button type="button" onClick={() => setOpen(false)} aria-label="자료 업로드 창 닫기" className="inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center rounded-re-control text-re-secondary hover:bg-re-subtle"><X className="h-5 w-5" aria-hidden="true" /></button>
        </div>
        <div className="p-5 sm:p-6">
          {navigationNotice && session.busy ? <p className="mb-4 rounded-re-control bg-re-subtle p-3 text-sm text-re-secondary" role="status">{navigationNotice}</p> : null}
          {hasOpened ? <DashboardIntake onProjectCreated={onProjectCreated} onSessionChange={setSession} /> : null}
        </div>
      </dialog>
    </>
  );
}
