"use client";

import { useParams } from "next/navigation";
import { DocumentReviewWorkspace } from "../../../../../components/document-review/document-review-workspace";

export default function ProjectDocumentReviewPage() {
  const params = useParams<{ id: string }>();
  return <DocumentReviewWorkspace projectId={String(params.id)} />;
}
