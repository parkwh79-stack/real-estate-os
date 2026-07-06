"use client";

import type { ReactNode } from "react";
import { Suspense } from "react";
import { usePathname } from "next/navigation";
import { RequireAuth } from "../auth/auth-context";
import { Header } from "./header";
import { ProjectMemoryStrip } from "./project-memory-strip";
import { ProjectWorkspaceNav } from "./project-workspace-nav";
import { Sidebar } from "./sidebar";

interface ShellProps {
  children: ReactNode;
}

export function Shell({ children }: ShellProps) {
  const pathname = usePathname();
  const isPublicRoute = Boolean(pathname?.startsWith("/login") || pathname?.startsWith("/auth"));
  const isFocusRoute = Boolean(pathname?.startsWith("/start"));
  const projectMatch = pathname?.match(/^\/project\/([^/]+)/);
  const currentProjectId = projectMatch?.[1];

  if (isPublicRoute) {
    return <>{children}</>;
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
          {currentProjectId ? <ProjectWorkspaceNav projectId={currentProjectId} /> : null}
          {currentProjectId ? (
            <Suspense fallback={null}>
              <ProjectMemoryStrip projectId={currentProjectId} />
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
