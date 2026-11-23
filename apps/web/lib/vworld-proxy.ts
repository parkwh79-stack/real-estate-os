type MapEnvironment = { VWORLD_API_KEY?: string; NEXT_PUBLIC_VWORLD_API_KEY?: string; REALYTICS_WEB_ORIGIN?: string };

/** Fixed-host image proxy. Provider failures must never become cached blank tiles. */
export async function fetchVworldTile(
  request: { origin: string; searchParams: URLSearchParams },
  environment: MapEnvironment,
  buildUrl: (params: URLSearchParams, key: string) => URL | null,
  fetcher: typeof fetch = fetch,
): Promise<Response> {
  const key = environment.VWORLD_API_KEY?.trim() || environment.NEXT_PUBLIC_VWORLD_API_KEY?.trim() || "";
  const unavailable = (status: number, reason: string, upstreamStatus?: number) => Response.json(
    { error: "지도 공급자에 연결하지 못했습니다.", reason, ...(upstreamStatus === undefined ? {} : { upstreamStatus }) },
    { status, headers: { "Cache-Control": "no-store" } },
  );
  if (!key) return unavailable(503, "provider_not_configured");
  const url = buildUrl(request.searchParams, key);
  if (!url || url.origin !== "https://api.vworld.kr") return unavailable(400, "invalid_map_request");
  let refererOrigin = request.origin;
  if (environment.REALYTICS_WEB_ORIGIN) {
    try {
      const configured = new URL(environment.REALYTICS_WEB_ORIGIN.trim());
      if (configured.protocol === "https:" && !configured.username && !configured.password) refererOrigin = configured.origin;
    } catch { /* Use the request origin when no valid service origin is configured. */ }
  }
  try {
    const upstream = await fetcher(url, {
      headers: { Referer: `${refererOrigin}/` }, cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(8000),
    });
    const contentType = upstream.headers.get("content-type") || "";
    if (!upstream.ok || !contentType.toLowerCase().startsWith("image/")) {
      return unavailable(upstream.status === 504 ? 504 : [429, 503].includes(upstream.status) ? 503 : 502,
        upstream.ok ? "provider_invalid_image" : "provider_rejected_request", upstream.status);
    }
    const body = await upstream.arrayBuffer();
    if (!body.byteLength) return unavailable(502, "provider_empty_image", upstream.status);
    return new Response(body, {
      status: 200, headers: { "Content-Type": contentType, "Cache-Control": "public, max-age=86400" },
    });
  } catch (error) {
    return unavailable(error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name) ? 504 : 502,
      "provider_request_failed");
  }
}
