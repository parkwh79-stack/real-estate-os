"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

type ProjectRow = {
  project?: { id?: string; name?: string };
  land?: { address?: string };
  land_acquisition?: Array<{ pnu?: string }>;
};

function matches(p: ProjectRow, q: string): boolean {
  const hay = [
    p.project?.name,
    p.project?.id,
    p.land?.address,
    ...(p.land_acquisition ?? []).map((x) => x.pnu),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(q.toLowerCase());
}

/** Global header search — finds a project by name / 주소 / PNU and opens it. */
export function HeaderSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/`, { cache: "no-store", headers: authHeaders("web") });
      if (!res.ok) throw new Error(`검색 실패 (${res.status})`);
      const projects = (await res.json()) as ProjectRow[];
      const hit = projects.find((p) => matches(p, q));
      if (hit?.project?.id) {
        router.push(`/project/${hit.project.id}`);
      } else {
        setError("일치하는 프로젝트가 없습니다.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "검색 중 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  }, [query, router]);

  return (
    <div className="relative w-full max-w-xl">
      <label className="flex h-10 w-full items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900">
        {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            if (error) setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") void run();
          }}
          placeholder="주소, PNU, 프로젝트 검색"
          className="w-full bg-transparent text-slate-900 outline-none placeholder:text-slate-400 dark:text-white"
        />
      </label>
      {error ? (
        <p className="absolute left-0 top-11 rounded-md bg-rose-50 px-2 py-1 text-xs font-semibold text-rose-600 shadow dark:bg-rose-950/60 dark:text-rose-300">
          {error}
        </p>
      ) : null}
    </div>
  );
}
