import { NextRequest } from "next/server";

// Serves a self-contained HTML document that boots the VWorld WebGL 3D SDK.
// Must be a full document (not a React-injected async script): the SDK uses
// document.write() to pull in Cesium etc., which the browser blocks for
// asynchronously-loaded scripts. Loaded via <iframe> so the SDK script in <head>
// runs during the iframe's own initial parse, where document.write works.
const responseHeaders = { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" };

function coordinate(value: string | null, limit: number): number | null {
  const parsed = value?.trim() ? Number(value) : NaN;
  return Number.isFinite(parsed) && Math.abs(parsed) <= limit ? parsed : null;
}

export async function GET(req: NextRequest) {
  // This explicitly public, domain-scoped key is separate from the server API key.
  const browserKey = process.env.NEXT_PUBLIC_VWORLD_API_KEY?.trim();
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
  <script type="text/javascript">window.__realyticsVworldSdkFailed = false;</script>
  <script type="text/javascript" src="https://map.vworld.kr/js/webglMapInit.js.do?version=3.0&amp;apiKey=${encodeURIComponent(browserKey)}" onerror="window.__realyticsVworldSdkFailed = true"></script>
</head>
<body>
  <div id="vw-status">3D 엔진 로딩 중…</div>
  <div id="vmap"></div>
  <script type="text/javascript">
    (function () {
      var tries = 0;
      var state = "loading";
      var renderDeadline = 0;
      var deadline = setTimeout(showUnavailable, 30000);
      function notify(status) {
        if (window.parent !== window) window.parent.postMessage("realytics:vworld-3d-" + status, window.location.origin);
      }
      function showUnavailable() {
        if (state === "unavailable") return;
        state = "unavailable";
        clearTimeout(deadline);
        var status = document.getElementById("vw-status");
        if (!status) {
          status = document.createElement("div"); status.id = "vw-status";
          document.body.appendChild(status);
        }
        status.textContent = "3D 지도를 불러오지 못했습니다.";
        notify("unavailable");
      }
      function verifyRender() {
        if (state !== "loading") return;
        var canvas = document.querySelector("#vmap canvas");
        var context = null;
        try {
          if (canvas) context = canvas.getContext("webgl2") || canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
        } catch {}
        if (canvas && canvas.width > 0 && canvas.height > 0 && context
            && !(typeof context.isContextLost === "function" && context.isContextLost())) {
          state = "ready";
          clearTimeout(deadline);
          var status = document.getElementById("vw-status");
          if (status) status.remove();
          canvas.addEventListener("webglcontextlost", showUnavailable, { once: true });
          notify("ready");
        } else if (Date.now() >= renderDeadline) {
          showUnavailable();
        } else {
          setTimeout(verifyRender, 100);
        }
      }
      function boot() {
        if (state !== "loading") return;
        tries += 1;
        if (window.__realyticsVworldSdkFailed) return showUnavailable();
        if (typeof vw === "undefined" || !vw.Map) {
          if (tries > 80) return showUnavailable();
          return setTimeout(boot, 250);
        }
        try {
          // start() initiates loading; only the SDK callback plus a live WebGL
          // canvas can acknowledge initialization to the parent page.
          vw.ws3dInitCallBack = function () {
            if (state !== "loading") return;
            renderDeadline = Date.now() + 10000;
            requestAnimationFrame(verifyRender);
          };
          var map = new vw.Map();
          map.setOption({
            mapId: "vmap",
            initPosition: new vw.CameraPosition(
              new vw.CoordZ(${lng}, ${lat}, ${height}),
              new vw.Direction(0, ${height >= 300000 ? -90 : -55}, 0)
            ),
            logo: true,
            navigation: true
          });
          map.setMapId("vmap");
          map.start();
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
