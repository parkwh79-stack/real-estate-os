import { ActivityLogPanel } from "../../components/audit/activity-log-panel";

export default async function AuditPage() {
  return (
    <div className="pb-8">
      <ActivityLogPanel
        title="Workspace Audit Log"
        description="Workspace-wide troubleshooting view for login events, API requests, report exports, and document download activity."
      />
    </div>
  );
}
