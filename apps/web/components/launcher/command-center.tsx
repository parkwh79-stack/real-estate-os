"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Loader2, ShieldCheck, Sparkles, UploadCloud } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { SmartIntake } from "../intake/smart-intake";

type Phase = "idle" | "creating" | "ready" | "error";

function deriveProjectName(files: File[]): string {
  const base = (files[0]?.name ?? "").replace(/\.[^.]+$/, "").trim();
  if (base) return base.length > 60 ? base.slice(0, 60) : base;
  const d = new Date();
  return `새 심사 ${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * REALYTICS Command Center — the sidebar-less, full-screen launcher shown after
 * login. Drop documents → AI classifies and a draft project is created → enter
 * the workspace. (Phase 2 will insert an AI track-triage step here.)
 */
export function CommandCenter() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [dragOver, setDragOver] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);

  const start = useCallback(async (picked: File[]) => {
    if (!picked.length) return;
    const name = deriveProjectName(picked);
    setProjectName(name);
    setPhase("creating");
    setError(null);
    try {
      // Snapshot bytes while the input is still mounted (input-backed File handles
      // can read as 0 bytes once unmounted).
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
    } catch (e) {
      setError(e instanceof Error ? e.message : "심사 공간 생성 중 오류가 발생했습니다.");
      setPhase("error");
    }
  }, []);

  return (
    <main className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-slate-950 px-5 py-12 text-white">
      {/* Ambient backdrop */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_50%_at_50%_0%,rgba(56,189,248,0.12),transparent),radial-gradient(50%_40%_at_80%_100%,rgba(99,102,241,0.12),transparent)]" />

      <div className="relative z-10 w-full max-w-3xl">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="inline-flex items-center gap-2 rounded-full border border-sky-400/30 bg-sky-500/10 px-3 py-1.5 text-xs font-bold tracking-[0.15em] text-sky-300">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-500" />
            </span>
            REALYTICS OS
          </div>
          <h1 className="mt-5 text-3xl font-black tracking-tight sm:text-4xl">
            자료를 올리면 <span className="bg-gradient-to-r from-sky-400 to-indigo-400 bg-clip-text text-transparent">AI가 분석을 시작</span>합니다
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-slate-400">
            사업계획서·토지조서·사업수지·임대내역 등 가진 자료를 종류 구분 없이 한 번에 올리세요. AI가 문서를 자동 분류하고 진행할 업무를 판단합니다.
          </p>
        </div>

        {phase === "ready" && projectId ? (
          <div className="space-y-4">
            <div className="flex flex-col gap-2 rounded-2xl border border-sky-500/20 bg-slate-900/60 p-4 backdrop-blur sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="text-xs font-bold uppercase tracking-wide text-sky-300">심사 공간 생성됨</p>
                <p className="truncate text-sm font-black">{projectName}</p>
              </div>
              <button
                type="button"
                onClick={() => router.push(`/project/${projectId}/input-package`)}
                className="inline-flex h-10 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-white px-4 text-sm font-bold text-slate-950 hover:bg-slate-200"
              >
                워크스페이스 입장 <ArrowRight className="h-4 w-4" />
              </button>
            </div>
            {/* Reuse the proven classify-and-process panel with the buffered files. */}
            <div className="rounded-2xl bg-white p-1 text-slate-900 dark:bg-slate-900 dark:text-white">
              <SmartIntake projectId={projectId} initialFiles={files} />
            </div>
          </div>
        ) : (
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
            className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-3xl border-2 border-dashed px-6 py-16 text-center transition-colors ${
              phase === "creating"
                ? "cursor-wait border-sky-500/40 bg-sky-500/5"
                : dragOver
                  ? "border-sky-400 bg-sky-500/10"
                  : "border-slate-700 hover:border-sky-500/50 hover:bg-white/5"
            }`}
          >
            {phase === "creating" ? (
              <>
                <Loader2 className="h-10 w-10 animate-spin text-sky-400" />
                <p className="text-base font-bold">심사 공간을 준비하고 있습니다…</p>
                <p className="text-xs text-slate-400">생성되면 올리신 자료를 AI가 바로 분류합니다.</p>
              </>
            ) : (
              <>
                <UploadCloud className="h-10 w-10 text-slate-500" />
                <p className="text-base font-bold">파일을 끌어다 놓거나 클릭해서 선택 (여러 개 가능)</p>
                <p className="text-xs text-slate-500">PDF · XLS/XLSX · DOCX · JSON · CSV</p>
              </>
            )}
            <input
              ref={inputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files?.length) void start(Array.from(e.target.files));
              }}
            />
          </div>
        )}

        {error ? <p className="mt-4 text-center text-sm font-bold text-rose-400">{error}</p> : null}

        <div className="mt-8 flex items-center justify-center gap-2 text-xs text-slate-500">
          <ShieldCheck className="h-4 w-4" />
          업로드 자료는 세션 기준으로 안전하게 보관되며, AI는 근거 있는 추출만 수행합니다.
          <Sparkles className="h-4 w-4 text-sky-500/70" />
        </div>
      </div>
    </main>
  );
}
