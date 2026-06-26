import { NextRequest } from "next/server";

// Server-side proxy for VWorld tiles. Two reasons:
//  1) VWorld WMS (지적도 등) sends no CORS header → the browser blocks maplibre's
//     tile fetch ("Failed to fetch (0)"). Same-origin proxy removes CORS.
//  2) The API key stays on the server (not in client tile URLs).
const VWORLD_KEY = process.env.NEXT_PUBLIC_VWORLD_API_KEY || process.env.VWORLD_API_KEY || "";

export async function GET(req: NextRequest) {
  if (!VWORLD_KEY) {
    return new Response("VWorld key not configured", { status: 503 });
  }
  const sp = req.nextUrl.searchParams;
  const kind = sp.get("kind");

  let url: string;
  if (kind === "base") {
    const z = sp.get("z");
    const x = sp.get("x");
    const y = sp.get("y");
    if (!z || !x || !y) return new Response("missing z/x/y", { status: 400 });
    // VWorld WMTS path order is {z}/{y}/{x}.
    url = `https://api.vworld.kr/req/wmts/1.0.0/${VWORLD_KEY}/Base/${z}/${y}/${x}.png`;
  } else if (kind === "wms") {
    const bbox = sp.get("bbox");
    if (!bbox) return new Response("missing bbox", { status: 400 });
    const layers = sp.get("layers") || "lt_c_landinfobasemap";
    const params = new URLSearchParams({
      service: "WMS",
      request: "GetMap",
      version: "1.3.0",
      layers,
      styles: layers,
      crs: "EPSG:3857",
      bbox,
      width: "256",
      height: "256",
      format: "image/png",
      transparent: "true",
      key: VWORLD_KEY,
    });
    url = `https://api.vworld.kr/req/wms?${params.toString()}`;
  } else {
    return new Response("bad kind", { status: 400 });
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
