"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  FileCheck,
  FolderKanban,
  LayoutDashboard,
  Layers,
  Map,
  Wallet,
} from "lucide-react";

const navItems = [
  { href: "/", label: "대시보드", icon: LayoutDashboard },
  { href: "/dashboard", label: "프로젝트 관리", icon: FolderKanban },
  { href: "/analysis", label: "토지/거래 분석", icon: Map },
  { href: "/feasibility", label: "사업성 분석", icon: BarChart3 },
  { href: "/cashflow-detail", label: "CF 스케줄", icon: Wallet },
  { href: "/finance", label: "금융구조", icon: Layers },
  { href: "/report", label: "PF 심사 보고서", icon: FileCheck },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white lg:flex lg:flex-col dark:border-slate-800 dark:bg-slate-950">
      <div className="border-b border-slate-200 px-5 py-5 dark:border-slate-800">
        <Link href="/" className="block">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
            REALYTICS
          </p>
          <h1 className="mt-1 text-2xl font-black tracking-tight text-slate-950 dark:text-white">
            PLATFORM9
          </h1>
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
        <p className="font-semibold text-slate-700 dark:text-slate-300">Alpha workspace</p>
        <p className="mt-1">PF 검토, 현금흐름, 리포트 생성</p>
      </div>
    </aside>
  );
}
