"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  BookMarked,
  BookOpen,
  BrainCircuit,
  Building2,
  FileCheck,
  FolderKanban,
  GitBranch,
  Landmark,
  LayoutDashboard,
  Layers,
  LibraryBig,
  Map,
  Settings,
  Wallet,
} from "lucide-react";
import { getStoredWorkflowTrack, TRACK_LABELS, type WorkflowTrack } from "../../lib/workflow-track";

const BLOG_URL = "https://blog.naver.com/babo7910/224295847211";

const navItems = [
  { key: "start", href: "/", label: "OS 시작", icon: LayoutDashboard },
  { key: "projects", href: "/dashboard", label: "프로젝트 관리", icon: FolderKanban },
  { key: "deal-room", href: "/dashboard", label: "AI 딜룸", icon: GitBranch },
  { key: "templates", href: "/templates", label: "업무 템플릿", icon: LibraryBig },
  { key: "glossary", href: "/glossary", label: "표준 용어", icon: BookOpen },
  { key: "analysis", href: "/analysis", label: "토지·거래 분석", icon: Map },
  { key: "feasibility", href: "/feasibility", label: "사업성 분석", icon: BarChart3 },
  { key: "cashflow", href: "/cashflow-detail", label: "CF 대시보드", icon: Wallet },
  { key: "finance", href: "/finance", label: "금융구조", icon: Layers },
  { key: "report", href: "/report", label: "PF 심사보고서", icon: FileCheck },
  { key: "monitoring", href: "/monitoring", label: "실시간 모니터링", icon: Activity },
  { key: "term-review", href: "/glossary/candidates", label: "용어 검토", icon: BookMarked },
  { key: "settings", href: "/settings", label: "AI 설정", icon: Settings },
];

const TRACK_MENU_KEYS: Record<WorkflowTrack, string[]> = {
  development: ["start", "projects", "deal-room", "analysis", "feasibility", "cashflow", "report", "glossary", "settings"],
  pf: ["start", "projects", "deal-room", "finance", "cashflow", "report", "monitoring", "term-review", "settings"],
  finance: ["start", "projects", "deal-room", "finance", "cashflow", "report", "monitoring", "glossary", "settings"],
  real_asset_finance: ["start", "projects", "deal-room", "analysis", "finance", "report", "monitoring", "glossary", "settings"],
  reit: ["start", "projects", "deal-room", "analysis", "finance", "report", "monitoring", "term-review", "settings"],
  fund: ["start", "projects", "deal-room", "templates", "finance", "cashflow", "report", "monitoring", "settings"],
};

const TRACK_DESCRIPTIONS: Record<WorkflowTrack, string> = {
  development: "토지, 인허가, 상품기획, 사업수지, 현금흐름 중심",
  pf: "사업수지, Tranche, 상환재원, 리스크, 심사보고서 중심",
  finance: "담보, 리파이낸싱, 대출구조, 회수 가능성 중심",
  real_asset_finance: "자산 실사, 임대/시장 증빙, 등기·권리관계, 인수 리스크 중심",
  reit: "편입자산, 배당재원, 감정가, 운용구조와 공시 리스크 중심",
  fund: "펀드 설정, 투자대상, 대출/에쿼티 구조, 운용·회수 리스크 중심",
};

const TRACK_ICONS: Record<WorkflowTrack, typeof Building2> = {
  development: Building2,
  pf: Landmark,
  finance: Wallet,
  real_asset_finance: BarChart3,
  reit: Layers,
  fund: Wallet,
};

export function Sidebar() {
  const pathname = usePathname();
  const [track, setTrack] = useState<WorkflowTrack | null>(null);
  const currentProjectId = pathname.match(/^\/project\/([^/]+)/)?.[1];

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
    const base = track ? navItems.filter((item) => TRACK_MENU_KEYS[track].includes(item.key)) : navItems;
    return currentProjectId ? base : base.filter((item) => item.key !== "deal-room");
  }, [track, currentProjectId]);

  const TrackIcon = track ? TRACK_ICONS[track] : BrainCircuit;

  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white lg:flex lg:flex-col dark:border-slate-800 dark:bg-slate-950">
      <div className="border-b border-slate-200 px-5 py-5 dark:border-slate-800">
        <Link href="/" className="block">
          <span className="text-2xl font-black tracking-tight text-slate-950 dark:text-white">
            REAL<span className="text-indigo-600">YTICS</span>
          </span>
          <p className="mt-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">
            REALYTICS x PLATFORM9 · powered by WAN2
          </p>
        </Link>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4">
        {track ? (
          <div className="mb-3 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-3 dark:border-indigo-900/60 dark:bg-indigo-950/30">
            <div className="flex items-center gap-2">
              <TrackIcon className="h-4 w-4 text-indigo-700 dark:text-indigo-300" />
              <p className="text-xs font-black text-indigo-700 dark:text-indigo-300">
                선택 트랙: {TRACK_LABELS[track]}
              </p>
            </div>
            <p className="mt-2 text-[11px] font-semibold leading-4 text-indigo-600/80 dark:text-indigo-300/80">
              {TRACK_DESCRIPTIONS[track]}
            </p>
          </div>
        ) : null}

        {visibleItems.map((item) => {
          const Icon = item.icon;
          const href =
            item.key === "deal-room" && currentProjectId
              ? `/project/${currentProjectId}/chief-orchestration`
              : item.href;
          const isActive =
            item.key === "deal-room"
              ? Boolean(currentProjectId && pathname.startsWith(`/project/${currentProjectId}/chief-orchestration`))
              : item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
          return (
            <Link
              key={item.key}
              href={href}
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
        <p className="mt-3 font-semibold text-slate-700 dark:text-slate-300">AI Deal Room</p>
        <p className="mt-1">트랙별 Agent Pod와 사용자 LLM 설정을 기준으로 실행합니다.</p>
      </div>
    </aside>
  );
}
