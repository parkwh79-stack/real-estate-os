import { NextRequest, NextResponse } from "next/server";

const BACKEND_ORIGIN = process.env.REALYTICS_API_ORIGIN || "http://127.0.0.1:8000";

async function proxy(request: NextRequest) {
  const backendPath = request.nextUrl.pathname.replace(/^\/api\/backend/, "") || "/";
  const target = `${BACKEND_ORIGIN}${backendPath}${request.nextUrl.search}`;
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");

  const init: RequestInit = {
    method: request.method,
    headers,
    redirect: "manual",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  try {
    const response = await fetch(target, init);
    const responseHeaders = new Headers(response.headers);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("content-length");
    return new NextResponse(await response.arrayBuffer(), {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    return NextResponse.json(
      { error: "backend_unavailable", detail: error instanceof Error ? error.message : "Backend request failed" },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
