"use client";

import type { ReactNode } from "react";
import { Suspense } from "react";
import { usePathname } from "next/navigation";
import { RequireAuth, RequireSupabaseSession } from "../auth/auth-context";
import { Header } from "./header";
import { ProjectContextHeader } from "./project-context-header";
import { Sidebar } from "./sidebar";

interface ShellProps {
  children: ReactNode;
}

export function Shell({ children }: ShellProps) {
  const pathname = usePathname();
  const isPublicRoute = Boolean(
    pathname?.startsWith("/login")
      || pathname?.startsWith("/auth")
      || pathname?.startsWith("/guest"),
  );
  const isOnboardingRoute = Boolean(pathname?.startsWith("/onboarding"));
  const isFocusRoute = Boolean(pathname?.startsWith("/start"));
  const projectMatch = pathname?.match(/^\/project\/([^/]+)/);
  const currentProjectId = projectMatch?.[1];

  if (isPublicRoute) {
    return <>{children}</>;
  }

  if (isOnboardingRoute) {
    return <RequireSupabaseSession>{children}</RequireSupabaseSession>;
  }

  if (isFocusRoute) {
    return <RequireAuth>{children}</RequireAuth>;
  }

  return (
    <RequireAuth>
      <div className="min-h-screen bg-slate-50 text-slate-950 dark:bg-slate-950 dark:text-slate-100">
        <Sidebar />
        <div className="min-h-screen lg:pl-64">
          <Header />
          {currentProjectId ? (
            <Suspense fallback={null}>
              <ProjectContextHeader projectId={currentProjectId} />
            </Suspense>
          ) : null}
          <main className="mx-auto w-full max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8">
            {children}
          </main>
        </div>
      </div>
    </RequireAuth>
  );
}
