// Supabase custom OAuth expects top-level OIDC-shaped profile claims.
// Naver returns { resultcode, response: { id, nickname, profile_image } }.
// The only identity source is Naver's authenticated API response.
export async function naverUserinfo(request: Request, fetchProfile: typeof fetch = fetch): Promise<Response> {
  if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
  const authorization = request.headers.get("authorization") ?? "";
  if (!/^Bearer [A-Za-z0-9._~+/-]+=*$/i.test(authorization) || authorization.length > 8192) {
    return json({ error: "invalid_token" }, 401);
  }
  try {
    const result = await fetchProfile("https://openapi.naver.com/v1/nid/me", {
      headers: { Authorization: authorization, Accept: "application/json" },
      redirect: "error",
      signal: AbortSignal.timeout(5000),
    });
    if (!result.ok) {
      return json({ error: result.status >= 500 || result.status === 429 ? "provider_unavailable" : "invalid_token" },
        result.status >= 500 || result.status === 429 ? 503 : 401);
    }
    const body = await result.json();
    const profile = body?.response;
    if (body?.resultcode !== "00" || typeof profile?.id !== "string" || !profile.id.trim()) {
      return json({ error: "invalid_provider_identity" }, 401);
    }
    // Naver's email field has no email_verified claim. Omit it so an address
    // cannot trigger automatic linking to an existing Supabase identity.
    return json({
      sub: profile.id,
      ...(typeof profile.nickname === "string" ? { name: profile.nickname } : {}),
      ...(typeof profile.profile_image === "string" && profile.profile_image.startsWith("https://")
        ? { picture: profile.profile_image } : {}),
    });
  } catch {
    return json({ error: "provider_unavailable" }, 503);
  }
}

function json(body: Record<string, unknown>, status = 200): Response {
  return Response.json(body, { status, headers: {
    "Cache-Control": "no-store, private",
    ...(status === 401 ? { "WWW-Authenticate": "Bearer" } : {}),
    ...(status === 405 ? { Allow: "GET" } : {}),
  } });
}
