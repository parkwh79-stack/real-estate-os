import type { ReactNode } from "react";
import { Header } from "./header";
import { Sidebar } from "./sidebar";

interface ShellProps {
  children: ReactNode;
}

export function Shell({ children }: ShellProps) {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-950 dark:bg-slate-950 dark:text-slate-100">
      <Sidebar />
      <div className="min-h-screen lg:pl-64">
        <Header />
        <main className="mx-auto w-full max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
