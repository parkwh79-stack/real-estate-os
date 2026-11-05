"use client";

import { useParams } from "next/navigation";
import { ChiefDealRoomMap } from "../../../../components/orchestration/chief-deal-room-map";

export default function ChiefOrchestrationPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = String(id);
  return <ChiefDealRoomMap key={projectId} projectId={projectId} view="workspace" />;
}
