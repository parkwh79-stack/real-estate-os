"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { projectOptionsFromResponse, type ProjectOption } from "../../lib/project-client";

function matches(project: ProjectOption, query: string, raw: unknown): boolean {
  const acquisitions = raw && typeof raw === "object"
    ? (raw as Record<string, unknown>).land_acquisition : undefined;
  const pnus = Array.isArray(acquisitions) ? acquisitions.flatMap((land: unknown) => {
    if (!land || typeof land !== "object") return [];
    const pnu = (land as Record<string, unknown>).pnu;
    return typeof pnu === "string" ? [pnu] : [];
  }) : [];
  return [
    project.name, project.id, project.address, project.pnu, ...pnus,
  ].filter(Boolean).join(" ").toLowerCase().includes(query.toLowerCase());
}

/** Search only selects a project after the user chooses a matching candidate. */
export function HeaderSearch() {
  const listId = useId();
  const requestRef = useRef<AbortController | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<ProjectOption[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  useEffect(() => () => requestRef.current?.abort(), []);
  useEffect(() => {
    if (open && activeIndex >= 0) {
      document.getElementById(`${listId}-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
    }
  }, [open, activeIndex, listId]);

  const run = useCallback(async () => {
    const search = query.trim();
    if (!search) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setError(null);
    setResults([]);
    setOpen(false);
    setActiveIndex(-1);
    try {
      const response = await fetch(`${apiBase}/projects/`, {
        cache: "no-store", headers: authHeaders("web"), signal: controller.signal,
      });
      if (!response.ok) throw new Error("프로젝트를 검색하지 못했습니다. 다시 시도해 주세요.");
      const body: unknown = await response.json();
      if (!Array.isArray(body)) throw new Error("검색 결과를 확인하지 못했습니다. 다시 시도해 주세요.");
      const found = projectOptionsFromResponse(body).filter((project, index) =>
        /^[A-Za-z0-9:_-]+$/.test(project.id) && matches(project, search, body[index]),
      );
      if (controller.signal.aborted) return;
      setResults(found);
      setOpen(true);
    } catch {
      if (controller.signal.aborted) return;
      setError("프로젝트를 검색하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [query]);

  const choose = (index: number) => {
    // Use the same link-click path for keyboard and pointer navigation so
    // pending-upload navigation guards can cancel either action.
    document.getElementById(`${listId}-${index}`)?.click();
  };

  return (
    <div className="relative w-full max-w-xl"
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
      <form role="search" aria-label="프로젝트 찾기" onSubmit={(event) => { event.preventDefault(); void run(); }}
        className="flex h-11 w-full items-center rounded-re-control border border-re-border bg-re-subtle">
        <input
          type="text" role="combobox" aria-label="프로젝트 검색" aria-autocomplete="list"
          aria-expanded={open} aria-controls={open ? listId : undefined}
          aria-activedescendant={open && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
          value={query}
          onChange={(event) => {
            requestRef.current?.abort();
            setLoading(false);
            setQuery(event.target.value);
            setError(null);
            setOpen(false);
            setResults([]);
            setActiveIndex(-1);
          }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === "Escape") { event.preventDefault(); setOpen(false); setActiveIndex(-1); }
            if (open && results.length > 0 && ["ArrowDown", "ArrowUp"].includes(event.key)) {
              event.preventDefault();
              setActiveIndex((index) => event.key === "ArrowDown"
                ? (index + 1) % results.length
                : (index <= 0 ? results.length - 1 : index - 1));
            }
            if (event.key === "Enter" && open && activeIndex >= 0) {
              event.preventDefault();
              if (results[activeIndex]) choose(activeIndex);
            }
          }}
          placeholder="프로젝트·주소 검색"
          className="min-w-0 flex-1 bg-transparent px-3 py-2 text-sm text-re-text placeholder:text-re-muted"
        />
        <button type="submit" aria-label="프로젝트 검색 실행" disabled={loading || !query.trim()}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-re-control text-re-secondary hover:bg-re-surface disabled:cursor-default disabled:opacity-50">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Search className="h-4 w-4" aria-hidden="true" />}
        </button>
      </form>
      {loading ? <span role="status" className="sr-only">프로젝트를 검색하고 있습니다.</span> : null}
      {error ? (
        <p role="alert" className="absolute left-0 right-0 top-12 rounded-re-control border border-re-border bg-re-surface p-3 text-sm text-re-danger shadow-re-panel">{error}</p>
      ) : null}
      {open ? (
        <div className="absolute left-0 right-0 top-12 z-40 max-h-80 overflow-y-auto rounded-re-control border border-re-border bg-re-surface shadow-lg">
          <p role="status" className="border-b border-re-border px-3 py-2 text-xs text-re-muted">
            {results.length > 0 ? `검색 결과 ${results.length.toLocaleString("ko-KR")}개 · 프로젝트를 선택하세요` : "일치하는 프로젝트가 없습니다."}
          </p>
          <ul id={listId} role="listbox" aria-label="프로젝트 검색 결과" className="p-1">
            {results.map((project, index) => (
              <li key={project.id} role="presentation" onMouseEnter={() => setActiveIndex(index)}
                className={`rounded-re-control ${activeIndex === index ? "bg-re-subtle" : ""}`}>
                <Link href={`/project/${encodeURIComponent(project.id)}`} prefetch={false}
                  role="option" id={`${listId}-${index}`} aria-selected={activeIndex === index}
                  tabIndex={-1} onMouseDown={(event) => event.preventDefault()}
                  onClick={() => { setOpen(false); setActiveIndex(-1); }}
                  className="block min-h-12 w-full px-3 py-2 text-left">
                  <span className="block text-sm font-semibold text-re-text">{project.name}</span>
                  <span className="block text-xs text-re-muted">{project.address || `프로젝트 #${project.id}`}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
