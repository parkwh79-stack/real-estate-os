"use client";

import { useEffect, useState } from "react";
import { Briefcase, Loader2 } from "lucide-react";
import { fetchProjectOptions, type ProjectOption } from "../../lib/project-client";
import { projectIdFromLocation } from "../../lib/project-navigation";

const STORAGE_KEY = "realytics.selected.project.v1";

/** Remembered selection so the chosen project persists across the demo→real pages. */
export function getStoredSelectedProjectId(): string | null {
  if (typeof window === "undefined") return null;
  const contextual = projectIdFromLocation(window.location.pathname, window.location.search);
  if (contextual) return contextual;
  try { return window.localStorage.getItem(STORAGE_KEY); } catch { return null; }
}

/**
 * Shared project selector. Lists the session's real projects and reports the
 * chosen id; the selection is remembered in localStorage so analysis/finance/
 * report pages all open on the same real project instead of demo data.
 */
export function ProjectPicker({
  value,
  onChange,
  label = "심사 프로젝트",
}: {
  value: string | null;
  onChange: (id: string) => void;
  label?: string;
}) {
  const [options, setOptions] = useState<ProjectOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchProjectOptions()
      .then((opts) => {
        if (cancelled) return;
        setOptions(opts);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "프로젝트 목록 로드 실패");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const select = (id: string) => {
    if (!id) return;
    if (typeof window !== "undefined") {
      try { window.localStorage.setItem(STORAGE_KEY, id); } catch { /* storage may be disabled */ }
      // Global analysis pages carry the selected project in their URL as well.
      if (!window.location.pathname.startsWith("/project/")) {
        const url = new URL(window.location.href);
        url.searchParams.set("projectId", id);
        window.history.replaceState(null, "", url);
      }
      window.dispatchEvent(new Event("realytics-project-changed"));
    }
    onChange(id);
  };

  return (
    <label className="flex h-11 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {loading ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : <Briefcase className="h-4 w-4 text-slate-400" />}
      <span className="shrink-0 font-semibold text-slate-500">{label}</span>
      <select
        value={value ?? ""}
        onChange={(e) => select(e.target.value)}
        disabled={loading || options.length === 0}
        className="w-full bg-transparent font-semibold text-slate-900 outline-none disabled:opacity-50 dark:text-white"
      >
        <option value="" disabled>
          {loading ? "불러오는 중…" : options.length ? "프로젝트를 선택하세요" : "프로젝트 없음"}
        </option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
            {o.address ? ` · ${o.address}` : ""}
          </option>
        ))}
      </select>
      {error ? <span className="shrink-0 text-xs font-semibold text-rose-500">{error}</span> : null}
    </label>
  );
}
