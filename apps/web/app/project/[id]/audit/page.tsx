import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { ActivityLogPanel } from "../../../../components/audit/activity-log-panel";

export default async function ProjectAuditPage({
  params,
}: {
  params: Promise<{ id: string }> | { id: string };
}) {
  const resolvedParams = await params;

  return (
    <div className="space-y-6 pb-8">
      <Link
        href={`/project/${resolvedParams.id}`}
        className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to project
      </Link>
      <ActivityLogPanel
        projectId={resolvedParams.id}
        title="Project Audit Log"
        description="Project-scoped user and server events for diagnosing blocked pilot runs, evidence access, report exports, and approval workflow issues."
      />
    </div>
  );
}
