"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { RequireAuth } from "../auth/auth-context";
import { Header } from "./header";
import { Sidebar } from "./sidebar";

interface ShellProps {
  children: ReactNode;
}

export function Shell({ children }: ShellProps) {
  const pathname = usePathname();
  const isPublicRoute = Boolean(pathname?.startsWith("/login") || pathname?.startsWith("/auth"));
  // Focus/launcher routes are authenticated but render full-screen WITHOUT the
  // sidebar/header chrome — the "AI OS boot" command center before a track is chosen.
  const isFocusRoute = Boolean(pathname?.startsWith("/start"));

  // Public routes (login, OAuth callbacks) render without the app chrome or the auth gate.
  if (isPublicRoute) {
    return <>{children}</>;
  }

  // Focus routes: gated, but no sidebar/header — the page paints its own full screen.
  if (isFocusRoute) {
    return <RequireAuth>{children}</RequireAuth>;
  }

  // Everything else is gated by the single RequireAuth guard, which redirects
  // unauthenticated users to /login and shows the fallback while checking.
  return (
    <RequireAuth>
      <div className="min-h-screen bg-slate-50 text-slate-950 dark:bg-slate-950 dark:text-slate-100">
        <Sidebar />
        <div className="min-h-screen lg:pl-64">
          <Header />
          <main className="mx-auto w-full max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8">
            {children}
          </main>
        </div>
      </div>
    </RequireAuth>
  );
}

