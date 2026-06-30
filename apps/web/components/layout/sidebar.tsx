"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  BookMarked,
  BookOpen,
  FileCheck,
  FolderKanban,
  LayoutDashboard,
  Layers,
  LibraryBig,
  Map,
  Wallet,
} from "lucide-react";
import { getStoredWorkflowTrack, TRACK_LABELS, type WorkflowTrack } from "../../lib/workflow-track";

const BLOG_URL = "https://blog.naver.com/babo7910/224295847211";

const navItems = [
  { key: "dashboard", href: "/", label: "OS 런처", icon: LayoutDashboard },
  { key: "projects", href: "/dashboard", label: "프로젝트 관리", icon: FolderKanban },
  { key: "templates", href: "/templates", label: "업무 템플릿", icon: LibraryBig },
  { key: "glossary", href: "/glossary", label: "표준 용어", icon: BookOpen },
  { key: "analysis", href: "/analysis", label: "토지·거래 분석", icon: Map },
  { key: "feasibility", href: "/feasibility", label: "사업성 분석", icon: BarChart3 },
  { key: "cashflow", href: "/cashflow-detail", label: "CF 대시보드", icon: Wallet },
  { key: "finance", href: "/finance", label: "금융구조", icon: Layers },
  { key: "report", href: "/report", label: "PF 심사보고서", icon: FileCheck },
  { key: "monitoring", href: "/monitoring", label: "실시간 모니터링", icon: Activity },
  { key: "term-review", href: "/glossary/candidates", label: "용어 검토", icon: BookMarked },
];

const TRACK_MENU_KEYS: Record<WorkflowTrack, string[]> = {
  development: ["dashboard", "projects", "templates", "analysis", "feasibility", "cashflow", "report", "glossary"],
  pf: ["dashboard", "projects", "finance", "cashflow", "report", "monitoring", "glossary", "term-review"],
  finance: ["dashboard", "projects", "finance", "cashflow", "report", "monitoring", "glossary"],
  real_asset_finance: ["dashboard", "projects", "analysis", "finance", "report", "monitoring", "glossary"],
  reit: ["dashboard", "projects", "analysis", "finance", "report", "monitoring", "glossary", "term-review"],
  fund: ["dashboard", "projects", "templates", "finance", "cashflow", "report", "monitoring", "glossary"],
};

const TRACK_DESCRIPTIONS: Record<WorkflowTrack, string> = {
  development: "토지·인허가·사업수지",
  pf: "트랜치·상환·심사보고서",
  finance: "담보·리파이낸싱·대출구조",
  real_asset_finance: "임대·감정·실물인수",
  reit: "편입자산·배당·공시",
  fund: "집합투자·운용·회수",
};

export function Sidebar() {
  const pathname = usePathname();
  const [track, setTrack] = useState<WorkflowTrack | null>(null);

  useEffect(() => {
    const read = () => setTrack(getStoredWorkflowTrack());
    read();
    window.addEventListener("storage", read);
    window.addEventListener("realytics-workflow-track-changed", read);
    return () => {
      window.removeEventListener("storage", read);
      window.removeEventListener("realytics-workflow-track-changed", read);
    };
  }, []);

  const visibleItems = useMemo(() => {
    if (!track) return navItems;
    const allowed = new Set(TRACK_MENU_KEYS[track]);
    return navItems.filter((item) => allowed.has(item.key));
  }, [track]);

  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white lg:flex lg:flex-col dark:border-slate-800 dark:bg-slate-950">
      <div className="border-b border-slate-200 px-5 py-5 dark:border-slate-800">
        <Link href="/" className="block">
          <span className="text-2xl font-black tracking-tight text-slate-950 dark:text-white">
            REAL<span className="text-indigo-600">YTICS</span>
          </span>
          <p className="mt-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">
            REALYTICS × PLATFORM9 · powered by WAN2
          </p>
        </Link>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4">
        {track ? (
          <div className="mb-3 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 dark:border-indigo-900/60 dark:bg-indigo-950/30">
            <p className="text-xs font-black text-indigo-700 dark:text-indigo-300">선택 트랙: {TRACK_LABELS[track]}</p>
            <p className="mt-1 text-[11px] font-semibold leading-4 text-indigo-600/80 dark:text-indigo-300/80">
              {TRACK_DESCRIPTIONS[track]}
            </p>
          </div>
        ) : null}
        {visibleItems.map((item) => {
          const Icon = item.icon;
          const isActive =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold transition-colors ${
                isActive
                  ? "bg-slate-950 text-white dark:bg-white dark:text-slate-950"
                  : "text-slate-600 hover:bg-slate-100 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-900 dark:hover:text-white"
              }`}
            >
              <Icon className="h-4 w-4" />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-slate-200 p-4 text-xs text-slate-500 dark:border-slate-800">
        <a
          href={BLOG_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 rounded-lg bg-slate-100 px-3 py-2 font-bold text-slate-700 transition-colors hover:bg-slate-200 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
        >
          <BookOpen className="h-4 w-4" />
          REALYTICS 블로그
        </a>
        <p className="mt-3 font-semibold text-slate-700 dark:text-slate-300">Alpha workspace</p>
        <p className="mt-1">PF 심사, 현금흐름, 리스크 보고서 자동화</p>
      </div>
    </aside>
  );
}
