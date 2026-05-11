import {
  Bell,
  CircleHelp,
  Search,
  ShieldCheck,
  UserCircle,
} from "lucide-react";

export function Header() {
  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur sm:px-6 lg:px-8 dark:border-slate-800 dark:bg-slate-950/90">
      <div className="mx-auto flex w-full max-w-[1600px] items-center justify-between gap-4">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-950 text-white lg:hidden dark:bg-white dark:text-slate-950">
            P9
          </div>
          <label className="flex h-10 w-full max-w-xl items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900">
            <Search className="h-4 w-4" />
            <input
              type="text"
              placeholder="주소, PNU, 프로젝트 검색"
              className="w-full bg-transparent text-slate-900 outline-none placeholder:text-slate-400 dark:text-white"
            />
          </label>
        </div>

        <div className="flex items-center gap-2">
          <div className="hidden items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-700 md:flex dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
            <ShieldCheck className="h-4 w-4" />
            API mock ready
          </div>
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
