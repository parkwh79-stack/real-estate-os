"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Boxes,
  FileSearch,
  FileText,
  GitBranch,
  Landmark,
  Map,
  Scale,
  ShieldAlert,
  Upload,
  Wallet,
} from "lucide-react";

const LABEL = {
  overview: "\uac1c\uc694",
  input: "\uc790\ub8cc \uc785\ub825",
  dealRoom: "AI \ub51c\ub8f8",
  land: "\ud1a0\uc9c0",
  map: "\uc9c0\ub3c4",
  market: "\uc2dc\uc7a5\uc131",
  feasibility: "\uc0ac\uc5c5\uc131",
  assetComparison: "\uc790\uc0b0\ube44\uad50",
  cashflow: "CF",
  pf: "\uae08\uc735\uad6c\uc870",
  risk: "\ub9ac\uc2a4\ud06c",
  evidence: "\uadfc\uac70",
  report: "\ubcf4\uace0\uc11c",
  project: "\ud504\ub85c\uc81d\ud2b8",
};

const PROJECT_STEPS = [
  { key: "overview", label: LABEL.overview, href: "", icon: Boxes },
  { key: "input", label: LABEL.input, href: "input-package", icon: Upload },
  { key: "deal-room", label: LABEL.dealRoom, href: "chief-orchestration", icon: GitBranch },
  { key: "land", label: LABEL.land, href: "land", icon: Landmark },
  { key: "map", label: LABEL.map, href: "map", icon: Map },
  { key: "market", label: LABEL.market, href: "marketability", icon: BarChart3 },
  { key: "feasibility", label: LABEL.feasibility, href: "feasibility", icon: BarChart3 },
  { key: "asset-comparison", label: LABEL.assetComparison, href: "asset-comparison", icon: Scale },
  { key: "cashflow", label: LABEL.cashflow, href: "cashflow", icon: Wallet },
  { key: "pf", label: LABEL.pf, href: "pf-structure", icon: Landmark },
  { key: "risk", label: LABEL.risk, href: "risk", icon: ShieldAlert },
  { key: "evidence", label: LABEL.evidence, href: "evidence", icon: FileSearch },
  { key: "report", label: LABEL.report, href: "report", icon: FileText },
];

function projectUrl(projectId: string, href: string) {
  return href ? `/project/${projectId}/${href}` : `/project/${projectId}`;
}

function isActive(pathname: string, projectId: string, href: string) {
  const url = projectUrl(projectId, href);
  if (!href) return pathname === url;
  return pathname === url || pathname.startsWith(`${url}/`);
}

export function ProjectWorkspaceNav({ projectId }: { projectId: string }) {
  const pathname = usePathname() ?? "";

  return (
    <div className="border-b border-slate-200 bg-white/85 px-4 py-2 backdrop-blur sm:px-6 lg:px-8 dark:border-slate-800 dark:bg-slate-950/85">
      <div className="mx-auto flex w-full max-w-[1600px] items-center gap-2 overflow-x-auto">
        <span className="mr-1 shrink-0 rounded-md bg-slate-100 px-2 py-1 text-[11px] font-black text-slate-600 dark:bg-slate-900 dark:text-slate-300">
          {LABEL.project} #{projectId}
        </span>
        {PROJECT_STEPS.map((item) => {
          const Icon = item.icon;
          const active = isActive(pathname, projectId, item.href);
          return (
            <Link
              key={item.key}
              href={projectUrl(projectId, item.href)}
              className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-xs font-bold transition-colors ${
                active
                  ? "bg-slate-950 text-white dark:bg-white dark:text-slate-950"
                  : "text-slate-600 hover:bg-slate-100 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-900 dark:hover:text-white"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {item.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
