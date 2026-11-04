"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import {
  Activity, BarChart3, BookMarked, BookOpen, ChevronDown, FileCheck,
  FolderKanban, GitBranch, LayoutDashboard, Layers, LibraryBig, Map,
  Menu, Settings, Wallet, X, type LucideIcon,
} from "lucide-react";
import { getStoredSelectedProjectId } from "../project/project-picker";
import { withProjectContext } from "../../lib/project-navigation";
import { getStoredWorkflowTrack, TRACK_LABELS, type WorkflowTrack } from "../../lib/workflow-track";

const BLOG_URL = "https://blog.naver.com/babo7910/224295847211";

const ANALYSIS_TOOLS = [
  { href: "/analysis", label: "토지·거래 분석", icon: Map },
  { href: "/feasibility", label: "사업성 분석", icon: BarChart3 },
  { href: "/cashflow-detail", label: "현금흐름", icon: Wallet },
  { href: "/finance", label: "금융구조", icon: Layers },
  { href: "/report", label: "PF 심사보고서", icon: FileCheck },
  { href: "/monitoring", label: "실시간 모니터링", icon: Activity },
];

const RESOURCES = [
  { href: "/templates", label: "업무 템플릿", icon: LibraryBig },
  { href: "/glossary", label: "표준 용어", icon: BookOpen },
  { href: "/glossary/candidates", label: "용어 검토", icon: BookMarked },
];

function NavigationContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const [track, setTrack] = useState<WorkflowTrack | null>(null);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);

  useEffect(() => {
    const sync = () => {
      setCurrentProjectId(getStoredSelectedProjectId());
      setTrack(getStoredWorkflowTrack());
    };
    sync();
    window.addEventListener("popstate", sync);
    window.addEventListener("storage", sync);
    window.addEventListener("realytics-project-changed", sync);
    window.addEventListener("realytics-workflow-track-changed", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("storage", sync);
      window.removeEventListener("realytics-project-changed", sync);
      window.removeEventListener("realytics-workflow-track-changed", sync);
    };
  }, [pathname]);

  const isActive = (href: string) => {
    if (href.includes("#")) return false;
    if (href === "/glossary" || /^\/project\/[^/]+$/.test(href)) return pathname === href;
    return pathname === href || pathname.startsWith(`${href}/`);
  };
  const renderLink = (href: string, label: string, Icon: LucideIcon, contextual = false) => {
    const target = contextual ? withProjectContext(href, currentProjectId) : href;
    return (
      <Link
        key={href}
        href={target}
        onClick={() => {
          if (href === "/dashboard#all-projects") {
            const projects = document.getElementById("all-projects");
            if (projects instanceof HTMLDetailsElement) projects.open = true;
          }
          onNavigate?.();
        }}
        aria-current={isActive(href) ? "page" : undefined}
        className={`flex min-h-11 items-center gap-3 rounded-re-control px-3 py-2.5 text-sm font-medium transition-colors ${isActive(href)
          ? "bg-re-subtle text-re-action"
          : "text-re-secondary hover:bg-re-subtle hover:text-re-text"}`}
      >
        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
        {label}
      </Link>
    );
  };

  return (
    <>
      <nav aria-label="주 메뉴" className="flex-1 space-y-1 px-3 py-4">
        {renderLink("/dashboard", "대시보드", LayoutDashboard)}
        {renderLink("/dashboard#all-projects", "내 프로젝트", FolderKanban)}
        {currentProjectId ? (
          <div className="my-4 border-y border-re-border py-3">
            <p className="px-3 pb-1 text-xs text-re-muted">선택한 프로젝트</p>
            {renderLink(`/project/${encodeURIComponent(currentProjectId)}`, "프로젝트 개요", FolderKanban)}
            {renderLink(`/project/${encodeURIComponent(currentProjectId)}/chief-orchestration`, "AI 딜룸", GitBranch)}
          </div>
        ) : null}
        <details className="group" open={ANALYSIS_TOOLS.some((item) => isActive(item.href))}>
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-3 rounded-re-control px-3 py-2.5 text-sm font-medium text-re-secondary hover:bg-re-subtle [&::-webkit-details-marker]:hidden">
            <BarChart3 className="h-4 w-4" aria-hidden="true" />
            분석 도구
            <ChevronDown className="ml-auto h-4 w-4 transition-transform group-open:rotate-180" aria-hidden="true" />
          </summary>
          <div className="ml-3 border-l border-re-border pl-2">
            {ANALYSIS_TOOLS.map((item) => renderLink(item.href, item.label, item.icon, true))}
          </div>
        </details>
        <details className="group" open={RESOURCES.some((item) => isActive(item.href))}>
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-3 rounded-re-control px-3 py-2.5 text-sm font-medium text-re-secondary hover:bg-re-subtle [&::-webkit-details-marker]:hidden">
            <LibraryBig className="h-4 w-4" aria-hidden="true" />
            자료실
            <ChevronDown className="ml-auto h-4 w-4 transition-transform group-open:rotate-180" aria-hidden="true" />
          </summary>
          <div className="ml-3 border-l border-re-border pl-2">
            {RESOURCES.map((item) => renderLink(item.href, item.label, item.icon))}
            <a href={BLOG_URL} target="_blank" rel="noopener noreferrer" onClick={onNavigate}
              className="flex min-h-11 items-center gap-3 rounded-re-control px-3 py-2.5 text-sm text-re-secondary hover:bg-re-subtle hover:text-re-text">
              <BookOpen className="h-4 w-4" aria-hidden="true" />
              REALYTICS 블로그<span className="sr-only"> (새 창)</span>
            </a>
          </div>
        </details>
      </nav>
      <div className="border-t border-re-border px-3 py-3">
        {renderLink("/start", track ? `업무 선택 · ${TRACK_LABELS[track]}` : "업무 선택", Layers)}
        {renderLink("/settings", "설정", Settings)}
      </div>
    </>
  );
}

export function Sidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col overflow-y-auto border-r border-re-border bg-re-surface lg:flex">
      <Link href="/dashboard" className="px-6 py-6 text-xl font-bold tracking-tight text-re-text" aria-label="REALYTICS 대시보드">
        REAL<span className="text-re-action">YTICS</span>
      </Link>
      <NavigationContent />
    </aside>
  );
}

export function MobileNavigation() {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const dialogId = useId();
  const titleId = useId();
  const close = () => dialogRef.current?.close();

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const desktop = window.matchMedia("(min-width: 1024px)");
    const closeOnDesktop = () => { if (desktop.matches) dialogRef.current?.close(); };
    desktop.addEventListener("change", closeOnDesktop);
    return () => {
      document.body.style.overflow = previousOverflow;
      desktop.removeEventListener("change", closeOnDesktop);
    };
  }, [open]);

  return (
    <>
      <button type="button" aria-label="메뉴 열기" aria-haspopup="dialog" aria-expanded={open} aria-controls={dialogId}
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-re-control text-re-secondary hover:bg-re-subtle lg:hidden"
        onClick={() => { dialogRef.current?.showModal(); setOpen(true); }}>
        <Menu className="h-5 w-5" aria-hidden="true" />
      </button>
      <dialog ref={dialogRef} id={dialogId} aria-labelledby={titleId} onClose={() => setOpen(false)}
        onClick={(event) => { if (event.target === event.currentTarget) close(); }}
        onKeyDown={(event) => {
          if (event.key !== "Tab") return;
          const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
            "a[href], button:not([disabled]), summary, [tabindex]",
          )).filter((element) => element.tabIndex >= 0 && element.getClientRects().length > 0);
          const first = controls[0];
          const last = controls[controls.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
          }
        }}
        className="fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-80 max-w-[85vw] border-0 bg-re-surface p-0 text-re-text shadow-xl backdrop:bg-black/40">
        <div className="flex min-h-full flex-col">
          <div className="flex items-center justify-between border-b border-re-border px-4 py-3">
            <h2 id={titleId} className="text-base font-semibold">REALYTICS 메뉴</h2>
            <button type="button" onClick={close} aria-label="메뉴 닫기" autoFocus
              className="flex h-11 w-11 items-center justify-center rounded-re-control text-re-secondary hover:bg-re-subtle">
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </div>
          {open ? <NavigationContent onNavigate={close} /> : null}
        </div>
      </dialog>
    </>
  );
}
