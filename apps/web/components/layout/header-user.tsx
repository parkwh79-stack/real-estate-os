"use client";

import { useEffect, useState } from "react";
import { UserCircle } from "lucide-react";
import { getStoredAuthSession } from "../../lib/auth-client";

const DEFAULT = { name: "게스트 심사역", role: "PLATFORM9 Alpha" };

/** Shows the logged-in member (from the stored auth session), not a hardcoded name. */
export function HeaderUser() {
  const [u, setU] = useState(DEFAULT);

  useEffect(() => {
    const stored = getStoredAuthSession();
    if (stored?.user) {
      setU({
        name: stored.user.displayName || stored.user.userId,
        role: stored.user.role ? `${stored.user.role} · PLATFORM9` : "PLATFORM9 Alpha",
      });
    }
  }, []);

  return (
    <div className="hidden items-center gap-2 border-l border-slate-200 pl-3 sm:flex dark:border-slate-800">
      <UserCircle className="h-8 w-8 text-slate-500" />
      <div className="text-sm leading-tight">
        <p className="font-semibold text-slate-900 dark:text-white">{u.name}</p>
        <p className="text-xs text-slate-500">{u.role}</p>
      </div>
    </div>
  );
}
