import { expect, test } from "@playwright/test";
import { resolveBackendOrigin } from "../lib/backend-origin-contract";
import {
  BackendResponseBodyLimitError,
  createBoundedBackendSignal,
  prepareBackendResponseBody,
  requiresBoundedSessionRedaction,
  streamingBackendRequestInit,
} from "../lib/backend-proxy-contract";

test("production backend origin accepts only a public origin-only HTTPS URL", () => {
  expect(
    resolveBackendOrigin("https://api.realytics.co.kr/", "production"),
  ).toBe("https://api.realytics.co.kr");
  expect(
    resolveBackendOrigin("https://api.realytics.co.kr:8443", "production"),
  ).toBe("https://api.realytics.co.kr:8443");

  for (const invalid of [
    undefined,
    "http://api.realytics.co.kr",
    "https://user:secret@api.realytics.co.kr",
    "https://api.realytics.co.kr/v1",
    "https://api.realytics.co.kr?tenant=a",
    "https://api.realytics.co.kr/#fragment",
    "https://localhost:8000",
    "https://127.0.0.1:8000",
    "https://10.0.0.1",
    "https://100.64.0.1",
    "https://169.254.1.1",
    "https://172.16.0.1",
    "https://192.168.0.1",
    "https://[::1]",
    "https://[fc00::1]",
    "https://api.internal",
    "https://api.cluster.local",
    "https://api",
  ]) {
    expect(resolveBackendOrigin(invalid, "production"), invalid).toBeNull();
  }
});

test("development backend origin retains its localhost fallback", () => {
  expect(resolveBackendOrigin(undefined, "development")).toBe(
    "http://127.0.0.1:8000",
  );
  expect(
    resolveBackendOrigin("http://localhost:9000/", "development"),
  ).toBe("http://localhost:9000");
});

test("backend upstream signal is bounded and propagates client cancellation", async () => {
  const bounded = createBoundedBackendSignal(undefined, 5);
  await expect.poll(() => bounded.timeoutSignal.aborted).toBe(true);
  expect(bounded.signal.aborted).toBe(true);

  const client = new AbortController();
  const clientBounded = createBoundedBackendSignal(client.signal, 60_000);
  client.abort();
  expect(clientBounded.signal.aborted).toBe(true);
  expect(clientBounded.timeoutSignal.aborted).toBe(false);
});

test("ordinary proxy bodies remain streaming in both directions", async () => {
  const requestBody = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array([1, 2, 3]));
      controller.close();
    },
  });
  const requestInit = streamingBackendRequestInit(
    "POST",
    new Headers({ "Content-Type": "application/octet-stream" }),
    requestBody,
    new AbortController().signal,
  );
  expect(requestInit.body).toBe(requestBody);
  expect(requestInit.duplex).toBe("half");

  const response = new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([4, 5, 6]));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "application/octet-stream" } },
  );
  const originalBody = response.body;
  const prepared = await prepareBackendResponseBody(response, false);
  expect(prepared.buffered).toBe(false);
  expect(prepared.body).toBe(originalBody);
});

test("session-bearing JSON is bounded and removes the browser-visible session id", async () => {
  expect(requiresBoundedSessionRedaction("/auth/me", "GET")).toBe(true);
  expect(requiresBoundedSessionRedaction("/auth/session", "POST")).toBe(true);
  expect(requiresBoundedSessionRedaction("/projects", "POST")).toBe(false);

  const prepared = await prepareBackendResponseBody(
    Response.json({
      authenticated: true,
      session: {
        sessionId: "server-session-id",
        expiresAt: "2026-07-30T12:00:00Z",
      },
    }),
    true,
  );
  expect(prepared.buffered).toBe(true);
  expect(prepared.localSession).toEqual({
    sessionId: "server-session-id",
    expiresAt: "2026-07-30T12:00:00Z",
  });
  expect(
    JSON.parse(new TextDecoder().decode(prepared.body as Uint8Array)),
  ).toEqual({
    authenticated: true,
    session: { expiresAt: "2026-07-30T12:00:00Z" },
  });

  const rejected = await prepareBackendResponseBody(
    Response.json(
      { error: "rejected", session: { sessionId: "must-not-escape" } },
      { status: 403 },
    ),
    true,
  );
  expect(
    JSON.parse(new TextDecoder().decode(rejected.body as Uint8Array)),
  ).toEqual({ error: "rejected", session: {} });

  await expect(
    prepareBackendResponseBody(
      new Response("x".repeat(17), {
        headers: { "Content-Type": "application/json" },
      }),
      true,
      16,
    ),
  ).rejects.toBeInstanceOf(BackendResponseBodyLimitError);
});
