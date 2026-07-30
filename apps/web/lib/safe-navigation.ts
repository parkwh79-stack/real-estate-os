const SENTINEL_ORIGIN = "https://realytics.invalid";

function hasUnsafePathCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return character === "\\" || codePoint <= 31 || codePoint === 127;
  });
}

/** Normalize a caller-supplied return path without permitting another origin. */
export function safeInternalNextPath(
  value: string | null,
  fallback = "/start",
): string {
  if (
    !value
    || !value.startsWith("/")
    || value.startsWith("//")
    || hasUnsafePathCharacter(value)
  ) {
    return fallback;
  }
  try {
    // WHATWG URLs treat `/\\host` as a scheme-relative navigation. Parsing
    // against a fixed origin and comparing the result protects every callback
    // consumer from current and future URL-normalization variants.
    const parsed = new URL(value, SENTINEL_ORIGIN);
    if (parsed.origin !== SENTINEL_ORIGIN) return fallback;
    const normalized = `${parsed.pathname}${parsed.search}${parsed.hash}`;
    return normalized === "/" || normalized === "/dashboard"
      ? fallback
      : normalized;
  } catch {
    return fallback;
  }
}
