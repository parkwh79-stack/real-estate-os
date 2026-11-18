import { DocumentReviewWorkspace } from "../../../../../components/document-review/document-review-workspace";

export default async function ProjectDocumentReviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ archiveId?: string | string[] }>;
}) {
  const [{ id }, { archiveId }] = await Promise.all([params, searchParams]);
  const requestedArchiveId = archiveId === undefined
    ? undefined
    : typeof archiveId === "string" && archiveId.length === 64 && /^[0-9a-f]{64}$/i.test(archiveId)
      ? archiveId.toLowerCase()
      : null;
  return (
    <DocumentReviewWorkspace
      key={JSON.stringify([id, requestedArchiveId])}
      projectId={id}
      requestedArchiveId={requestedArchiveId}
    />
  );
}
