export const BACKEND_UPSTREAM_TIMEOUT_MS = 120_000;
export const BOUNDED_AUTH_RESPONSE_BYTES = 256 * 1024;

export type StreamingRequestInit = RequestInit & { duplex?: "half" };

export type LocalBackendSession = {
  sessionId: string;
  expiresAt?: string;
};

export type PreparedBackendResponseBody = {
  body: BodyInit | null;
  localSession: LocalBackendSession | null;
  buffered: boolean;
};

export class BackendResponseBodyLimitError extends Error {
  constructor() {
    super("backend_auth_response_too_large");
    this.name = "BackendResponseBodyLimitError";
  }
}

export function streamingBackendRequestInit(
  method: string,
  headers: Headers,
  body: ReadableStream<Uint8Array> | null,
  signal: AbortSignal,
): StreamingRequestInit {
  const init: StreamingRequestInit = {
    method,
    headers,
    redirect: "manual",
    signal,
  };
  if (!['GET', 'HEAD'].includes(method.toUpperCase()) && body !== null) {
    init.body = body;
    // Node/undici requires this marker for a streaming request body. It is
    // ignored by browser fetch and does not alter the HTTP payload.
    init.duplex = "half";
  }
  return init;
}

export function createBoundedBackendSignal(
  clientSignal?: AbortSignal,
  timeoutMs = BACKEND_UPSTREAM_TIMEOUT_MS,
): { signal: AbortSignal; timeoutSignal: AbortSignal } {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  return {
    signal: clientSignal
      ? AbortSignal.any([clientSignal, timeoutSignal])
      : timeoutSignal,
    timeoutSignal,
  };
}

export function requiresBoundedSessionRedaction(
  backendPath: string,
  method: string,
): boolean {
  return (
    backendPath === "/auth/me"
    || (backendPath === "/auth/session" && method.toUpperCase() === "POST")
  );
}

async function readBoundedBody(
  response: Response,
  maxBytes: number,
): Promise<Uint8Array> {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new BackendResponseBodyLimitError();
  }
  if (!response.body) return new Uint8Array();

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        try {
          await reader.cancel("bounded_auth_response_exceeded");
        } catch {
          // The size violation is authoritative even if cancellation fails.
        }
        throw new BackendResponseBodyLimitError();
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const combined = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return combined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

/**
 * Preserve streaming for ordinary API, upload, PDF, and Excel responses. Only
 * the two legacy session-bearing JSON contracts are read into a bounded buffer
 * so a session id can never escape into browser-visible JSON.
 */
export async function prepareBackendResponseBody(
  response: Response,
  redactSession: boolean,
  maxBytes = BOUNDED_AUTH_RESPONSE_BYTES,
): Promise<PreparedBackendResponseBody> {
  const contentType = (response.headers.get("content-type") ?? "").toLowerCase();
  if (
    !redactSession
    || !(contentType.includes("application/json") || contentType.includes("+json"))
  ) {
    return { body: response.body, localSession: null, buffered: false };
  }

  const encoded = await readBoundedBody(response, maxBytes);
  const parsed = JSON.parse(new TextDecoder().decode(encoded)) as unknown;
  let localSession: LocalBackendSession | null = null;
  if (isRecord(parsed) && isRecord(parsed.session)) {
    const sessionId = parsed.session.sessionId;
    const expiresAt = parsed.session.expiresAt;
    delete parsed.session.sessionId;
    if (typeof sessionId === "string" && sessionId) {
      localSession = {
        sessionId,
        ...(typeof expiresAt === "string" ? { expiresAt } : {}),
      };
    }
  }
  return {
    body: new TextEncoder().encode(JSON.stringify(parsed)),
    localSession,
    buffered: true,
  };
}
