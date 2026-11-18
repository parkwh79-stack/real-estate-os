import type { NextRequest } from "next/server";
import { vworldRequestUrl } from "../../../lib/vworld-request";
import { fetchVworldTile } from "../../../lib/vworld-proxy";

// Server-side proxy for VWorld tiles. Two reasons:
//  1) VWorld WMS (지적도 등) sends no CORS header → the browser blocks maplibre's
//     tile fetch ("Failed to fetch (0)"). Same-origin proxy removes CORS.
//  2) The API key stays on the server (not in client tile URLs).
export async function GET(req: NextRequest) {
  return fetchVworldTile(req.nextUrl, {
    VWORLD_API_KEY: process.env.VWORLD_API_KEY,
    NEXT_PUBLIC_VWORLD_API_KEY: process.env.NEXT_PUBLIC_VWORLD_API_KEY,
    REALYTICS_WEB_ORIGIN: process.env.REALYTICS_WEB_ORIGIN,
  }, vworldRequestUrl);
}
