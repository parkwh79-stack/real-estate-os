/** URL context wins over remembered selection; never invent a project ID. */
export function projectIdFromLocation(pathname: string, search: string): string | null {
  const pathId = pathname.match(/^\/project\/([^/]+)(?:\/|$)/)?.[1];
  const value = pathId ?? new URLSearchParams(search).get("projectId");
  if (!value) return null;
  try {
    const decoded = pathId ? decodeURIComponent(value) : value;
    return /^[A-Za-z0-9:_-]+$/.test(decoded) ? decoded : null;
  } catch {
    return null;
  }
}

export function withProjectContext(href: string, projectId: string | null): string {
  if (!projectId || !href.startsWith("/") || href.startsWith("//")) return href;
  const url = new URL(href, "http://local.invalid");
  url.searchParams.set("projectId", projectId);
  return `${url.pathname}${url.search}${url.hash}`;
}
