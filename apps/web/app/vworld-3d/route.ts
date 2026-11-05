import { NextRequest } from "next/server";

// Serves a self-contained HTML document that boots the VWorld WebGL 3D SDK.
// Must be a full document (not a React-injected async script): the SDK uses
// document.write() to pull in Cesium etc., which the browser blocks for
// asynchronously-loaded scripts. Loaded via <iframe> so the SDK script in <head>
// runs during the iframe's own initial parse, where document.write works.
const browserKey = process.env.NEXT_PUBLIC_VWORLD_API_KEY?.trim();
const responseHeaders = { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" };

function coordinate(value: string | null, limit: number): number | null {
  const parsed = value?.trim() ? Number(value) : NaN;
  return Number.isFinite(parsed) && Math.abs(parsed) <= limit ? parsed : null;
}

export async function GET(req: NextRequest) {
  if (!browserKey) {
    return new Response("<p>현재 3D 지도를 사용할 수 없습니다.</p>", { status: 503, headers: responseHeaders });
  }
  const sp = req.nextUrl.searchParams;
  const lng = coordinate(sp.get("lng"), 180);
  const lat = coordinate(sp.get("lat"), 90);
  if (lng === null || lat === null) {
    return new Response("<p>프로젝트 위치를 확인할 수 없습니다.</p>", { status: 400, headers: responseHeaders });
  }
  const requestedHeight = Number(sp.get("z"));
  const height = Number.isFinite(requestedHeight) && requestedHeight > 0 ? requestedHeight : 1800;

  const html = `<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VWorld 3D</title>
  <style>html,body,#vmap{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#eef2f7}
  #vw-status{position:absolute;top:12px;left:12px;z-index:9;font:600 13px/1.4 system-ui,sans-serif;color:#475569;background:rgba(255,255,255,.9);padding:6px 10px;border-radius:8px}</style>
  <script type="text/javascript" src="https://map.vworld.kr/js/webglMapInit.js.do?version=3.0&amp;apiKey=${encodeURIComponent(browserKey)}"></script>
</head>
<body>
  <div id="vw-status">3D 엔진 로딩 중…</div>
  <div id="vmap"></div>
  <script type="text/javascript">
    (function () {
      var tries = 0;
      function notify(status) {
        if (window.parent !== window) window.parent.postMessage("realytics:vworld-3d-" + status, window.location.origin);
      }
      function showUnavailable() {
        document.getElementById("vw-status").textContent = "3D 지도를 불러오지 못했습니다.";
        notify("unavailable");
      }
      function boot() {
        tries += 1;
        if (typeof vw === "undefined" || !vw.Map) {
          if (tries > 80) return showUnavailable();
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
          document.getElementById("vw-status").remove();
          notify("ready");
        } catch {
          showUnavailable();
        }
      }
      boot();
    })();
  </script>
</body>
</html>`;

  return new Response(html, {
    status: 200,
    headers: responseHeaders,
  });
}
