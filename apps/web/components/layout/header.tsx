import Image from "next/image";
import Link from "next/link";
import {
  Bell,
  BookOpen,
  CircleHelp,
  ShieldCheck,
  UserCircle,
} from "lucide-react";
import { HeaderSearch } from "./header-search";

const BLOG_URL = "https://blog.naver.com/babo7910/224295847211";

export function Header() {
  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur sm:px-6 lg:px-8 dark:border-slate-800 dark:bg-slate-950/90">
      <div className="mx-auto flex w-full max-w-[1600px] items-center justify-between gap-4">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Link href="/" className="shrink-0 lg:hidden" aria-label="REALYTICS 홈">
            <Image
              src="/brand/realytics-logo.png"
              alt="REALYTICS"
              width={120}
              height={40}
              priority
              className="h-8 w-auto max-w-[120px] object-contain"
            />
          </Link>
          <HeaderSearch />
        </div>

        <div className="flex items-center gap-2">
          <div className="hidden items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-700 md:flex dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
            <ShieldCheck className="h-4 w-4" />
            VWorld · 실거래가 연동
          </div>
          <a
            href={BLOG_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-900 dark:hover:text-white"
            aria-label="REALYTICS 블로그"
            title="REALYTICS 블로그"
          >
            <BookOpen className="h-5 w-5" />
          </a>
          <button
            type="button"
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-900 dark:hover:text-white"
            aria-label="도움말"
          >
            <CircleHelp className="h-5 w-5" />
          </button>
          <button
            type="button"
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-900 dark:hover:text-white"
            aria-label="알림"
          >
            <Bell className="h-5 w-5" />
          </button>
          <div className="hidden items-center gap-2 border-l border-slate-200 pl-3 sm:flex dark:border-slate-800">
            <UserCircle className="h-8 w-8 text-slate-500" />
            <div className="text-sm leading-tight">
              <p className="font-semibold text-slate-900 dark:text-white">심사역 김철수</p>
              <p className="text-xs text-slate-500">PLATFORM9 Alpha</p>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
