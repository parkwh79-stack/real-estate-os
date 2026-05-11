"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, Loader2 } from "lucide-react";

export default function FileUpload({ projectId }: { projectId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const router = useRouter();

  const handleUpload = async () => {
    if (!file) return;
    setIsUploading(true);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`http://127.0.0.1:8000/projects/${projectId}/upload`, {
        method: "POST",
        body: formData,
      });

      if (res.ok) {
        alert("사업계획서가 성공적으로 분석되었습니다!");
        router.refresh();
      } else {
        alert("문서 분석에 실패했습니다.");
      }
    } catch (e) {
      console.error(e);
      alert("업로드 중 오류가 발생했습니다.");
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
      <label className="flex-1">
        <input
          type="file"
          accept=".pdf"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
          className="block w-full text-sm text-slate-500 file:mr-4 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-4 file:py-2.5 file:text-sm file:font-semibold file:text-indigo-700 hover:file:bg-indigo-100 dark:file:bg-indigo-950/30 dark:file:text-indigo-300"
        />
      </label>
      <button
        type="button"
        onClick={handleUpload}
        disabled={!file || isUploading}
        className="inline-flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-40 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
      >
        {isUploading ? (
          <><Loader2 className="h-4 w-4 animate-spin" /> AI 분석 중...</>
        ) : (
          <><Upload className="h-4 w-4" /> 문서 분석</>
        )}
      </button>
    </div>
  );
}
