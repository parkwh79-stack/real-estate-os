/** Keep all eight decimal places of 억원: one won must survive a save/reopen. */
export function wonToEok(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) return "";
  const won = BigInt(value);
  const absolute = won < 0n ? -won : won;
  const fraction = String(absolute % 100000000n).padStart(8, "0").replace(/0+$/, "");
  return `${won < 0n ? "-" : ""}${absolute / 100000000n}${fraction ? `.${fraction}` : ""}`;
}

export function eokToWon(value: string): number {
  const text = value.trim();
  if (!/^-?\d+(?:\.\d{1,8})?$/.test(text)) {
    throw new Error("금액은 억원 단위 숫자로 입력하세요. 소수점 아래 8자리까지 원 단위로 저장합니다.");
  }
  const [whole = "0", fraction = ""] = text.replace(/^-/, "").split(".");
  const won = (BigInt(whole) * 100000000n + BigInt(fraction.padEnd(8, "0"))) * (text.startsWith("-") ? -1n : 1n);
  if (won > BigInt(Number.MAX_SAFE_INTEGER) || won < BigInt(Number.MIN_SAFE_INTEGER)) {
    throw new Error("저장 가능한 금액 범위를 초과했습니다.");
  }
  return Number(won);
}
