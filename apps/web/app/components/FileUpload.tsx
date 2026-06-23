"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { GitBranch, Loader2, Upload } from "lucide-react";
import { authHeaders } from "../../lib/auth-client";

type UploadMode = "parse" | "run";

interface UploadResponse {
  status?: string;
  run?: {
    run_id: string;
  };
}

export default function FileUpload({ projectId }: { projectId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [activeMode, setActiveMode] = useState<UploadMode | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const router = useRouter();

  const handleUpload = async (mode: UploadMode) => {
    if (!file) return;
    setActiveMode(mode);
    setMessage(null);

    const formData = new FormData();
    formData.append("file", file);
    const endpoint = mode === "run" ? "upload-and-run" : "upload";

    try {
      const res = await fetch(`http://127.0.0.1:8000/projects/${projectId}/${endpoint}`, {
        method: "POST",
        headers: authHeaders("web"),
        body: formData,
      });

      if (!res.ok) {
        throw new Error("문서 처리에 실패했습니다.");
      }

      const body = (await res.json()) as UploadResponse;
      if (body.status === "host_llm_required") {
        setMessage("Claude Desktop host LLM 추출이 필요한 문서입니다. MCP에서 Claude가 추출한 JSON으로 다음 단계를 실행하세요.");
        return;
      }

      if (mode === "run" && body.run?.run_id) {
        setMessage("문서 분석과 PF 심사 실행이 완료되었습니다.");
        router.push(`/project/${projectId}/orchestration`);
        router.refresh();
        return;
      }

      setMessage("문서 분석이 완료되어 프로젝트 데이터가 업데이트되었습니다.");
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage(error instanceof Error ? error.message : "업로드 중 오류가 발생했습니다.");
    } finally {
      setActiveMode(null);
    }
  };

  const isBusy = activeMode !== null;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <label className="flex-1">
          <input
            type="file"
            accept=".pdf"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
            className="block w-full text-sm text-slate-500 file:mr-4 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-4 file:py-2.5 file:text-sm file:font-semibold file:text-indigo-700 hover:file:bg-indigo-100 dark:file:bg-indigo-950/30 dark:file:text-indigo-300"
          />
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <button
            type="button"
            onClick={() => void handleUpload("parse")}
            disabled={!file || isBusy}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            {activeMode === "parse" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            문서 분석
          </button>
          <button
            type="button"
            onClick={() => void handleUpload("run")}
            disabled={!file || isBusy}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
          >
            {activeMode === "run" ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}
            분석 후 PF 심사 실행
          </button>
        </div>
      </div>
      {message ? (
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          {message}
        </p>
      ) : null}
    </div>
  );
}
