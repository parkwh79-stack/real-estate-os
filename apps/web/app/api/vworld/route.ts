import { NextRequest } from "next/server";
import { vworldRequestUrl } from "../../../lib/vworld-request";

// Server-side proxy for VWorld tiles. Two reasons:
//  1) VWorld WMS (지적도 등) sends no CORS header → the browser blocks maplibre's
//     tile fetch ("Failed to fetch (0)"). Same-origin proxy removes CORS.
//  2) The API key stays on the server (not in client tile URLs).
const VWORLD_KEY = process.env.VWORLD_API_KEY || process.env.NEXT_PUBLIC_VWORLD_API_KEY || "";

export async function GET(req: NextRequest) {
  if (!VWORLD_KEY) {
    return new Response("VWorld key not configured", { status: 503 });
  }
  const url = vworldRequestUrl(req.nextUrl.searchParams, VWORLD_KEY);
  if (!url || url.origin !== "https://api.vworld.kr") {
    return new Response("유효하지 않은 지도 요청입니다.", { status: 400 });
  }

  // 1x1 transparent PNG — returned for any non-image/error so a single bad tile
  // (e.g. an edge tile VWorld answers 400) never logs a console error or blanks the map.
  const transparentTile = () =>
    new Response(
      Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
        "base64",
      ),
      { status: 200, headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=600" } },
    );

  try {
    const upstream = await fetch(url, {
      headers: { Referer: "http://localhost:3000/" },
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(8000),
    });
    const contentType = upstream.headers.get("content-type") || "";
    if (!upstream.ok || !contentType.startsWith("image/")) {
      return transparentTile(); // coerce 400/non-image to an empty tile
    }
    const body = await upstream.arrayBuffer();
    return new Response(body, {
      status: 200,
      headers: { "Content-Type": contentType, "Cache-Control": "public, max-age=86400" },
    });
  } catch {
    return transparentTile();
  }
}
