const LOCAL_DEVELOPMENT_BACKEND_ORIGIN = "http://127.0.0.1:8000";

const PRIVATE_DNS_SUFFIXES = [
  ".internal",
  ".lan",
  ".local",
  ".localdomain",
  ".localhost",
  ".home",
  ".home.arpa",
  ".svc",
  ".cluster.local",
] as const;

const RESERVED_DNS_SUFFIXES = [
  ".invalid",
  ".test",
  ".example",
  ".example.com",
  ".example.net",
  ".example.org",
] as const;

function isIpv4Address(hostname: string): boolean {
  const segments = hostname.split(".");
  return (
    segments.length === 4
    && segments.every((segment) => /^\d{1,3}$/.test(segment))
  );
}

function isNonPublicIpv4(hostname: string): boolean {
  const [first = 0, second = 0, third = 0] = hostname
    .split(".")
    .map((segment) => Number(segment));
  return (
    first === 0
    || first === 10
    || first === 127
    || (first === 100 && second >= 64 && second <= 127)
    || (first === 169 && second === 254)
    || (first === 172 && second >= 16 && second <= 31)
    || (first === 192 && second === 0 && third === 0)
    || (first === 192 && second === 0 && third === 2)
    || (first === 192 && second === 168)
    || (first === 198 && (second === 18 || second === 19))
    || (first === 198 && second === 51 && third === 100)
    || (first === 203 && second === 0 && third === 113)
    || first >= 224
  );
}

function isNonPublicIpv6(hostname: string): boolean {
  const address = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  const firstHextet = Number.parseInt(address.split(":", 1)[0] || "0", 16);
  return (
    address === "::"
    || address === "::1"
    || address.startsWith("::ffff:")
    || (firstHextet >= 0xfc00 && firstHextet <= 0xfdff)
    || (firstHextet >= 0xfe80 && firstHextet <= 0xfebf)
    || (firstHextet >= 0xff00 && firstHextet <= 0xffff)
    || address === "2001:db8::"
    || address.startsWith("2001:db8:")
  );
}

/**
 * Production Web/BFF deployments may only target an explicit public HTTPS
 * origin. Development retains the loopback default and may explicitly select
 * an HTTP endpoint, but credentials and URL routing components are never
 * accepted in either environment.
 */
export function resolveBackendOrigin(
  configuredOrigin: string | undefined,
  environment: string | undefined,
): string | null {
  const production = environment === "production";
  const candidate = configuredOrigin?.trim();
  if (!candidate) {
    return production ? null : LOCAL_DEVELOPMENT_BACKEND_ORIGIN;
  }

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return null;
  }

  if (
    parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
    || !["http:", "https:"].includes(parsed.protocol)
  ) {
    return null;
  }
  if (production && parsed.protocol !== "https:") return null;

  const hostname = parsed.hostname.toLowerCase();
  if (!hostname || hostname.endsWith(".")) return null;
  if (production) {
    if (
      hostname === "localhost"
      || (!hostname.includes(".") && !hostname.includes(":"))
      || PRIVATE_DNS_SUFFIXES.some((suffix) => hostname.endsWith(suffix))
      || RESERVED_DNS_SUFFIXES.some(
        (suffix) => hostname === suffix.slice(1) || hostname.endsWith(suffix),
      )
    ) {
      return null;
    }
    if (isIpv4Address(hostname) && isNonPublicIpv4(hostname)) return null;
    if (hostname.startsWith("[") && isNonPublicIpv6(hostname)) return null;
  }

  return parsed.origin;
}

export { LOCAL_DEVELOPMENT_BACKEND_ORIGIN };
