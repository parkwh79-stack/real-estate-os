/** Display adapter only. Financial amounts (including shortfall) come from the engine. */
export function trancheAmounts(row: Record<string, unknown>) {
  const finite = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
  return {
    claimAmount: finite(row.claimAmount),
    recoveredAmount: finite(row.recoveredAmount),
    shortfall: finite(row.shortfall),
    recoveryRatio: finite(row.recoveryRatio),
  };
}

export function amountInEok(value: number | null): number | null {
  return value === null ? null : value / 1e8;
}
