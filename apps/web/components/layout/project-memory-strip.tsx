"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { AlertTriangle, CheckCircle2, Database, FileText, GitBranch, Loader2, MapPin } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../lib/workspace-context";

const T = {
  loading: "\uacf5\ud1b5 \ud504\ub85c\uc81d\ud2b8 \uae30\uc5b5\uc744 \ubd88\ub7ec\uc624\ub294 \uc911",
  memory: "\uacf5\ud1b5 \ub51c \uba54\ubaa8\ub9ac",
  project: "\ud504\ub85c\uc81d\ud2b8",
  documents: "\uc800\uc7a5 \uc790\ub8cc",
  landEvidence: "\ud1a0\uc9c0 \uc99d\ube59",
  missing: "\ubcf4\uc644 \ud544\uc694",
  decision: "\uc2b9\uc778 Gate",
  pnuReady: "PNU",
  officialPrice: "\uacf5\uc2dc\uc9c0\uac00",
  registry: "\ub4f1\uae30",
  openInput: "\uc790\ub8cc \ubcf4\uac15",
  openDealRoom: "AI \ub51c\ub8f8",
  noName: "\uc0ac\uc5c5\uba85 \ubbf8\ud655\uc778",
  unavailable: "\uacf5\ud1b5 \uba54\ubaa8\ub9ac\ub97c \ubd88\ub7ec\uc624\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.",
};

function pageFromPath(pathname: string, projectId: string) {
  const base = `/project/${projectId}`;
  const rest = pathname.startsWith(base) ? pathname.slice(base.length).replace(/^\/+/, "") : "";
  const first = rest.split("/")[0] || "overview";
  if (first === "input-package") return "input_package";
  if (first === "chief-orchestration") return "chief_orchestration";
  if (first === "pf-structure") return "pf";
  if (first === "marketability") return "marketability";
  if (first === "cashflow") return "cashflow";
  return first;
}

function formatDecision(context: WorkspacePageContext | null) {
  const decision = context?.decisionGate?.decision || context?.decisionGate?.status;
  if (!decision) return "-";
  return String(decision).replaceAll("_", " ");
}

function yesNo(value?: boolean) {
  if (value === undefined) return "-";
  return value ? "\ud655\ubcf4" : "\ubcf4\uac15";
}

export function ProjectMemoryStrip({ projectId }: { projectId: string }) {
  const pathname = usePathname() ?? "";
  const searchParams = useSearchParams();
  const page = useMemo(() => pageFromPath(pathname, projectId), [pathname, projectId]);
  const track = searchParams.get("track") ?? undefined;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchWorkspaceContext(projectId, page, track)
      .then((body) => {
        if (active) setContext(body);
      })
      .catch(() => {
        if (active) setError(T.unavailable);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, page, track]);

  const memory = context?.projectMemory;
  const pageData = context?.pageData;
  const sourceCount = memory?.sourceDocumentCount ?? pageData?.sourceDocumentSummary?.count ?? 0;
  const landEvidenceCount = memory?.landEvidenceCount ?? pageData?.landEvidenceSummary?.count ?? 0;
  const missingCount = context?.missingInputRegister?.length ?? 0;
  const landEvidence = pageData?.landEvidenceSummary;

  return (
    <div className="border-b border-slate-200 bg-slate-50/90 px-4 py-2 sm:px-6 lg:px-8 dark:border-slate-800 dark:bg-slate-950/70">
      <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-md bg-white px-2.5 py-1 text-[11px] font-black text-slate-600 ring-1 ring-slate-200 dark:bg-slate-900 dark:text-slate-300 dark:ring-slate-800">
            <Database className="h-3.5 w-3.5" />
            {T.memory}
          </span>
          {loading ? (
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {T.loading}
            </span>
          ) : error ? (
            <span className="inline-flex items-center gap-1.5 text-xs font-bold text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-3.5 w-3.5" />
              {error}
            </span>
          ) : (
            <>
              <span className="max-w-[360px] truncate text-xs font-black text-slate-900 dark:text-white">
                {displayText(memory?.projectName, `${T.project} #${projectId}`)}
              </span>
              <Fact icon={<FileText className="h-3.5 w-3.5" />} label={T.documents} value={String(sourceCount)} />
              <Fact icon={<MapPin className="h-3.5 w-3.5" />} label={T.landEvidence} value={String(landEvidenceCount)} />
              <Fact label={T.pnuReady} value={yesNo(landEvidence?.hasPnuResolution)} />
              <Fact label={T.officialPrice} value={yesNo(landEvidence?.hasOfficialLandPrice)} />
              <Fact label={T.registry} value={yesNo(landEvidence?.hasRegistryEvidence)} />
              <Fact
                icon={missingCount ? <AlertTriangle className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                label={T.missing}
                value={String(missingCount)}
                tone={missingCount ? "warn" : "good"}
              />
              <Fact label={T.decision} value={formatDecision(context)} />
            </>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link
            href={`/project/${projectId}/input-package`}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <FileText className="h-3.5 w-3.5" />
            {T.openInput}
          </Link>
          <Link
            href={`/project/${projectId}/chief-orchestration`}
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-slate-950 px-2.5 text-xs font-bold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950"
          >
            <GitBranch className="h-3.5 w-3.5" />
            {T.openDealRoom}
          </Link>
        </div>
      </div>
    </div>
  );
}

function Fact({
  icon,
  label,
  value,
  tone,
}: {
  icon?: ReactNode;
  label: string;
  value: string;
  tone?: "good" | "warn";
}) {
  const toneClass =
    tone === "good"
      ? "text-emerald-700 dark:text-emerald-300"
      : tone === "warn"
        ? "text-amber-700 dark:text-amber-300"
        : "text-slate-700 dark:text-slate-200";
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-bold ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
      {icon}
      <span className="text-slate-400">{label}</span>
      <span className={toneClass}>{value}</span>
    </span>
  );
}
