"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, Loader2, Sparkles, UploadCloud } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { SmartIntake } from "./smart-intake";

type Phase = "idle" | "creating" | "ready" | "error";

function deriveProjectName(files: File[]): string {
  const first = files[0]?.name ?? "";
  const base = first.replace(/\.[^.]+$/, "").trim();
  if (base) return base.length > 60 ? base.slice(0, 60) : base;
  const d = new Date();
  const stamp = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return `새 심사 ${stamp}`;
}

/**
 * Dashboard-first intake: drop any documents at once, we create a draft review
 * project on the fly and hand the files to SmartIntake for AI classification.
 */
export function DashboardIntake({ onProjectCreated }: { onProjectCreated?: () => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [dragOver, setDragOver] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string>("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);

  const start = useCallback(
    async (picked: File[]) => {
      if (!picked.length) return;
      const name = deriveProjectName(picked);
      setProjectName(name);
      setPhase("creating");
      setError(null);
      try {
        // Snapshot bytes immediately. The <input> that produced these File handles
        // is unmounted when we switch to the SmartIntake view; an input-backed File
        // can then read as 0 bytes. Buffer into in-memory Files so later uploads are safe.
        const buffered = await Promise.all(
          picked.map(async (f) => new File([await f.arrayBuffer()], f.name, { type: f.type })),
        );
        setFiles(buffered);
        const res = await fetch(`${apiBase}/projects/`, {
          method: "POST",
          headers: { ...authHeaders("web"), "Content-Type": "application/json" },
          body: JSON.stringify({ name, status: "draft" }),
        });
        if (!res.ok) throw new Error(`심사 공간 생성 실패 (${res.status})`);
        const body = (await res.json()) as { id?: string; project?: { id?: string } };
        const id = body.id ?? body.project?.id;
        if (!id) throw new Error("생성된 심사 ID를 받지 못했습니다.");
        setProjectId(String(id));
        setPhase("ready");
        onProjectCreated?.();
      } catch (err) {
        setError(err instanceof Error ? err.message : "심사 공간 생성 중 오류가 발생했습니다.");
        setPhase("error");
      }
    },
    [onProjectCreated],
  );

  const reset = useCallback(() => {
    setPhase("idle");
    setProjectId(null);
    setProjectName("");
    setFiles([]);
    setError(null);
  }, []);

  if (phase === "ready" && projectId) {
    return (
      <section className="space-y-3">
        <div className="flex flex-col gap-2 rounded-xl border border-indigo-200 bg-indigo-50 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-indigo-900/50 dark:bg-indigo-950/30">
          <div className="min-w-0">
            <p className="text-xs font-bold uppercase tracking-wide text-indigo-600 dark:text-indigo-300">새 심사 생성됨</p>
            <p className="truncate text-sm font-black text-slate-900 dark:text-white">{projectName}</p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Link
              href={`/project/${projectId}/input-package`}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-slate-950 px-3 text-xs font-bold text-white dark:bg-white dark:text-slate-950"
            >
              심사 상세로 이동 <ArrowRight className="h-3.5 w-3.5" />
            </Link>
            <button
              type="button"
              onClick={reset}
              className="inline-flex h-9 items-center rounded-lg border border-slate-300 px-3 text-xs font-bold text-slate-700 hover:bg-white dark:border-slate-700 dark:text-slate-200"
            >
              다른 심사 시작
            </button>
          </div>
        </div>
        <SmartIntake projectId={projectId} initialFiles={files} onProcessed={onProjectCreated} />
      </section>
    );
  }

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-gradient-to-br from-white to-slate-50 p-6 shadow-sm dark:border-slate-800 dark:from-slate-900 dark:to-slate-950">
      <div className="flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-600 text-white">
          <Sparkles className="h-6 w-6" />
        </div>
        <div>
          <h2 className="text-xl font-black tracking-tight text-slate-950 dark:text-white">자료 한 번에 올리고 심사 시작</h2>
          <p className="mt-0.5 text-sm text-slate-500">
            사업계획서·토지조서·사업수지 등 가진 자료를 종류 구분 없이 한꺼번에 올리면, AI가 문서를 자동 분류하고 새 심사를 만들어 드립니다.
          </p>
        </div>
      </div>

      <div
        role="button"
        tabIndex={0}
        aria-disabled={phase === "creating"}
        onClick={() => phase !== "creating" && inputRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && phase !== "creating") inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (phase !== "creating") setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (phase !== "creating" && e.dataTransfer.files?.length) void start(Array.from(e.dataTransfer.files));
        }}
        className={`mt-5 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors ${
          phase === "creating"
            ? "cursor-wait border-indigo-300 bg-indigo-50/60 dark:border-indigo-700 dark:bg-indigo-950/20"
            : dragOver
              ? "border-indigo-400 bg-indigo-50 dark:border-indigo-500 dark:bg-indigo-950/30"
              : "border-slate-300 hover:border-indigo-300 dark:border-slate-700"
        }`}
      >
        {phase === "creating" ? (
          <>
            <Loader2 className="h-9 w-9 animate-spin text-indigo-600" />
            <p className="text-sm font-bold text-slate-700 dark:text-slate-200">심사 공간을 준비하고 있어요…</p>
            <p className="text-xs text-slate-400">생성되면 올리신 자료를 AI가 바로 분류합니다.</p>
          </>
        ) : (
          <>
            <UploadCloud className="h-9 w-9 text-slate-400" />
            <p className="text-sm font-bold text-slate-700 dark:text-slate-200">파일을 끌어다 놓거나 클릭해서 선택 (여러 개 가능)</p>
            <p className="text-xs text-slate-400">PDF · XLS/XLSX · DOCX · JSON · CSV</p>
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

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}
    </section>
  );
}
