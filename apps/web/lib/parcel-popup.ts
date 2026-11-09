/** Provider strings are text, never HTML. Keep this boundary independent of the map renderer. */
export function createParcelPopup(properties: Record<string, unknown>, document: Document): HTMLElement {
  const container = document.createElement("div");
  container.style.fontSize = "12px";
  container.style.lineHeight = "1.55";
  const title = document.createElement("strong");
  title.textContent = String(properties.addressFragment ?? properties.lotNumber ?? properties.pnu ?? "필지");
  container.append(title);
  const numericText = (value: unknown, unit: string): string => {
    if (value === null || value === undefined || value === "") return "미확인";
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toLocaleString("ko-KR")}${unit}` : "미확인";
  };
  for (const text of [
    `PNU ${properties.pnu ?? "미확인"}`,
    `지목 ${properties.landCategory ?? "미확인"} · 면적 ${numericText(properties.areaSqm, "㎡")}`,
    `공시지가 ${numericText(properties.officialLandPricePerSqm, "원/㎡")}`,
  ]) {
    const line = document.createElement("div");
    line.textContent = text;
    container.append(line);
  }
  return container;
}
