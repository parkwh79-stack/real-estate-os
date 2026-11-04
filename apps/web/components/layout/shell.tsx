"use client";

import type { ReactNode } from "react";
import { Suspense } from "react";
import { usePathname } from "next/navigation";
import { projectIdFromLocation } from "../../lib/project-navigation";
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
  const currentProjectId = projectIdFromLocation(pathname ?? "", "");

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
      <div className="min-h-screen bg-re-canvas text-re-text">
        <a href="#workspace-main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-2 focus:z-50 focus:rounded-re-control focus:bg-re-surface focus:px-4 focus:py-2">
          본문 바로가기
        </a>
        <Sidebar />
        <div className="min-h-screen lg:pl-64">
          <Header />
          <main id="workspace-main" tabIndex={-1} className="mx-auto w-full max-w-[1600px] scroll-mt-16 px-4 py-4 sm:px-6 lg:px-8 lg:py-5">
            {currentProjectId ? (
              <Suspense fallback={null}>
                <ProjectContextHeader key={currentProjectId} projectId={currentProjectId} />
              </Suspense>
            ) : null}
            {children}
          </main>
        </div>
      </div>
    </RequireAuth>
  );
}
