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

export type ReviewSource = { archiveId: string; originalFileName: string };
export type FieldReview = {
  origin: "user_assumption" | "document_correction";
  reason: string;
  sourceCell: string;
  sourceDocument?: ReviewSource | null;
};
export const SCHEDULE_FIELDS = [
  ["land_purchase", "토지 매입일"], ["permit_approval", "인허가일"],
  ["construction_start", "착공일"], ["presale_start", "분양 시작일"],
  ["completion", "준공일"], ["loan_maturity", "대출 만기일"],
] as const;
export const FINANCING_PRIORITIES = [
  ["senior", "선순위"], ["mezzanine", "중순위"], ["junior", "후순위"],
  ["equity", "자기자본"], ["bridge", "브릿지"], ["other", "기타"],
] as const;
export type FinancingTerm = {
  tranche_id: string; name: string; priority: string;
  amount: number | null; interest_rate: number | null; fee_rate: number | null;
  term_months: number | null; repayment_source: string | null;
};
export type ReviewTerms = { schedule: Record<string, string | null>; financing: FinancingTerm[] };
export type FinancingDraft = {
  tranche_id: string; name: string; priority: string;
  amount: string; interest_rate: string; fee_rate: string; term_months: string; repayment_source: string;
};
export type TermsDraft = {
  schedule: Record<string, string>; financing: FinancingDraft[];
  reviews: Record<"schedule" | "financing", FieldReview>;
  changedSchedule: string[]; scheduleDirty: boolean; financingDirty: boolean;
};

const STORED_VALUE_ISSUE = "원본값 확인 필요: ";
function storedValueIssue(value: unknown): string {
  return `${STORED_VALUE_ISSUE}${String(value)}`;
}

function percentText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) return storedValueIssue(value);
  const percent = value * 100;
  const rounded = Number(percent.toFixed(8));
  // Never round an unsupported stored precision into an editable, different rate.
  if ((value !== 0 && rounded === 0) || Math.abs(percent - rounded) > Number.EPSILON * Math.max(1, percent) * 4) {
    return storedValueIssue(value);
  }
  return percent.toFixed(8).replace(/\.?0+$/, "");
}

function storedAmountText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? wonToEok(value) : storedValueIssue(value);
}

function storedMonthText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 1200 ? String(value) : storedValueIssue(value);
}

export function termsDraft(terms?: ReviewTerms, reviews?: Partial<TermsDraft["reviews"]>): TermsDraft {
  const review = (key: "schedule" | "financing"): FieldReview => ({
    origin: reviews?.[key]?.origin ?? "user_assumption", reason: reviews?.[key]?.reason ?? "",
    sourceCell: reviews?.[key]?.sourceCell ?? "", sourceDocument: reviews?.[key]?.sourceDocument,
  });
  return {
    schedule: Object.fromEntries(SCHEDULE_FIELDS.map(([key]) => [key, terms?.schedule[key] ?? ""])),
    financing: (terms?.financing ?? []).map((row) => ({
      tranche_id: row.tranche_id, name: typeof row.name === "string" ? row.name : storedValueIssue(row.name), priority: row.priority,
      amount: storedAmountText(row.amount), interest_rate: percentText(row.interest_rate), fee_rate: percentText(row.fee_rate),
      term_months: storedMonthText(row.term_months), repayment_source: row.repayment_source === null || row.repayment_source === undefined
        ? "" : typeof row.repayment_source === "string" ? row.repayment_source : storedValueIssue(row.repayment_source),
    })),
    reviews: { schedule: review("schedule"), financing: review("financing") }, changedSchedule: [], scheduleDirty: false, financingDirty: false,
  };
}

export function termsHaveChanges(draft: TermsDraft): boolean {
  return draft.scheduleDirty || draft.financingDirty;
}

function rateToDecimal(value: string): number | null {
  if (!value.trim()) return null;
  if (!/^\d+(?:\.\d{1,8})?$/.test(value.trim()) || Number(value) > 100) {
    throw new Error("금리·수수료율은 0~100% 숫자로 입력하세요.");
  }
  return Number(value) / 100;
}

/** Build only changed groups; validation completes before the single save request. */
export function reviewedTermsPayload(draft: TermsDraft, selectedSource?: ReviewSource) {
  const terms: Partial<ReviewTerms> = {};
  const termReviews: Partial<TermsDraft["reviews"]> = {};
  const termSourceDocuments: Partial<Record<"schedule" | "financing", ReviewSource>> = {};
  const groups = [draft.scheduleDirty ? "schedule" : null, draft.financingDirty ? "financing" : null] as const;
  for (const group of groups) {
    if (!group) continue;
    const review = draft.reviews[group];
    const label = group === "schedule" ? "사업기간" : "대출 조건";
    if (!review.reason.trim()) throw new Error(`${label}: 수정 또는 가정의 이유를 입력하세요.`);
    if (review.origin === "document_correction") {
      const source = review.sourceDocument ?? selectedSource;
      if (!source || !review.sourceCell.trim()) throw new Error(`${label}: 원본 문서와 출처 셀을 입력하세요.`);
      termSourceDocuments[group] = source;
    }
    termReviews[group] = { origin: review.origin, reason: review.reason.trim(), sourceCell: review.sourceCell.trim() };
  }
  if (draft.scheduleDirty) {
    terms.schedule = {};
    for (const key of draft.changedSchedule) {
      const value = draft.schedule[key]?.trim() ?? "";
      if (value && (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-") || !Number.isFinite(Date.parse(value))
        || new Date(value).toISOString().slice(0, 10) !== value)) {
        throw new Error("일정은 YYYY-MM-DD 형식의 실제 날짜로 입력하세요. 빈 값은 미입력으로 저장합니다.");
      }
      terms.schedule[key] = value || null;
    }
    const start = draft.schedule.construction_start ?? "";
    const end = draft.schedule.completion ?? "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(start) && /^\d{4}-\d{2}-\d{2}$/.test(end) && end < start) {
      throw new Error("준공일은 착공일보다 앞설 수 없습니다.");
    }
  }
  if (draft.financingDirty) {
    terms.financing = draft.financing.map((row, index) => {
      if (row.name.startsWith(STORED_VALUE_ISSUE) || row.repayment_source.startsWith(STORED_VALUE_ISSUE)) {
        throw new Error(`대출 ${index + 1}: 형식이 다른 기존 값을 확인하고 직접 입력하거나 지우세요.`);
      }
      if (!row.name.trim() || !FINANCING_PRIORITIES.some(([priority]) => priority === row.priority)) {
        throw new Error(`대출 ${index + 1}: 명칭과 순위를 입력하세요.`);
      }
      const amount = row.amount.trim() ? eokToWon(row.amount) : null;
      if (amount !== null && amount < 0) throw new Error("대출 금액에 음수를 입력할 수 없습니다.");
      const monthText = row.term_months.trim();
      if (monthText && (!/^\d+$/.test(monthText) || Number(monthText) < 1 || Number(monthText) > 1200)) {
        throw new Error("대출 만기는 1~1200개월의 정수로 입력하세요.");
      }
      return { tranche_id: row.tranche_id, name: row.name.trim(), priority: row.priority, amount,
        interest_rate: rateToDecimal(row.interest_rate), fee_rate: rateToDecimal(row.fee_rate),
        term_months: monthText ? Number(monthText) : null, repayment_source: row.repayment_source.trim() || null };
    });
  }
  return Object.keys(terms).length ? { terms, termReviews, termSourceDocuments } : {};
}
