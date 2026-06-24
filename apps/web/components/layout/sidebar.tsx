"use client";

import Image from "next/image";
import Link from "next/link";
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

const BLOG_URL = "https://blog.naver.com/babo7910/224295847211";

const navItems = [
  { href: "/", label: "대시보드", icon: LayoutDashboard },
  { href: "/dashboard", label: "프로젝트 관리", icon: FolderKanban },
  { href: "/templates", label: "업무 템플릿", icon: LibraryBig },
  { href: "/glossary", label: "표준 용어", icon: BookOpen },
  { href: "/analysis", label: "토지·거래 분석", icon: Map },
  { href: "/feasibility", label: "사업성 분석", icon: BarChart3 },
  { href: "/cashflow-detail", label: "CF 대시보드", icon: Wallet },
  { href: "/finance", label: "금융구조", icon: Layers },
  { href: "/report", label: "PF 심사보고서", icon: FileCheck },
  { href: "/monitoring", label: "실시간 모니터링", icon: Activity },
  { href: "/glossary/candidates", label: "용어 검토", icon: BookMarked },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white lg:flex lg:flex-col dark:border-slate-800 dark:bg-slate-950">
      <div className="border-b border-slate-200 px-5 py-5 dark:border-slate-800">
        <Link href="/" className="block">
          <Image
            src="/brand/realytics-logo.png"
            alt="REALYTICS — powered by WAN2"
            width={200}
            height={64}
            priority
            className="h-12 w-auto object-contain"
          />
          <p className="mt-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">
            REALYTICS × PLATFORM9 · powered by WAN2
          </p>
        </Link>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4">
        {navItems.map((item) => {
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
