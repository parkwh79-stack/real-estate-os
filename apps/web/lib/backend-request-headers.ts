const UNTRUSTED_IDENTITY_HEADER_NAMES = new Set([
  "authorization",
  "cookie",
  "x-request-id",
  "x-user-id",
  "x-codex-user-id",
  "x-role",
  "x-tenant-id",
  "x-runtime-mode",
]);

const HOP_BY_HOP_HEADER_NAMES = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "http2-settings",
]);

const PROXY_SEMANTIC_HEADER_NAMES = new Set([
  "forwarded",
  "via",
  "x-real-ip",
]);

function connectionNominatedHeaderNames(headers: Headers): string[] {
  return (headers.get("connection") ?? "")
    .split(",")
    .map((name) => name.trim().toLowerCase())
    .filter((name) => /^[!#$%&'*+.^_`|~0-9a-z-]+$/.test(name));
}

function removeUntrustedTransportHeaders(headers: Headers): void {
  const connectionNominated = connectionNominatedHeaderNames(headers);
  for (const name of Array.from(headers.keys())) {
    const normalized = name.toLowerCase();
    if (
      HOP_BY_HOP_HEADER_NAMES.has(normalized)
      || PROXY_SEMANTIC_HEADER_NAMES.has(normalized)
      || normalized.startsWith("x-forwarded-")
      || connectionNominated.includes(normalized)
    ) {
      headers.delete(name);
    }
  }
}

/** Remove browser-controlled identity aliases before a request reaches FastAPI. */
export function sanitizedBackendRequestHeaders(
  source?: HeadersInit,
): Headers {
  const headers = new Headers(source);
  removeUntrustedTransportHeaders(headers);
  for (const name of Array.from(headers.keys())) {
    const normalized = name.toLowerCase();
    if (
      UNTRUSTED_IDENTITY_HEADER_NAMES.has(normalized)
      || normalized.startsWith("x-realytics-")
    ) {
      headers.delete(name);
    }
  }
  headers.delete("host");
  headers.delete("content-length");
  return headers;
}

/** Issue a fresh BFF correlation id after browser-controlled ids are removed. */
export function serverIssuedBackendRequestHeaders(
  source?: HeadersInit,
  requestId = crypto.randomUUID(),
): Headers {
  const headers = sanitizedBackendRequestHeaders(source);
  headers.set("X-Request-Id", requestId);
  return headers;
}

/** Do not relay upstream connection/proxy semantics to the browser. */
export function sanitizedBackendResponseHeaders(source?: HeadersInit): Headers {
  const headers = new Headers(source);
  removeUntrustedTransportHeaders(headers);
  headers.delete("content-encoding");
  headers.delete("content-length");
  headers.delete("set-cookie");
  return headers;
}
