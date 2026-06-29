import { NextRequest } from "next/server";

// Serves a self-contained HTML document that boots the VWorld WebGL 3D SDK.
// Must be a full document (not a React-injected async script): the SDK uses
// document.write() to pull in Cesium etc., which the browser blocks for
// asynchronously-loaded scripts. Loaded via <iframe> so the SDK script in <head>
// runs during the iframe's own initial parse, where document.write works.
const VWORLD_KEY = process.env.NEXT_PUBLIC_VWORLD_API_KEY || process.env.VWORLD_API_KEY || "";

function num(v: string | null, fallback: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export async function GET(req: NextRequest) {
  if (!VWORLD_KEY) {
    return new Response("<p>VWorld key not configured</p>", {
      status: 503,
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  }
  const sp = req.nextUrl.searchParams;
  const lng = num(sp.get("lng"), 126.9779);
  const lat = num(sp.get("lat"), 37.5663);
  const height = num(sp.get("z"), 1800);

  const html = `<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VWorld 3D</title>
  <style>html,body,#vmap{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#eef2f7}
  #vw-status{position:absolute;top:12px;left:12px;z-index:9;font:600 13px/1.4 system-ui,sans-serif;color:#475569;background:rgba(255,255,255,.9);padding:6px 10px;border-radius:8px}</style>
  <script type="text/javascript" src="https://map.vworld.kr/js/webglMapInit.js.do?version=3.0&apiKey=${VWORLD_KEY}"></script>
</head>
<body>
  <div id="vw-status">3D 엔진 로딩 중…</div>
  <div id="vmap"></div>
  <script type="text/javascript">
    (function () {
      var tries = 0;
      function boot() {
        tries += 1;
        if (typeof vw === "undefined" || !vw.Map) {
          if (tries > 80) { document.getElementById("vw-status").textContent = "3D 엔진 로드 실패 (vw 미준비)"; return; }
          return setTimeout(boot, 250);
        }
        try {
          var map = new vw.Map();
          map.setOption({
            mapId: "vmap",
            initPosition: new vw.CameraPosition(
              new vw.CoordZ(${lng}, ${lat}, ${height}),
              new vw.Direction(0, -55, 0)
            ),
            logo: true,
            navigation: true
          });
          map.setMapId("vmap");
          map.start();
          var s = document.getElementById("vw-status"); if (s) s.remove();
        } catch (e) {
          document.getElementById("vw-status").textContent = "3D 초기화 오류: " + (e && e.message ? e.message : e);
        }
      }
      boot();
    })();
  </script>
</body>
</html>`;

  return new Response(html, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}
