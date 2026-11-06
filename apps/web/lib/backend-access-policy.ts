const PUBLIC_GET_PATHS = new Set([
  "/auth/idp/status",
  "/auth/me",
  "/auth/stats",
  "/health",
  "/health/live",
  "/health/ready",
]);

/** Exact unauthenticated BFF allowlist; every business route fails closed. */
export function backendRequestAllowsAnonymous(
  method: string,
  path: string,
  localBootstrapRequest = false,
): boolean {
  if (localBootstrapRequest) return true;
  return method.toUpperCase() === "GET" && PUBLIC_GET_PATHS.has(path);
}
