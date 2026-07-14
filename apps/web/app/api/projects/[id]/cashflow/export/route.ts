import { NextResponse } from "next/server";
import ExcelJS from "exceljs";

export const runtime = "nodejs";

// CalculationRun snapshots are validated by the backend and remain open JSON at this proxy boundary.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRecord = Record<string, any>;

function money(value: unknown): number {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

function setHeader(row: ExcelJS.Row) {
  row.font = { bold: true, color: { argb: "FFFFFFFF" } };
  row.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF172554" } };
  row.alignment = { horizontal: "center", vertical: "middle", wrapText: true };
}

function addTitle(sheet: ExcelJS.Worksheet, title: string, subtitle: string) {
  sheet.mergeCells("A1:H1");
  sheet.getCell("A1").value = title;
  sheet.getCell("A1").font = { bold: true, size: 16, color: { argb: "FF0F172A" } };
  sheet.getCell("A2").value = subtitle;
  sheet.getCell("A2").font = { italic: true, color: { argb: "FF475569" } };
  sheet.columns = Array.from({ length: 8 }, (_, index) => ({ width: index === 0 ? 18 : 20 }));
}

async function latestRun(origin: string, projectId: string, engineKey: string, headers: Headers): Promise<AnyRecord | null> {
  const response = await fetch(`${origin}/projects/${encodeURIComponent(projectId)}/calculation-runs?engine_key=${encodeURIComponent(engineKey)}&limit=1`, {
    cache: "no-store",
    headers,
  });
  if (!response.ok) return null;
  const body = await response.json() as AnyRecord;
  return Array.isArray(body.records) ? body.records[0] ?? null : null;
}

function copyAuthHeaders(request: Request): Headers {
  const headers = new Headers();
  for (const name of ["authorization", "cookie", "x-user-id", "x-role", "x-tenant-id", "x-runtime-mode"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const origin = process.env.REALYTICS_API_ORIGIN || "http://127.0.0.1:8000";
  const auth = copyAuthHeaders(request);
  const engines = ["cash_flow_engine", "waterfall", "tranche_recovery", "unsold_ltv", "discount_stress"];
  const runs = Object.fromEntries(await Promise.all(engines.map(async (engine) => [engine, await latestRun(origin, id, engine, auth)] as const)));
  const cash = runs.cash_flow_engine?.resultSnapshot ?? {};
  const input = runs.cash_flow_engine?.inputSnapshot ?? {};
  const workbook = new ExcelJS.Workbook();
  workbook.creator = "REALYTICS Platform9";
  workbook.calcProperties.fullCalcOnLoad = true;

  const summary = workbook.addWorksheet("00_계산근거");
  addTitle(summary, "REALYTICS 프로젝트별 Cash Flow 계산서", `프로젝트 ${id}의 최신 원장 CalculationRun을 기준으로 생성`);
  summary.addRow(["항목", "값", "근거"]);
  setHeader(summary.getRow(3));
  summary.addRows([
    ["프로젝트 ID", id, "API project context"],
    ["Cash Flow CalculationRun", runs.cash_flow_engine?.calculationRunId ?? "-", "calculation_runs.cash_flow_engine"],
    ["Waterfall CalculationRun", runs.waterfall?.calculationRunId ?? "-", "calculation_runs.waterfall"],
    ["Tranche Recovery CalculationRun", runs.tranche_recovery?.calculationRunId ?? "-", "calculation_runs.tranche_recovery"],
    ["Unsold LTV CalculationRun", runs.unsold_ltv?.calculationRunId ?? "-", "calculation_runs.unsold_ltv"],
    ["Discount Stress CalculationRun", runs.discount_stress?.calculationRunId ?? "-", "calculation_runs.discount_stress"],
    ["Excel 입력 스냅샷", JSON.stringify(input.productMix?.map((item: AnyRecord) => item.collectionSourceLocator).filter(Boolean) ?? []), "cash_flow_engine.inputSnapshot"],
    ["원본 위치", JSON.stringify(runs.cash_flow_engine?.sourceLocator ?? {}), "calculation run sourceLocator"],
  ]);
  summary.getColumn(2).alignment = { wrapText: true };

  const cashSheet = workbook.addWorksheet("01_월별현금흐름");
  addTitle(cashSheet, "월별 Cash Flow", "입력값은 최신 CalculationRun 결과, 순현금흐름과 누적현금흐름은 Excel 수식으로 재계산");
  cashSheet.addRow(["기간", "Cash-In", "Cash-Out", "Net CF", "누적 CF", "Max Minus 여부"]);
  setHeader(cashSheet.getRow(3));
  const monthly = Array.isArray(cash.monthlyCashFlow) ? cash.monthlyCashFlow : [];
  monthly.forEach((row: AnyRecord, index: number) => {
    const excelRow = index + 4;
    const cashIn = money(row.cashIn?.totalCashIn ?? row.cash_in ?? 0) / 1e8;
    const cashOut = money(row.cashOut?.totalCashOut ?? row.cash_out ?? 0) / 1e8;
    cashSheet.addRow([row.period ?? `M${row.monthIndex ?? index}`, cashIn, cashOut, null, null, false]);
    cashSheet.getCell(`D${excelRow}`).value = { formula: `B${excelRow}-C${excelRow}` };
    cashSheet.getCell(`E${excelRow}`).value = { formula: index === 0 ? `D${excelRow}` : `E${excelRow - 1}+D${excelRow}` };
  });
  cashSheet.getColumn(2).numFmt = cashSheet.getColumn(3).numFmt = cashSheet.getColumn(4).numFmt = cashSheet.getColumn(5).numFmt = '#,##0.0;[Red]-#,##0.0';

  const waterfall = workbook.addWorksheet("02_Waterfall");
  addTitle(waterfall, "상환 Waterfall", "시나리오별 가용현금과 Tranche 청구액·부족액");
  waterfall.addRow(["시나리오", "가용현금", "총 청구액", "총 부족액", "잔여현금"]);
  setHeader(waterfall.getRow(3));
  for (const row of (runs.waterfall?.resultSnapshot?.rows ?? []) as AnyRecord[]) waterfall.addRow([row.scenarioId, money(row.cashAvailable) / 1e8, money(row.totalClaimAmount) / 1e8, money(row.totalShortfall) / 1e8, money(row.remainingCash) / 1e8]);

  const recovery = workbook.addWorksheet("03_Tranche회수율");
  addTitle(recovery, "Tranche별 회수율 및 금액", "원금청구액, 회수액, 부족액, 회수율을 시나리오별로 표시");
  recovery.addRow(["시나리오", "Tranche", "원금청구액", "회수액", "부족액", "회수율"]);
  setHeader(recovery.getRow(3));
  for (const scenario of (runs.tranche_recovery?.resultSnapshot?.rows ?? []) as AnyRecord[]) for (const tranche of (scenario.tranches ?? []) as AnyRecord[]) recovery.addRow([scenario.scenarioId, tranche.tranche, money(tranche.principalClaim) / 1e8, money(tranche.paymentReceived) / 1e8, money(tranche.shortfall) / 1e8, `${(money(tranche.recoveryRatio) * 100).toFixed(1)}%`]);

  const ltv = workbook.addWorksheet("04_미분양LTV");
  addTitle(ltv, "미분양 LTV Matrix", "분양률·할인율별 미분양 환가가치와 LTV");
  ltv.addRow(["분양률", "미분양률", "할인율", "할인 후 미분양가치", "미분양 LTV", "위험등급"]);
  setHeader(ltv.getRow(3));
  for (const row of (runs.unsold_ltv?.resultSnapshot?.rows ?? []) as AnyRecord[]) ltv.addRow([`${money(row.saleRate) * 100}%`, `${money(row.unsoldRate) * 100}%`, `${money(row.discountRate) * 100}%`, money(row.discountedUnsoldValue) / 1e8, `${money(row.unsoldLtv) * 100}%`, row.riskLevel ?? "-"]);

  const stress = workbook.addWorksheet("05_할인분양Stress");
  addTitle(stress, "할인분양 Stress", "할인율별 Exit 분양률과 선순위 회수율");
  stress.addRow(["할인율", "Exit 분양률", "선순위 회수율"]);
  setHeader(stress.getRow(3));
  for (const [key, metric] of Object.entries((runs.discount_stress?.resultSnapshot?.metrics ?? {}) as AnyRecord)) if (key.endsWith(".exitSaleRate")) stress.addRow([key.replace(".exitSaleRate", "").replace("discount", "") + "%", `${money(metric.value) * 100}%`, `${money((runs.discount_stress?.resultSnapshot?.metrics ?? {})[key.replace("exitSaleRate", "seniorRecoveryRatio")]?.value) * 100}%`]);

  for (const sheet of workbook.worksheets) {
    sheet.eachRow((row) => row.eachCell((cell) => { cell.border = { top: { style: "thin", color: { argb: "FFE2E8F0" } }, bottom: { style: "thin", color: { argb: "FFE2E8F0" } } }; }));
    sheet.views = [{ state: "frozen", ySplit: 3 }];
  }
  const buffer = await workbook.xlsx.writeBuffer();
  return new NextResponse(buffer, { status: 200, headers: { "Content-Disposition": `attachment; filename="REALYTICS_${id}_CashFlow_Calculation.xlsx"`, "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } });
}
