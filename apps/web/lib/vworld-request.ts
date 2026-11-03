const ORIGIN = "https://api.vworld.kr";
const LAYERS = new Set(["lt_c_landinfobasemap", "lt_c_uq111"]);

/** Build only supported VWorld requests; callers cannot choose a host or path. */
export function vworldRequestUrl(params: URLSearchParams, key: string): URL | null {
  if (params.get("kind") === "base") {
    const values = ["z", "x", "y"].map((name) => params.get(name));
    if (values.some((value) => !value || !/^\d{1,8}$/.test(value))) return null;
    const [z, x, y] = [Number(values[0]), Number(values[1]), Number(values[2])] as const;
    if (z < 0 || z > 22 || x < 0 || y < 0 || x >= 2 ** z || y >= 2 ** z) return null;
    return new URL(`/req/wmts/1.0.0/${encodeURIComponent(key)}/Base/${z}/${y}/${x}.png`, ORIGIN);
  }
  if (params.get("kind") !== "wms") return null;
  const layer = params.get("layers") || "lt_c_landinfobasemap";
  if (!LAYERS.has(layer)) return null;
  const raw = params.get("bbox")?.split(",");
  if (!raw || raw.length !== 4 || raw.some((value) => !value.trim())) return null;
  const bbox = [Number(raw[0]), Number(raw[1]), Number(raw[2]), Number(raw[3])] as const;
  if (bbox.some((value) => !Number.isFinite(value) || Math.abs(value) > 20037509)
    || bbox[0] >= bbox[2] || bbox[1] >= bbox[3]) return null;
  const url = new URL("/req/wms", ORIGIN);
  url.search = new URLSearchParams({
    service: "WMS", request: "GetMap", version: "1.3.0", layers: layer,
    styles: layer, crs: "EPSG:3857", bbox: bbox.join(","), width: "256",
    height: "256", format: "image/png", transparent: "true", key,
  }).toString();
  return url;
}
