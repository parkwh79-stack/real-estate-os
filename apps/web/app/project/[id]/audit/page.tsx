import { ArrowLeft } from "lucide-react";
import { ActivityLogPanel } from "../../../../components/audit/activity-log-panel";
import { ProjectWorkspaceLink } from "../../../../components/layout/project-workspace-link";

export default async function ProjectAuditPage({
  params,
}: {
  params: Promise<{ id: string }> | { id: string };
}) {
  const resolvedParams = await params;

  return (
    <div className="space-y-6 pb-8">
      <ProjectWorkspaceLink
        projectId={resolvedParams.id}
        path="report"
        className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        심사보고서로 돌아가기
      </ProjectWorkspaceLink>
      <ActivityLogPanel
        projectId={resolvedParams.id}
        title="Project Audit Log"
        description="Project-scoped user and server events for diagnosing blocked pilot runs, evidence access, report exports, and approval workflow issues."
      />
    </div>
  );
}
