"use client";

import { useRouter } from "next/navigation";
import { UserCircle } from "lucide-react";
import { useAuth } from "../auth/auth-context";

const DEFAULT = { name: "게스트 심사역", role: "PLATFORM9 Alpha" };

/** Shows the logged-in member from the shared auth context, not a hardcoded name. */
export function HeaderUser() {
  const router = useRouter();
  const { user, logout } = useAuth();

  const u = user
    ? {
        name: user.displayName || user.userId,
        role: user.role ? `${user.role} · PLATFORM9` : "PLATFORM9 Alpha",
      }
    : DEFAULT;

  return (
    <div className="hidden items-center gap-2 border-l border-slate-200 pl-3 sm:flex dark:border-slate-800">
      <UserCircle className="h-8 w-8 text-slate-500" />
      <div className="text-sm leading-tight">
        <p className="font-semibold text-slate-900 dark:text-white">{u.name}</p>
        <p className="text-xs text-slate-500">{u.role}</p>
      </div>
      <button
        type="button"
        onClick={() => {
          void logout().finally(() => router.replace("/login"));
        }}
        className="ml-2 rounded-md border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
      >
        로그아웃
      </button>
    </div>
  );
}
