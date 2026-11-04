"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowLeft, Box } from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { ProjectWorkspaceLink } from "../../../../components/layout/project-workspace-link";
const VWORLD_KEY = process.env.NEXT_PUBLIC_VWORLD_API_KEY;
const SEOUL: [number, number] = [126.9779, 37.5663];

export default function Project3DMapPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [center, setCenter] = useState<[number, number] | null>(null);

  // Center the 3D map on the project's parcel, if resolvable.
  useEffect(() => {
    void (async () => {
      let c = SEOUL;
      try {
        const res = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/map?resolve_geometry=true`, {
          cache: "no-store",
          headers: authHeaders("web"),
        });
        if (res.ok) {
          const data = await res.json();
          if (data?.center?.lng && data?.center?.lat) c = [data.center.lng, data.center.lat];
        }
      } catch {
        /* fall back to Seoul */
      }
      setCenter(c);
    })();
  }, [projectId]);

  // The VWorld WebGL SDK uses document.write to load Cesium, which the browser
  // blocks for async-loaded scripts. So we embed a self-contained HTML document
  // (served by /vworld-3d) in an iframe, where the SDK script runs during the
  // iframe's own initial parse and document.write works.
  const iframeSrc = center ? `/vworld-3d?lng=${center[0]}&lat=${center[1]}` : null;

  return (
    <div className="space-y-4">
      <header className="flex items-center gap-3">
        <ProjectWorkspaceLink
          projectId={projectId}
          path="map"
          aria-label="지도로 돌아가기"
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        </ProjectWorkspaceLink>
        <div className="flex items-center gap-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-900 text-white dark:bg-white dark:text-slate-950">
            <Box className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-xl font-black text-slate-950 dark:text-white">3D 지도 (VWorld WebGL)</h1>
            <p className="text-xs font-semibold text-slate-500">국토교통부 공간정보 오픈플랫폼 · 3D 건물</p>
          </div>
        </div>
      </header>

      {!VWORLD_KEY ? (
        <p className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          VWorld 키(NEXT_PUBLIC_VWORLD_API_KEY)가 설정되어야 3D 지도를 표시할 수 있습니다.
        </p>
      ) : (
        <div className="h-[640px] overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
          {iframeSrc ? (
            <iframe key={iframeSrc} src={iframeSrc} title="VWorld 3D" className="h-full w-full border-0" />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-slate-500">위치 확인 중…</div>
          )}
        </div>
      )}
    </div>
  );
}
