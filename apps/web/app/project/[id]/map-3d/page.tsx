"use client";

import Link from "next/link";
import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowLeft, Box, Loader2 } from "lucide-react";
import { authHeaders } from "../../../../lib/auth-client";

const apiBase = "http://127.0.0.1:8000";
const VWORLD_KEY = process.env.NEXT_PUBLIC_VWORLD_API_KEY;
const SEOUL: [number, number] = [126.9779, 37.5663];

// VWorld WebGL globals (loaded by the SDK script).
declare global {
  interface Window {
    vw?: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  }
}

export default function Project3DMapPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [center, setCenter] = useState<[number, number]>(SEOUL);
  const [scriptLoaded, setScriptLoaded] = useState(false);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const startedRef = useRef(false);

  // Center the 3D map on the project's parcel, if resolvable.
  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch(`${apiBase}/projects/${projectId}/map?resolve_geometry=true`, {
          cache: "no-store",
          headers: authHeaders("web"),
        });
        if (res.ok) {
          const data = await res.json();
          if (data?.center?.lng && data?.center?.lat) setCenter([data.center.lng, data.center.lat]);
        }
      } catch {
        /* fall back to Seoul */
      }
    })();
  }, [projectId]);

  const startMap = useCallback(() => {
    const vw = window.vw;
    if (startedRef.current || !vw || !vw.Map) return false;
    try {
      startedRef.current = true;
      const map = new vw.Map();
      map.setOption({
        mapId: "vmap",
        initPosition: new vw.CameraPosition(
          new vw.CoordZ(center[0], center[1], 1800),
          new vw.Direction(0, -55, 0),
        ),
        logo: true,
        navigation: true,
      });
      map.setMapId("vmap");
      map.start();
      setStatus("ready");
      return true;
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "3D 지도 초기화 실패");
      setStatus("error");
      return true;
    }
  }, [center]);

  // Poll for the vw global after the script loads (Cesium loads async).
  useEffect(() => {
    if (!scriptLoaded) return;
    if (startMap()) return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (startMap()) {
        window.clearInterval(timer);
      } else if (Date.now() - started > 20000) {
        window.clearInterval(timer);
        setErrorMsg("VWorld 3D 엔진 로드 시간 초과 (vw 전역 미준비)");
        setStatus("error");
      }
    }, 400);
    return () => window.clearInterval(timer);
  }, [scriptLoaded, startMap]);

  return (
    <div className="space-y-4">
      <header className="flex items-center gap-3">
        <Link
          href={`/project/${projectId}/marketability`}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
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
        <>
          <div className="relative h-[640px] overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
            <div id="vmap" className="h-full w-full" />
            {status !== "ready" ? (
              <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-50/90 text-sm font-semibold text-slate-600 dark:bg-slate-950/80 dark:text-slate-300">
                {status === "error" ? (
                  <span className="px-4 text-center text-rose-600 dark:text-rose-300">3D 지도 오류: {errorMsg}</span>
                ) : (
                  <span className="flex items-center gap-2">
                    <Loader2 className="h-5 w-5 animate-spin" /> 3D 엔진 로딩 중…
                  </span>
                )}
              </div>
            ) : null}
          </div>
          <Script
            src={`https://map.vworld.kr/js/webglMapInit.js.do?version=3.0&apiKey=${VWORLD_KEY}`}
            strategy="afterInteractive"
            onLoad={() => setScriptLoaded(true)}
            onError={() => {
              setErrorMsg("VWorld 3D 스크립트 로드 실패");
              setStatus("error");
            }}
          />
        </>
      )}
    </div>
  );
}
