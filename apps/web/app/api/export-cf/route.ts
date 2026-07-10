/* eslint-disable no-useless-escape -- Excel formula strings intentionally contain escaped quotes. */
import { NextResponse } from "next/server";
import ExcelJS from "exceljs";
import {
  resolveExcelWorkbookTemplate,
  type WorkbookTemplateMetadata,
} from "../../../lib/excel-template-repository";

export const runtime = "nodejs";

const MONTH_COUNT = 45;
const SALES_START_MONTH = 10;
const CONSTRUCTION_START_MONTH = 3;
const COMPLETION_MONTH = 42;
const COLLECTION_START_COLUMN = 7;

const productMix = [
  {
    type: "아파트",
    units: 230,
    totalRevenue: 1267.3,
    source: "샘플 입력!32:50, 사업수지!F8",
  },
  {
    type: "오피스텔",
    units: 76,
    totalRevenue: 245.91,
    source: "샘플 입력!51:59, 사업수지!H8",
  },
  {
    type: "근린생활시설",
    units: 1,
    totalRevenue: 142.76,
    source: "샘플 사업수지!J8 역산",
  },
];

const assumptions = {
  projectName: "대구 본동 주상복합 PF",
  unit: "억원",
  totalRevenue: 1655.97,
  landCost: 460.32,
  constructionCost: 720.36,
  designCost: 35.61,
  salesCost: 63.18,
  chargesCost: 26.54,
  financeCost: 162.98,
  otherCost: 49.42,
  pfLoan: 900,
  analysisMonths: MONTH_COUNT,
  salesStartMonth: SALES_START_MONTH,
  constructionStartMonth: CONSTRUCTION_START_MONTH,
  completionMonth: COMPLETION_MONTH,
  pfInterestRate: 0.065,
  preCompletionRepaymentReserveRatio: 0.25,
  postCompletionRepaymentReserveRatio: 1,
  revenueScenario: "Base",
  sourceWorkbook:
    "19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx",
};

const residentialCollectionSteps = [
  { label: "계약금", offsetMonth: 0, rate: 0.1 },
  { label: "중도금 1차", offsetMonth: 4, rate: 0.1 },
  { label: "중도금 2차", offsetMonth: 8, rate: 0.1 },
  { label: "중도금 3차", offsetMonth: 12, rate: 0.1 },
  { label: "중도금 4차", offsetMonth: 16, rate: 0.1 },
  { label: "중도금 5차", offsetMonth: 20, rate: 0.1 },
  { label: "중도금 6차", offsetMonth: 24, rate: 0.1 },
  { label: "잔금", dueMonth: COMPLETION_MONTH, rate: 0.3 },
];

const retailCollectionSteps = [
  { label: "계약금", offsetMonth: 0, rate: 0.1 },
  { label: "중도금 1차", offsetMonth: 6, rate: 0.1 },
  { label: "중도금 2차", offsetMonth: 12, rate: 0.1 },
  { label: "중도금 3차", offsetMonth: 18, rate: 0.1 },
  { label: "중도금 4차", offsetMonth: 24, rate: 0.1 },
  { label: "중도금 5차", offsetMonth: 30, rate: 0.1 },
  { label: "잔금", dueMonth: COMPLETION_MONTH, rate: 0.4 },
];

const trancheStructure = [
  {
    tranche: "Tr.A",
    name: "선순위",
    principal: 630,
    interestRate: 0.045,
    feeRate: 0.015,
    priority: 1,
  },
  {
    tranche: "Tr.B",
    name: "중순위",
    principal: 120,
    interestRate: 0.065,
    feeRate: 0.05,
    priority: 2,
  },
  {
    tranche: "Tr.C",
    name: "후순위",
    principal: 150,
    interestRate: 0.08,
    feeRate: 0.1,
    priority: 3,
  },
];

const discountScenarios = [0, 0.05, 0.1, 0.15, 0.2];

const discountTriggers = [
  { label: "6개월", month: 6, targetSalesRate: 0.4, discountRate: 0.05 },
  { label: "12개월", month: 12, targetSalesRate: 0.6, discountRate: 0.1 },
  { label: "24개월", month: 24, targetSalesRate: 0.75, discountRate: 0.15 },
  { label: "준공월", month: COMPLETION_MONTH, targetSalesRate: 0.8, discountRate: 0.2 },
];

function columnName(columnIndex: number) {
  let dividend = columnIndex;
  let name = "";

  while (dividend > 0) {
    const modulo = (dividend - 1) % 26;
    name = String.fromCharCode(65 + modulo) + name;
    dividend = Math.floor((dividend - modulo) / 26);
  }

  return name;
}

function inputCell(row: number) {
  return `'01_입력가정'!$C$${row}`;
}

function quoteSheet(sheetName: string) {
  return `'${sheetName.replaceAll("'", "''")}'`;
}

function defineWorkbookName(
  workbook: ExcelJS.Workbook,
  name: string,
  sheetName: string,
  address: string,
) {
  workbook.definedNames.add(`${quoteSheet(sheetName)}!${address}`, name);
}

function styleTitle(cell: ExcelJS.Cell) {
  cell.font = { bold: true, size: 15, color: { argb: "FF0F172A" } };
  cell.alignment = { vertical: "middle" };
}

function styleHeader(row: ExcelJS.Row) {
  row.font = { bold: true, color: { argb: "FF0F172A" } };
  row.alignment = { horizontal: "center", vertical: "middle", wrapText: true };
  row.fill = {
    type: "pattern",
    pattern: "solid",
    fgColor: { argb: "FFE2E8F0" },
  };
}

function styleSheet(sheet: ExcelJS.Worksheet) {
  sheet.eachRow((row) => {
    row.eachCell((cell) => {
      cell.border = {
        top: { style: "thin", color: { argb: "FFE2E8F0" } },
        left: { style: "thin", color: { argb: "FFE2E8F0" } },
        bottom: { style: "thin", color: { argb: "FFE2E8F0" } },
        right: { style: "thin", color: { argb: "FFE2E8F0" } },
      };
      cell.alignment = {
        vertical: "middle",
        horizontal: typeof cell.value === "number" ? "right" : "left",
        wrapText: true,
      };
    });
  });
}

function setFormula(cell: ExcelJS.Cell, formula: string, result: number | string) {
  cell.value = { formula, result };
  cell.protection = { locked: true };
}

function setMoneyFormat(cell: ExcelJS.Cell) {
  cell.numFmt = '#,##0.0;[Red]-#,##0.0;"-"';
}

function setPercentFormat(cell: ExcelJS.Cell) {
  cell.numFmt = "0.0%;[Red]-0.0%";
}

function getCollectionSteps(productType: string) {
  return productType === "근린생활시설"
    ? retailCollectionSteps
    : residentialCollectionSteps;
}

function buildCollectionRows() {
  return productMix.flatMap((product) =>
    getCollectionSteps(product.type).map((step) => {
      const dueMonth =
        "dueMonth" in step
          ? step.dueMonth
          : assumptions.salesStartMonth + step.offsetMonth;
      return {
        productType: product.type,
        label: step.label,
        dueMonth,
        collectionRate: step.rate,
        productRevenue: product.totalRevenue,
        amount: product.totalRevenue * step.rate,
      };
    }),
  );
}

const collectionRows = buildCollectionRows();
const collectionTotalRow = 4 + collectionRows.length;
const collectionLastColumn = columnName(COLLECTION_START_COLUMN + MONTH_COUNT - 1);

function salesCollectionForMonth(month: number) {
  return collectionRows
    .filter((row) => row.dueMonth === month)
    .reduce((sum, row) => sum + row.amount, 0);
}

function sCurveCumulative(month: number) {
  if (month < assumptions.constructionStartMonth) {
    return 0;
  }
  if (month >= assumptions.completionMonth) {
    return 1;
  }

  const elapsed = month - assumptions.constructionStartMonth + 1;
  const duration = assumptions.completionMonth - assumptions.constructionStartMonth + 1;
  return (1 - Math.cos(Math.PI * elapsed / duration)) / 2;
}

function sCurveMonthlyProgress(month: number) {
  return Math.max(0, sCurveCumulative(month) - sCurveCumulative(month - 1));
}

function buildMonthlyResults() {
  const monthlyDesign = assumptions.designCost / MONTH_COUNT;
  const monthlyOther = assumptions.otherCost / MONTH_COUNT;
  const financeMonths = MONTH_COUNT - assumptions.constructionStartMonth + 1;

  let cumulative = 0;

  return Array.from({ length: MONTH_COUNT }, (_, index) => {
    const month = index + 1;
    const salesRevenue = salesCollectionForMonth(month);
    const landCost = month === 1 ? assumptions.landCost : 0;
    const constructionCost = assumptions.constructionCost * sCurveMonthlyProgress(month);
    const designCost = monthlyDesign;
    const salesCost =
      assumptions.totalRevenue === 0
        ? 0
        : assumptions.salesCost * (salesRevenue / assumptions.totalRevenue);
    const chargesCost =
      month === assumptions.completionMonth - 1 || month === assumptions.completionMonth
        ? assumptions.chargesCost / 2
        : 0;
    const financeCost =
      month >= assumptions.constructionStartMonth ? assumptions.financeCost / financeMonths : 0;
    const otherCost = monthlyOther;
    const cashOut =
      landCost +
      constructionCost +
      designCost +
      salesCost +
      chargesCost +
      financeCost +
      otherCost;
    const netCashFlow = salesRevenue - cashOut;
    cumulative += netCashFlow;
    const maxMinusToDate = Math.min(cumulative, 0);

    return {
      month,
      salesRevenue,
      cashIn: salesRevenue,
      landCost,
      constructionCost,
      designCost,
      salesCost,
      chargesCost,
      financeCost,
      otherCost,
      cashOut,
      netCashFlow,
      cumulative,
      maxMinusToDate,
      pfDrawNeed: Math.min(assumptions.pfLoan, Math.max(0, -maxMinusToDate)),
    };
  });
}

function eokToThousand(value: number) {
  return value * 100000;
}

function eokToMillion(value: number) {
  return value * 100;
}

function clonePlain<T>(value: T): T {
  if (!value) {
    return value;
  }
  return JSON.parse(JSON.stringify(value)) as T;
}

function cloneCellValue(value: ExcelJS.CellValue) {
  if (value instanceof Date) {
    return new Date(value);
  }
  if (value && typeof value === "object") {
    return clonePlain(value);
  }
  return value;
}

function copyWorksheetFromTemplate(
  source: ExcelJS.Worksheet,
  workbook: ExcelJS.Workbook,
  targetName = source.name,
) {
  const target = workbook.addWorksheet(targetName);

  target.properties = clonePlain(source.properties);
  target.pageSetup = clonePlain(source.pageSetup);
  target.headerFooter = clonePlain(source.headerFooter);
  target.views = clonePlain(source.views);
  target.autoFilter = clonePlain(source.autoFilter);
  target.state = source.state;

  source.columns.forEach((sourceColumn, index) => {
    const targetColumn = target.getColumn(index + 1);
    if (sourceColumn.width !== undefined) {
      targetColumn.width = sourceColumn.width;
    }
    if (sourceColumn.hidden !== undefined) {
      targetColumn.hidden = sourceColumn.hidden;
    }
    if (sourceColumn.outlineLevel !== undefined) {
      targetColumn.outlineLevel = sourceColumn.outlineLevel;
    }
    if (sourceColumn.style) {
      targetColumn.style = clonePlain(sourceColumn.style);
    }
  });

  source.eachRow({ includeEmpty: true }, (sourceRow, rowNumber) => {
    const targetRow = target.getRow(rowNumber);
    targetRow.height = sourceRow.height;
    targetRow.hidden = sourceRow.hidden;
    targetRow.outlineLevel = sourceRow.outlineLevel;

    sourceRow.eachCell({ includeEmpty: true }, (sourceCell, columnNumber) => {
      const targetCell = targetRow.getCell(columnNumber);
      targetCell.value = cloneCellValue(sourceCell.value);
      targetCell.style = clonePlain(sourceCell.style);
      targetCell.numFmt = sourceCell.numFmt;
      targetCell.font = clonePlain(sourceCell.font);
      targetCell.alignment = clonePlain(sourceCell.alignment);
      targetCell.border = clonePlain(sourceCell.border);
      targetCell.fill = clonePlain(sourceCell.fill);
      targetCell.protection = clonePlain(sourceCell.protection);
      targetCell.note = clonePlain(sourceCell.note);
    });
  });

  const merges = (source.model.merges ?? []) as string[];
  merges.forEach((mergeRange) => target.mergeCells(mergeRange));

  return target;
}

async function addSampleWorkbookParitySheets(
  workbook: ExcelJS.Workbook,
): Promise<WorkbookTemplateMetadata | null> {
  const template = await resolveExcelWorkbookTemplate();
  if (!template) {
    return null;
  }

  const sourceWorkbook = new ExcelJS.Workbook();
  await sourceWorkbook.xlsx.readFile(template.sourcePath);

  if (sourceWorkbook.worksheets.length === 0) {
    return null;
  }

  sourceWorkbook.worksheets.forEach((sourceSheet) => {
    copyWorksheetFromTemplate(sourceSheet, workbook);
  });
  return {
    templateMode: "sample_package",
    templateId: template.templateId,
    templateVersion: template.templateVersion,
    sourceType: template.sourceType,
    sourcePath: template.sourcePath,
    sourceFileName: template.sourceFileName,
    sourceSha256: template.sourceSha256,
    copiedSheetCount: sourceWorkbook.worksheets.length,
    copiedSheetNames: sourceWorkbook.worksheets.map((sheet) => sheet.name),
    resolvedAt: new Date().toISOString(),
  };
}

function addLegacyInputMirrorSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("입력");
  sheet.columns = Array.from({ length: 13 }, () => ({ width: 15 }));

  sheet.mergeCells("A1:M1");
  sheet.getCell("A1").value = "입      력";
  styleTitle(sheet.getCell("A1"));
  sheet.getCell("A2").value = "1. 사업명:";
  sheet.getCell("D2").value = "본리동 주상복합 신축공사";
  sheet.getCell("H2").value = "빨간색만 입력하세요";
  sheet.getCell("A3").value = "2. 사업개요";
  sheet.getCell("C4").value = "설계 개요";
  sheet.getCell("D5").value = "대지위치:";
  sheet.getCell("E5").value = "대구광역시 달서구 본리동 1-4번지 일원";
  sheet.getCell("D6").value = "지역지구:";
  sheet.getCell("E6").value = "중심상업지역, 방화지구";

  const inputRows = [
    [8, "▶사업면적", 4691.9, 1419.29975, "공시지가", 1141000, "원/㎡"],
    [9, "▶제외면적", 227.9, 68.93975, "주변개발부지", 2214000, "원/㎡"],
    [10, "▶실대지면적", 4464, 1350.36, "", "", ""],
    [11, "▶아파트", 3112.9213, 941.6587, 0.697339, "", ""],
    [12, "▶오피스텔", 1028.8838, 311.2374, 0.230485, "", ""],
    [13, "▶상  가", 322.1948, 97.4639, 0.072176, "", ""],
  ];
  inputRows.forEach(([row, label, sqm, py, note, noteValue, unit]) => {
    sheet.getCell(Number(row), 4).value = label;
    sheet.getCell(Number(row), 5).value = sqm;
    sheet.getCell(Number(row), 6).value = py;
    sheet.getCell(Number(row), 7).value = note;
    sheet.getCell(Number(row), 8).value = noteValue;
    sheet.getCell(Number(row), 9).value = unit;
  });

  sheet.getCell("D14").value = "규    모";
  sheet.getCell("E14").value = "a) 아파트";
  sheet.getCell("F14").value = "- 지상";
  sheet.getCell("G14").value = 41;
  sheet.getCell("H14").value = "층";
  sheet.getCell("F15").value = "- 지하";
  sheet.getCell("G15").value = 4;
  sheet.getCell("H15").value = "층";
  sheet.getCell("F16").value = "- 동수";
  sheet.getCell("G16").value = 3;
  sheet.getCell("H16").value = "개동";

  sheet.getCell("D19").value = "연면적";
  sheet.getCell("E19").value = "a) 전체";
  sheet.getCell("F19").value = 54919.35;
  sheet.getCell("G19").value = 16613.103375;
  sheet.getCell("D22").value = "용적율";
  sheet.getCell("E22").value = 8.574381720430107;
  sheet.getCell("D23").value = "건축면적";
  sheet.getCell("E23").value = 900;
  sheet.getCell("D24").value = "사업기간:";
  sheet.getCell("E24").value = COMPLETION_MONTH;
  sheet.getCell("D25").value = "▶착공일:";
  sheet.getCell("E25").value = "2020-05-01";
  sheet.getCell("D26").value = "▶준공일:";
  sheet.getCell("E26").value = "2023-10-01";

  sheet.getCell("L5").value = "아파트";
  sheet.getCell("M5").value = 19535.9714;
  sheet.getCell("L6").value = "오피스텔";
  sheet.getCell("M6").value = 6457.036;
  sheet.getCell("L7").value = "상가";
  sheet.getCell("M7").value = 2022.02;
  sheet.getCell("L8").value = "계";
  setFormula(sheet.getCell("M8"), "SUM(M5:M7)", 28015.0274);

  sheet.getCell("A28").value = "나.";
  sheet.getCell("C28").value = "면적표";
  sheet.addRow(["구분", "단지", "Type", "세대수", "공급면적", null, "공용면적", null, "소계", null, "기타공용", null, "지하주차장"]);
  styleHeader(sheet.getRow(29));
  const areaRows = [
    ["아파트", 1, "84A", 162, 84.9571, 25.6995, 28.823, 8.719, 113.7801, 34.4185, 1.1596, 0.3508, 51.9064],
    [null, 1, "84B", 68, 84.8959, 25.681, 30.4559, 9.2129, 115.3518, 34.8939, 1.1588, 0.3505, 51.869],
    [null, null, "소  계", 230, 19535.9714, 5909.6313, 6740.3272, 2038.949, 26276.2986, 7948.5835, 266.6536, 80.6627, 11935.9288],
    ["오피스텔", 0, "O-84A", 38, 84.9628, 25.7012, 38.2758, 11.5784, 123.2386, 37.2797, 3.2979, 0.9976, 53.027],
    [null, 0, "O-84B", 38, 84.9592, 25.7002, 39.7645, 12.0288, 124.7237, 37.7289, 3.2978, 0.9976, 53.0247],
    [null, null, "소  계", 76, 6457.036, 1953.2534, 2965.5314, 897.0732, 9422.5674, 2850.3266, 250.6366, 75.8176, 4029.9646],
    ["상가", 1, "1F판매", 1, 1236.98, 374.1865, null, 0, 1236.98, 374.1865, 23.1793, 7.0117, 414.3964],
    [null, 1, "2F판매", 1, 785.04, 237.4746, null, 0, 785.04, 237.4746, 14.7106, 4.45, 262.9935],
    [null, null, "소  계", 2, 2022.02, 611.661, 0, 0, 2022.02, 611.661, 37.8899, 11.4617, 677.3899],
  ];
  areaRows.forEach((row) => sheet.addRow(row));

  sheet.views = [{ state: "frozen", ySplit: 4 }];
  styleSheet(sheet);
}

function addLegacySalesMirrorSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("분양");
  sheet.columns = Array.from({ length: 15 }, () => ({ width: 14 }));
  sheet.getCell("A1").value = "다. 분양 수입";
  sheet.getCell("D1").value = "본리동 주상복합 신축공사";
  sheet.addRow(["구분", null, "공급면적", "기타공용", "계약면적", "대지비", "건축비", "세대분양금액", "세대수", "공급가액", "부가가치세", "분양수입계", "세대별확장비", "확장부가세", "확장비총액"]);
  styleHeader(sheet.getRow(2));

  const rows = [
    ["아파트", 34, 34.4185, 16.0525, 50.4709, 139231, 411769, 551000, 162, 89262000, 0, 89262000, 13000, 0, 0],
    [null, 34, 34.8939, 16.0409, 50.9348, 139131, 411869, 551000, 68, 37468000, 0, 37468000, 15000, 0, 0],
  ];
  rows.forEach((row) => sheet.addRow(row));
  for (let row = 5; row <= 20; row += 1) sheet.addRow([null, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, null, 0, 0]);
  sheet.addRow([null, "소  계", 7948.5835, 3691.2812, 11639.8615, 32016330, 94713670, 126730000, 230, 126730000, 0, 126730000, 3126000, 0, 0]);

  sheet.addRow(["오피스텔", 37, 37.2797, 17.0383, 54.318, 139241, 202759, 342000, 38, 12295559.818, 700440.182, 12996000, null, null, null]);
  sheet.addRow([null, 37, 37.7289, 17.0376, 54.7665, 139235, 202765, 342000, 38, 12295539.091, 700460.909, 12996000, null, null, null]);
  for (let row = 24; row <= 29; row += 1) sheet.addRow([null, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, null, null, null]);
  sheet.addRow([null, "소  계", 2850.3266, 1294.8819, 4145.2085, 10582088, 15409912, 25992000, 76, 24591098.909, 1400901.091, 25992000, null, null, null]);

  sheet.addRow(["상가", "B1", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, null, null, null]);
  sheet.addRow([null, "1F판매", 374.1865, 132.3666, 506.5531, 2027216, 10130058, 12157274, 1, 11236360.018, 920914.364, 12157274.382, null, null, null]);
  sheet.addRow([null, "2F판매", 237.4746, 84.0055, 321.4801, 1286558, 1928243, 3214801, 1, 3039506.084, 175294.818, 3214800.903, null, null, null]);
  for (let row = 34; row <= 39; row += 1) sheet.addRow([null, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, null, null, null]);
  sheet.addRow([null, "소  계", 611.661, 216.3721, 828.0332, null, null, 15372075, 2, 14275866.103, 1096209.182, 15372075.285, null, null, null]);
  sheet.addRow(["합  계", null, null, null, null, null, null, null, null, null, null, null, null, null, null]);
  setFormula(sheet.getCell("J41"), "SUM(J21,J30,J40)", eokToThousand(assumptions.totalRevenue));
  setFormula(sheet.getCell("K41"), "SUM(K21,K30,K40)", 2497110.273);
  setFormula(sheet.getCell("L41"), "SUM(L21,L30,L40)", 168094075.285);
  sheet.addRow(["총계(분양가+확장비)", null, null, null, null, null, null, null, null, null, null, null, null, null, null]);
  setFormula(sheet.getCell("J42"), "J41", eokToThousand(assumptions.totalRevenue));
  setFormula(sheet.getCell("K42"), "K41", 2497110.273);
  setFormula(sheet.getCell("L42"), "L41", 168094075.285);

  [21, 30, 40, 41, 42].forEach((row) => {
    sheet.getRow(row).font = { bold: true };
  });
  styleSheet(sheet);
}

function addLegacyBusinessPlanMirrorSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("사업수지");
  sheet.columns = Array.from({ length: 14 }, () => ({ width: 15 }));
  sheet.mergeCells("A1:N1");
  sheet.getCell("A1").value = "사 업 계 획 서(총괄)";
  styleTitle(sheet.getCell("A1"));
  sheet.getCell("A2").value = "(단위 : 천원, VAT 별도)";
  sheet.getCell("A3").value = "사   업   명";
  setFormula(sheet.getCell("C3"), "'입력'!D2", "본리동 주상복합 신축공사");
  sheet.getCell("E3").value = "위 치";
  setFormula(sheet.getCell("F3"), "'입력'!E5", "대구광역시 달서구 본리동 1-4번지 일원");
  sheet.getCell("I3").value = "사업부지면적";
  setFormula(sheet.getCell("J3"), "'입력'!F8", 1419.29975);

  sheet.getRow(5).values = ["사업개요", "구분", null, "계", "평당가", "아파트", "평당가", "오피스텔", "평당가", "근린생활시설", "평당가", "용적율"];
  styleHeader(sheet.getRow(5));
  sheet.getRow(6).values = [null, "실대지면적/연면적(평)", null, 1350.36, 16613.1034, 941.6587, 11639.8729, 311.2374, 4145.2112, 97.4639, 828.0333, 8.5744];
  sheet.getRow(7).values = [null, "금액", null, "금액", "평당가", "금액", "평당가", "금액", "평당가", "금액", "평당가", "비고"];

  sheet.getCell("A8").value = "수입";
  sheet.getCell("C8").value = "소계";
  setFormula(sheet.getCell("D8"), "'분양'!J41", eokToThousand(assumptions.totalRevenue));
  setFormula(sheet.getCell("F8"), "'분양'!J21", eokToThousand(productMix[0]!.totalRevenue));
  setFormula(sheet.getCell("H8"), "'분양'!J30", eokToThousand(productMix[1]!.totalRevenue));
  setFormula(sheet.getCell("J8"), "'분양'!J40", eokToThousand(productMix[2]!.totalRevenue));
  sheet.getCell("C9").value = "확장비";
  sheet.getCell("D9").value = 0;
  sheet.getCell("C10").value = "기타수입";
  sheet.getCell("D10").value = 0;
  sheet.getCell("C11").value = "총수입비용";
  setFormula(sheet.getCell("D11"), "SUM(D8:D10)", eokToThousand(assumptions.totalRevenue));

  const costRows = [
    [13, "공사비", "순공사비", eokToThousand(714.36), "확장공사비 포함"],
    [15, null, "철거", eokToThousand(4), null],
    [16, null, "도로,공원공사비", eokToThousand(2), null],
    [18, null, "공사비 소계", "SUM(D13:D17)", null],
    [19, "대지비", "대지구입비", eokToThousand(420), "전체 1,419.3평"],
    [20, null, "등기 비용", eokToThousand(20.16), "대지비의 4.8%"],
    [21, null, "토지용역비", eokToThousand(15.12), null],
    [22, null, "명도비용", eokToThousand(5.04), null],
    [26, null, "소  계", "SUM(D19:D25)", null],
    [27, "용역비", "설계비", eokToThousand(11.629), "70천원/평"],
    [28, null, "감리비", eokToThousand(16.613), "100천원/평"],
    [29, null, "교통영향평가", eokToThousand(3), null],
    [30, null, "지구단위계획", eokToThousand(3), null],
    [31, null, "인허가비", eokToThousand(1.36968), null],
    [33, null, "소  계", "SUM(D27:D32)", null],
    [34, "분양제경비", "M/H부지임차료", eokToThousand(3), null],
    [35, null, "M/H건립비", eokToThousand(12), null],
    [36, null, "M/H운영비", eokToThousand(1.2), null],
    [37, null, "광고선전비", eokToThousand(10.0856), null],
    [38, null, "영업비", eokToThousand(21.0705), null],
    [39, null, "분양보증 외", eokToThousand(14.8259), null],
    [40, null, "기타경비", eokToThousand(1), null],
    [41, null, "소  계", "SUM(D34:D40)", null],
    [52, "각종부담금", "소  계", eokToThousand(assumptions.chargesCost), null],
    [62, "금융비용", "소  계", eokToThousand(assumptions.financeCost), null],
    [71, "기타", "소  계", eokToThousand(assumptions.otherCost), null],
  ] as const;

  const subtotalFormulaResults: Record<number, number> = {
    18: eokToThousand(714.36 + 4 + 2),
    26: eokToThousand(420 + 20.16 + 15.12 + 5.04),
    33: eokToThousand(11.629 + 16.613 + 3 + 3 + 1.36968),
    41: eokToThousand(3 + 12 + 1.2 + 10.0856 + 21.0705 + 14.8259 + 1),
  };

  costRows.forEach(([row, category, item, amountOrFormula, note]) => {
    sheet.getCell(row, 2).value = category;
    sheet.getCell(row, 3).value = item;
    if (typeof amountOrFormula === "string") {
      setFormula(sheet.getCell(row, 4), amountOrFormula, subtotalFormulaResults[row] ?? 0);
    } else {
      sheet.getCell(row, 4).value = amountOrFormula;
    }
    sheet.getCell(row, 12).value = note;
  });
  setFormula(sheet.getCell("D72"), "SUM(D18,D26,D33,D41,D52,D62,D71)", eokToThousand(assumptions.landCost + assumptions.constructionCost + assumptions.designCost + assumptions.salesCost + assumptions.chargesCost + assumptions.financeCost + assumptions.otherCost));
  sheet.getCell("B72").value = "총투입비용";
  setFormula(sheet.getCell("D74"), "D11-D72", eokToThousand(assumptions.totalRevenue - (assumptions.landCost + assumptions.constructionCost + assumptions.designCost + assumptions.salesCost + assumptions.chargesCost + assumptions.financeCost + assumptions.otherCost)));
  sheet.getCell("A74").value = "경상이익";

  [8, 11, 18, 26, 33, 41, 52, 62, 71, 72, 74].forEach((row) => {
    sheet.getRow(row).font = { bold: true };
  });
  styleSheet(sheet);
}

function addLegacyCashFlowMirrorSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("2.현금흐름");
  const monthStartCol = 6;
  sheet.columns = [
    { width: 12 },
    { width: 18 },
    { width: 24 },
    { width: 14 },
    { width: 14 },
    ...Array.from({ length: MONTH_COUNT }, () => ({ width: 11 })),
  ];
  sheet.mergeCells("A1:CH1");
  sheet.getCell("A1").value = "* 현금흐름";
  sheet.getCell("E1").value = "(본리동 주상복합 신축공사)";
  styleTitle(sheet.getCell("A1"));
  sheet.getCell("A3").value = "구  분";
  sheet.getCell("D3").value = "년   도";
  sheet.getCell("D4").value = "월   차";
  sheet.getCell("E4").value = "합계";
  for (let month = 1; month <= MONTH_COUNT; month += 1) {
    sheet.getCell(4, monthStartCol + month - 1).value = month;
  }
  styleHeader(sheet.getRow(4));

  const labelRows = [
    [31, "수입", "분양수입금", "아파트", "수납금율", 1],
    [32, null, null, null, "총매출액", eokToMillion(productMix[0]!.totalRevenue)],
    [33, null, null, null, "계약금", eokToMillion(productMix[0]!.totalRevenue * 0.1)],
    [34, null, null, null, "중잔금", eokToMillion(productMix[0]!.totalRevenue * 0.9)],
    [35, null, null, null, "분양수입소계", eokToMillion(productMix[0]!.totalRevenue)],
    [37, null, null, "오피스텔", "수납금율", 1],
    [38, null, null, null, "총매출액", eokToMillion(productMix[1]!.totalRevenue)],
    [39, null, null, null, "계약금", eokToMillion(productMix[1]!.totalRevenue * 0.1)],
    [40, null, null, null, "중,잔금", eokToMillion(productMix[1]!.totalRevenue * 0.9)],
    [41, null, null, null, "분양수입소계", eokToMillion(productMix[1]!.totalRevenue)],
    [42, null, null, "상가", "수납금율", 1],
    [43, null, null, null, "총매출액", eokToMillion(productMix[2]!.totalRevenue)],
    [44, null, null, null, "계약금", eokToMillion(productMix[2]!.totalRevenue * 0.1)],
    [45, null, null, null, "중잔금", eokToMillion(productMix[2]!.totalRevenue * 0.9)],
    [46, null, null, null, "분양수입소계", eokToMillion(productMix[2]!.totalRevenue)],
    [47, null, "계", null, null, eokToMillion(assumptions.totalRevenue)],
    [48, null, "기타", "대출금(Tr.A)", null, eokToMillion(assumptions.pfLoan)],
    [55, null, null, "시행자투자자금", null, 5000],
    [56, null, "합  계", null, null, eokToMillion(assumptions.totalRevenue + assumptions.pfLoan) + 5000],
    [58, "지출", "공사비", "순공사비", null, eokToMillion(assumptions.constructionCost)],
    [63, null, "대지비", "대지구입비", null, eokToMillion(420)],
    [69, null, null, "소계", null, eokToMillion(assumptions.landCost)],
    [70, null, "용역비", "설계/감리/인허가", null, eokToMillion(assumptions.designCost)],
    [76, null, "분양제경비", "소계", null, eokToMillion(assumptions.salesCost)],
    [83, null, "각종부담금", "소계", null, eokToMillion(assumptions.chargesCost)],
    [93, null, "금융비용", "대출금상환(Tr.A)", null, eokToMillion(assumptions.pfLoan)],
    [97, null, null, "대출이자(Tr.A)", null, eokToMillion(87.87)],
    [101, null, null, "대출수수료", null, eokToMillion(10)],
    [107, null, null, "신탁수수료", null, eokToMillion(8.4)],
    [111, null, "기타", "시행사투자자금회수", null, 5000],
    [120, null, "투입금액소계", null, null, eokToMillion(assumptions.landCost + assumptions.constructionCost + assumptions.designCost + assumptions.salesCost + assumptions.chargesCost + assumptions.financeCost + assumptions.otherCost + assumptions.pfLoan + 50)],
    [122, "현금흐름", "자금수지", "당기", null, 0],
    [124, null, null, "누계", null, 0],
    [128, null, "누계 수지 차", null, null, 0],
    [130, null, "누계 현금 흐름", null, null, 0],
  ] as const;
  labelRows.forEach(([row, a, b, c, d, e]) => {
    sheet.getCell(row, 1).value = a;
    sheet.getCell(row, 2).value = b;
    sheet.getCell(row, 3).value = c;
    sheet.getCell(row, 4).value = d;
    sheet.getCell(row, 5).value = e;
    setMoneyFormat(sheet.getCell(row, 5));
  });

  const monthlyResults = buildMonthlyResults();
  let cumulativeCashFlowMillion = 0;

  for (let month = 1; month <= MONTH_COUNT; month += 1) {
    const col = monthStartCol + month - 1;
    const column = columnName(col);
    const cfColumn = columnName(month + 3);
    const result = monthlyResults[month - 1]!;
    const salesCollectionMillion = result.salesRevenue * 100;
    const pfDrawMillion = month === 1 ? eokToMillion(assumptions.pfLoan) : 0;
    const equityMillion = month === 1 ? 5000 : 0;
    const cashInMillion = salesCollectionMillion + pfDrawMillion + equityMillion;
    const cashOutMillion = result.cashOut * 100;
    const netCashFlowMillion = cashInMillion - cashOutMillion;
    cumulativeCashFlowMillion += netCashFlowMillion;

    setFormula(sheet.getCell(47, col), `'03_월별현금흐름'!${cfColumn}7*100`, salesCollectionMillion);
    setFormula(sheet.getCell(48, col), month === 1 ? "$E$48" : "0", pfDrawMillion);
    setFormula(sheet.getCell(55, col), month === 1 ? "$E$55" : "0", equityMillion);
    setFormula(sheet.getCell(56, col), `SUM(${column}47:${column}55)`, cashInMillion);
    setFormula(sheet.getCell(120, col), `'03_월별현금흐름'!${cfColumn}18*100`, cashOutMillion);
    setFormula(sheet.getCell(122, col), `${column}56-${column}120`, netCashFlowMillion);
    setFormula(sheet.getCell(124, col), month === 1 ? `${column}122` : `${columnName(col - 1)}124+${column}122`, cumulativeCashFlowMillion);
    setFormula(sheet.getCell(128, col), `${column}124`, cumulativeCashFlowMillion);
    setFormula(sheet.getCell(130, col), `${column}124`, cumulativeCashFlowMillion);
  }
  [47, 56, 120, 122, 124, 128, 130].forEach((row) => {
    sheet.getRow(row).font = { bold: true };
  });
  sheet.views = [{ state: "frozen", xSplit: 5, ySplit: 4 }];
  styleSheet(sheet);
}

function addReadMeSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("00_사용안내");
  sheet.columns = [
    { key: "label", width: 24 },
    { key: "value", width: 90 },
  ];

  sheet.mergeCells("A1:B1");
  sheet.getCell("A1").value = "REALYTICS 계산 검증형 Cash Flow Workbook";
  styleTitle(sheet.getCell("A1"));
  sheet.getRow(1).height = 26;

  sheet.addRows([
    [],
    ["목적", "사업수지, 월별 Cash Flow, Max Minus, LTV/LTC/Exit 분양률을 Excel 수식으로 검산할 수 있게 만든 다운로드 파일입니다."],
    ["입력 셀", "01_입력가정 시트의 노란색 셀입니다. 사용자가 값을 바꾸면 다른 계산 시트가 다시 계산됩니다."],
    ["수식 셀", "02_사업수지, 03_월별현금흐름, 04_검증 시트의 파란색 셀입니다. 계산 결과가 값이 아니라 Excel formula로 저장됩니다."],
    ["검증 방식", "04_검증 시트에서 월별 합계와 입력가정이 일치하는지, 총사업비와 Max Minus 산식이 맞는지 확인합니다."],
    ["수식 지도", "05_수식지도 시트에서 주요 계산 항목의 셀 위치, 수식, 의미, 원천 입력을 확인할 수 있습니다."],
    ["샘플 대사", "06_샘플대사 시트에서 본리동 샘플 workbook의 주요 시트/셀과 export workbook의 대응 위치를 확인할 수 있습니다."],
    ["주의", "현재 파일은 MVP용 계산 모델입니다. 실제 승인 전에는 원본 사업수지 Excel, 토지조서, 시장 Evidence, PF 약정조건과 대사해야 합니다."],
  ]);

  sheet.getColumn(1).font = { bold: true };
  styleSheet(sheet);
}

function addInputSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("01_입력가정");
  sheet.columns = [
    { key: "section", width: 20 },
    { key: "label", width: 34 },
    { key: "value", width: 18 },
    { key: "unit", width: 12 },
    { key: "note", width: 54 },
  ];

  sheet.mergeCells("A1:E1");
  sheet.getCell("A1").value = "입력가정";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["구분", "항목", "입력값", "단위", "비고"]);
  styleHeader(sheet.getRow(3));

  const rows = [
    ["사업개요", "사업명", assumptions.projectName, "", "보고서/프로젝트 기본정보"],
    ["공통", "표시 단위", assumptions.unit, "", "모든 금액은 억원 기준"],
    ["수입", "총분양수입", assumptions.totalRevenue, "억원", "상품별 분양수입 합계"],
    ["비용", "대지비", assumptions.landCost, "억원", "샘플 사업수지!D26 소계"],
    ["비용", "공사비", assumptions.constructionCost, "억원", "샘플 사업수지!D18 공사비 소계"],
    ["비용", "용역비", assumptions.designCost, "억원", "샘플 사업수지!D33 소계"],
    ["비용", "분양제경비", assumptions.salesCost, "억원", "샘플 사업수지!D41 소계"],
    ["비용", "각종부담금", assumptions.chargesCost, "억원", "샘플 사업수지!D52 소계"],
    ["비용", "금융비용", assumptions.financeCost, "억원", "샘플 사업수지!D62 소계"],
    ["비용", "기타사업비", assumptions.otherCost, "억원", "샘플 사업수지!D71 소계"],
    ["금융", "PF 대출금", assumptions.pfLoan, "억원", "승인 또는 요청 대출금"],
    ["기간", "분석기간", assumptions.analysisMonths, "개월", "샘플 공사기간 42개월 + 준공 후 3개월"],
    ["기간", "분양수입 유입 시작월", assumptions.salesStartMonth, "월", "샘플 2.현금흐름의 분양 개시 구조를 단순화"],
    ["기간", "공사비 지출 시작월", assumptions.constructionStartMonth, "월", "M3부터 균등 지출"],
    ["기간", "준공월", assumptions.completionMonth, "월", "샘플 입력!E24 사업기간 42개월"],
    ["PF", "PF 연 이자율", assumptions.pfInterestRate, "%", "PF 인출/상환 시트의 월 이자 계산"],
    ["PF", "준공 전 상환적립률", assumptions.preCompletionRepaymentReserveRatio, "%", "샘플의 준공 전 분양수입 일부 상환적립 구조"],
    ["PF", "준공 후 상환적립률", assumptions.postCompletionRepaymentReserveRatio, "%", "준공 후 분양수입 100% 상환적립 가정"],
    ["시나리오", "Cash Flow 수입 시나리오", assumptions.revenueScenario, "Base/Trigger", "Base는 원 수납스케줄, Trigger는 할인 Trigger 조정수입을 반영"],
    ["출처", "참고 샘플", assumptions.sourceWorkbook, "", "샘플의 주요 시트 구조와 핵심 금액을 반영"],
  ];

  sheet.addRows(rows);
  sheet.getCell(22, 3).dataValidation = {
    type: "list",
    allowBlank: false,
    formulae: ['"Base,Trigger"'],
  };

  for (let rowIndex = 4; rowIndex <= sheet.rowCount; rowIndex += 1) {
    const valueCell = sheet.getCell(rowIndex, 3);
    valueCell.fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: "FFFFF7CC" },
    };
    valueCell.protection = { locked: false };
    if (typeof valueCell.value === "number") {
      valueCell.numFmt = "#,##0.0";
    }
  }

  sheet.views = [{ state: "frozen", ySplit: 3 }];
  styleSheet(sheet);
}

function addProductMixSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("02A_분양수입");
  sheet.columns = [
    { key: "type", width: 20 },
    { key: "units", width: 14 },
    { key: "revenue", width: 18 },
    { key: "share", width: 14 },
    { key: "avgUnitPrice", width: 18 },
    { key: "source", width: 54 },
  ];

  sheet.mergeCells("A1:F1");
  sheet.getCell("A1").value = "상품별 분양수입";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["상품유형", "세대/호실수", "분양수입", "수입비중", "세대/호실당 금액", "샘플 근거"]);
  styleHeader(sheet.getRow(3));

  productMix.forEach((item, index) => {
    const row = index + 4;
    sheet.getCell(row, 1).value = item.type;
    sheet.getCell(row, 2).value = item.units;
    sheet.getCell(row, 3).value = item.totalRevenue;
    setFormula(sheet.getCell(row, 4), `IF($C$9=0,0,C${row}/$C$9)`, item.totalRevenue / assumptions.totalRevenue);
    setFormula(sheet.getCell(row, 5), `IF(B${row}=0,0,C${row}/B${row})`, item.totalRevenue / item.units);
    sheet.getCell(row, 6).value = item.source;
  });

  sheet.getCell(9, 1).value = "합계";
  setFormula(sheet.getCell(9, 2), "SUM(B4:B6)", productMix.reduce((sum, item) => sum + item.units, 0));
  setFormula(sheet.getCell(9, 3), "SUM(C4:C6)", productMix.reduce((sum, item) => sum + item.totalRevenue, 0));
  setFormula(sheet.getCell(9, 4), "IF('01_입력가정'!$C$6=0,0,C9/'01_입력가정'!$C$6)", 1);
  sheet.getCell(9, 6).value = "샘플 분양/사업수지 시트의 상품별 분양수입 구조 반영";

  for (let row = 4; row <= 9; row += 1) {
    setMoneyFormat(sheet.getCell(row, 3));
    setPercentFormat(sheet.getCell(row, 4));
    setMoneyFormat(sheet.getCell(row, 5));
  }

  sheet.getRow(9).font = { bold: true };
  styleSheet(sheet);
}

function addCollectionScheduleSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("02B_수납스케줄");
  const monthColumns = Array.from({ length: MONTH_COUNT }, (_, index) => ({
    key: `m${index + 1}`,
    width: 11,
  }));

  sheet.columns = [
    { key: "productType", width: 18 },
    { key: "stage", width: 18 },
    { key: "dueMonth", width: 12 },
    { key: "rate", width: 12 },
    { key: "productRevenue", width: 18 },
    { key: "amount", width: 18 },
    ...monthColumns,
  ];

  sheet.mergeCells(`A1:${columnName(COLLECTION_START_COLUMN + MONTH_COUNT - 1)}1`);
  sheet.getCell("A1").value = "상품별 분양대금 수납 스케줄";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow([
    "상품유형",
    "납입단계",
    "수납월",
    "납입비율",
    "상품별 분양수입",
    "단계별 수납액",
    ...Array.from({ length: MONTH_COUNT }, (_, index) => `M${index + 1}`),
  ]);
  styleHeader(sheet.getRow(3));

  collectionRows.forEach((row, index) => {
    const excelRow = index + 4;
    sheet.getCell(excelRow, 1).value = row.productType;
    sheet.getCell(excelRow, 2).value = row.label;
    sheet.getCell(excelRow, 3).value = row.dueMonth;
    sheet.getCell(excelRow, 4).value = row.collectionRate;
    sheet.getCell(excelRow, 5).value = row.productRevenue;
    setFormula(sheet.getCell(excelRow, 6), `D${excelRow}*E${excelRow}`, row.amount);

    for (let month = 1; month <= MONTH_COUNT; month += 1) {
      const col = COLLECTION_START_COLUMN + month - 1;
      const column = columnName(col);
      setFormula(
        sheet.getCell(excelRow, col),
        `IF(${column}$3=\"M\"&$C${excelRow},$F${excelRow},0)`,
        row.dueMonth === month ? row.amount : 0,
      );
      setMoneyFormat(sheet.getCell(excelRow, col));
    }
  });

  sheet.getCell(collectionTotalRow, 1).value = "합계";
  setFormula(sheet.getCell(collectionTotalRow, 5), `SUM(E4:E${collectionTotalRow - 1})`, productMix.reduce((sum, item) => sum + item.totalRevenue * getCollectionSteps(item.type).length, 0));
  setFormula(sheet.getCell(collectionTotalRow, 6), `SUM(F4:F${collectionTotalRow - 1})`, assumptions.totalRevenue);
  for (let month = 1; month <= MONTH_COUNT; month += 1) {
    const col = COLLECTION_START_COLUMN + month - 1;
    const column = columnName(col);
    const amount = salesCollectionForMonth(month);
    setFormula(
      sheet.getCell(collectionTotalRow, col),
      `SUM(${column}4:${column}${collectionTotalRow - 1})`,
      amount,
    );
    setMoneyFormat(sheet.getCell(collectionTotalRow, col));
  }

  for (let row = 4; row <= collectionTotalRow; row += 1) {
    setPercentFormat(sheet.getCell(row, 4));
    setMoneyFormat(sheet.getCell(row, 5));
    setMoneyFormat(sheet.getCell(row, 6));
  }

  sheet.getRow(collectionTotalRow).font = { bold: true };
  sheet.views = [{ state: "frozen", xSplit: 6, ySplit: 3 }];
  styleSheet(sheet);
}

function addProgressSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("03A_공정률");
  const monthColumns = Array.from({ length: MONTH_COUNT }, (_, index) => ({
    key: `m${index + 1}`,
    width: 10,
  }));

  sheet.columns = [
    { key: "category", width: 22 },
    { key: "item", width: 24 },
    { key: "total", width: 14 },
    ...monthColumns,
  ];

  sheet.mergeCells(`A1:${columnName(MONTH_COUNT + 3)}1`);
  sheet.getCell("A1").value = "공정률 및 분양률 가정";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["구분", "항목", "합계", ...Array.from({ length: MONTH_COUNT }, (_, index) => `M${index + 1}`)]);
  styleHeader(sheet.getRow(3));

  sheet.getCell(5, 1).value = "공정";
  sheet.getCell(5, 2).value = "월공정률";
  sheet.getCell(6, 1).value = "공정";
  sheet.getCell(6, 2).value = "누적공정률";
  sheet.getCell(8, 1).value = "분양";
  sheet.getCell(8, 2).value = "월분양률";
  sheet.getCell(9, 1).value = "분양";
  sheet.getCell(9, 2).value = "누적분양률";

  for (let month = 1; month <= MONTH_COUNT; month += 1) {
    const col = month + 3;
    const column = columnName(col);
    const prevColumn = columnName(col - 1);
    const constructionResult = sCurveMonthlyProgress(month);
    const cumulativeConstruction = sCurveCumulative(month);
    const monthlySalesRate =
      assumptions.totalRevenue === 0
        ? 0
        : salesCollectionForMonth(month) / assumptions.totalRevenue;
    const cumulativeSales = Math.min(
      1,
      Array.from({ length: month }, (_, index) =>
        assumptions.totalRevenue === 0
          ? 0
          : salesCollectionForMonth(index + 1) / assumptions.totalRevenue,
      ).reduce((sum, value) => sum + value, 0),
    );

    setFormula(
      sheet.getCell(5, col),
      `MAX(0,MIN(1,(1-COS(PI()*(COLUMN()-3-'01_입력가정'!$C$17+1)/('01_입력가정'!$C$18-'01_입력가정'!$C$17+1)))/2)-IF(COLUMN()-3<='01_입력가정'!$C$17,0,MIN(1,(1-COS(PI()*(COLUMN()-3-'01_입력가정'!$C$17)/('01_입력가정'!$C$18-'01_입력가정'!$C$17+1)))/2)))`,
      constructionResult,
    );
    setFormula(
      sheet.getCell(6, col),
      col === 4 ? `${column}5` : `MIN(1,${prevColumn}6+${column}5)`,
      cumulativeConstruction,
    );
    setFormula(
      sheet.getCell(8, col),
      `IFERROR(INDEX('02B_수납스케줄'!$G$${collectionTotalRow}:$${collectionLastColumn}$${collectionTotalRow},1,COLUMN()-3)/'01_입력가정'!$C$6,0)`,
      monthlySalesRate,
    );
    setFormula(
      sheet.getCell(9, col),
      col === 4 ? `${column}8` : `MIN(1,${prevColumn}9+${column}8)`,
      cumulativeSales,
    );
    [5, 6, 8, 9].forEach((row) => setPercentFormat(sheet.getCell(row, col)));
  }

  setFormula(sheet.getCell(5, 3), `SUM(D5:${columnName(MONTH_COUNT + 3)}5)`, 1);
  setFormula(sheet.getCell(6, 3), `${columnName(MONTH_COUNT + 3)}6`, 1);
  setFormula(sheet.getCell(8, 3), `SUM(D8:${columnName(MONTH_COUNT + 3)}8)`, 1);
  setFormula(sheet.getCell(9, 3), `${columnName(MONTH_COUNT + 3)}9`, 1);
  [5, 6, 8, 9].forEach((row) => setPercentFormat(sheet.getCell(row, 3)));

  sheet.getRow(6).font = { bold: true };
  sheet.getRow(9).font = { bold: true };
  sheet.views = [{ state: "frozen", xSplit: 3, ySplit: 3 }];
  styleSheet(sheet);
}

function addFeasibilitySheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("02_사업수지");
  sheet.columns = [
    { key: "section", width: 22 },
    { key: "item", width: 34 },
    { key: "amount", width: 18 },
    { key: "unit", width: 12 },
    { key: "formula", width: 60 },
  ];

  sheet.mergeCells("A1:E1");
  sheet.getCell("A1").value = "사업수지 및 PF 기본지표";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["구분", "항목", "계산값", "단위", "주요 수식"]);
  styleHeader(sheet.getRow(3));

  const rows = [
    [4, "수입", "총분양수입", `=${inputCell(6)}`, assumptions.totalRevenue, "억원", "입력가정 총분양수입"],
    [6, "비용", "대지비", `=${inputCell(7)}`, assumptions.landCost, "억원", "입력가정 대지비"],
    [7, "비용", "공사비", `=${inputCell(8)}`, assumptions.constructionCost, "억원", "입력가정 공사비"],
    [8, "비용", "용역비", `=${inputCell(9)}`, assumptions.designCost, "억원", "입력가정 용역비"],
    [9, "비용", "분양제경비", `=${inputCell(10)}`, assumptions.salesCost, "억원", "입력가정 분양제경비"],
    [10, "비용", "각종부담금", `=${inputCell(11)}`, assumptions.chargesCost, "억원", "입력가정 각종부담금"],
    [11, "비용", "금융비용", `=${inputCell(12)}`, assumptions.financeCost, "억원", "입력가정 금융비용"],
    [12, "비용", "기타사업비", `=${inputCell(13)}`, assumptions.otherCost, "억원", "입력가정 기타사업비"],
    [13, "비용", "총사업비", "=SUM(C6:C12)", assumptions.landCost + assumptions.constructionCost + assumptions.designCost + assumptions.salesCost + assumptions.chargesCost + assumptions.financeCost + assumptions.otherCost, "억원", "대지비+공사비+용역비+분양제경비+각종부담금+금융비용+기타사업비"],
    [15, "수익성", "시행이익", "=C4-C13", assumptions.totalRevenue - (assumptions.landCost + assumptions.constructionCost + assumptions.designCost + assumptions.salesCost + assumptions.chargesCost + assumptions.financeCost + assumptions.otherCost), "억원", "총분양수입-총사업비"],
    [16, "수익성", "매출이익률", "=IF(C4=0,0,C15/C4)", (assumptions.totalRevenue - (assumptions.landCost + assumptions.constructionCost + assumptions.designCost + assumptions.salesCost + assumptions.chargesCost + assumptions.financeCost + assumptions.otherCost)) / assumptions.totalRevenue, "%", "시행이익/총분양수입"],
    [17, "수익성", "원가대비 이익률", "=IF(C13=0,0,C15/C13)", (assumptions.totalRevenue - (assumptions.landCost + assumptions.constructionCost + assumptions.designCost + assumptions.salesCost + assumptions.chargesCost + assumptions.financeCost + assumptions.otherCost)) / (assumptions.landCost + assumptions.constructionCost + assumptions.designCost + assumptions.salesCost + assumptions.chargesCost + assumptions.financeCost + assumptions.otherCost), "%", "시행이익/총사업비"],
    [19, "PF 지표", "LTC", `=IF(C13=0,0,${inputCell(14)}/C13)`, assumptions.pfLoan / (assumptions.landCost + assumptions.constructionCost + assumptions.designCost + assumptions.salesCost + assumptions.chargesCost + assumptions.financeCost + assumptions.otherCost), "%", "PF 대출금/총사업비"],
    [20, "PF 지표", "분양가 LTV", `=IF(C4=0,0,${inputCell(14)}/C4)`, assumptions.pfLoan / assumptions.totalRevenue, "%", "PF 대출금/총분양수입"],
    [21, "PF 지표", "Base Exit 분양률", `=IF(C4=0,0,${inputCell(14)}/C4)`, assumptions.pfLoan / assumptions.totalRevenue, "%", "PF 대출금 회수 필요 분양률"],
  ] as const;

  rows.forEach(([rowIndex, section, item, formula, result, unit, note]) => {
    sheet.getCell(rowIndex, 1).value = section;
    sheet.getCell(rowIndex, 2).value = item;
    setFormula(sheet.getCell(rowIndex, 3), formula.slice(1), result);
    sheet.getCell(rowIndex, 4).value = unit;
    sheet.getCell(rowIndex, 5).value = note;
    sheet.getCell(rowIndex, 3).fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: "FFEFF6FF" },
    };
    if (unit === "%") {
      setPercentFormat(sheet.getCell(rowIndex, 3));
    } else {
      setMoneyFormat(sheet.getCell(rowIndex, 3));
    }
  });

  [13, 15, 19, 20, 21].forEach((rowIndex) => {
    sheet.getRow(rowIndex).font = { bold: true };
  });

  sheet.views = [{ state: "frozen", ySplit: 3 }];
  styleSheet(sheet);
}

function addCashFlowSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("03_월별현금흐름");
  const monthColumns = Array.from({ length: MONTH_COUNT }, (_, index) => ({
    key: `m${index + 1}`,
    width: 12,
  }));
  sheet.columns = [
    { key: "category", width: 22 },
    { key: "item", width: 28 },
    { key: "total", width: 16 },
    ...monthColumns,
  ];

  sheet.mergeCells("A1:AA1");
  sheet.getCell("A1").value = "월별 Cash Flow";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["구분", "항목", "합계", ...Array.from({ length: MONTH_COUNT }, (_, index) => `M${index + 1}`)]);
  styleHeader(sheet.getRow(3));

  const monthIndexRow = 4;
  sheet.getCell(monthIndexRow, 1).value = "월";
  sheet.getCell(monthIndexRow, 2).value = "월 인덱스";
  sheet.getCell(monthIndexRow, 3).value = "";
  for (let month = 1; month <= MONTH_COUNT; month += 1) {
    sheet.getCell(monthIndexRow, month + 3).value = month;
  }

  const results = buildMonthlyResults();
  const rowDefs = [
    { row: 7, category: "Cash In", item: "분양수입", key: "salesRevenue" },
    { row: 8, category: "Cash In", item: "현금유입 합계", key: "cashIn", totalFormula: "=SUM(D7:AV7)" },
    { row: 10, category: "Cash Out", item: "대지비", key: "landCost" },
    { row: 11, category: "Cash Out", item: "공사비", key: "constructionCost" },
    { row: 12, category: "Cash Out", item: "용역비", key: "designCost" },
    { row: 13, category: "Cash Out", item: "분양제경비", key: "salesCost" },
    { row: 14, category: "Cash Out", item: "각종부담금", key: "chargesCost" },
    { row: 15, category: "Cash Out", item: "금융비용", key: "financeCost" },
    { row: 16, category: "Cash Out", item: "기타사업비", key: "otherCost" },
    { row: 18, category: "Cash Out", item: "현금유출 합계", key: "cashOut", totalFormula: "=SUM(D18:AV18)" },
    { row: 20, category: "Net CF", item: "월별 순현금흐름", key: "netCashFlow", totalFormula: "=SUM(D20:AV20)" },
    { row: 21, category: "Cumulative CF", item: "누적 현금흐름", key: "cumulative", totalFormula: "=AV21" },
    { row: 22, category: "Max Minus", item: "누적 최저점", key: "maxMinusToDate", totalFormula: "=MIN(D21:AV21)" },
    { row: 23, category: "PF", item: "누적 PF 인출 필요액", key: "pfDrawNeed", totalFormula: "=MAX(D23:AV23)" },
  ] as const;

  const formulaByRow = (row: number, col: number) => {
    const column = columnName(col);
    const prevColumn = columnName(col - 1);
    switch (row) {
      case 7:
        return `IF(${inputCell(22)}=\"Trigger\",'04B_할인Trigger'!${columnName(col + 1)}16,INDEX('02B_수납스케줄'!$G$${collectionTotalRow}:$${collectionLastColumn}$${collectionTotalRow},1,${column}$4))`;
      case 8:
        return `SUM(${column}7:${column}7)`;
      case 10:
        return `IF(${column}$4=1,${inputCell(7)},0)`;
      case 11:
        return `${inputCell(8)}*'03A_공정률'!${column}5`;
      case 12:
        return `${inputCell(9)}/${inputCell(15)}`;
      case 13:
        return `IF(${inputCell(6)}=0,0,${inputCell(10)}*${column}7/${inputCell(6)})`;
      case 14:
        return `IF(OR(${column}$4=${inputCell(18)}-1,${column}$4=${inputCell(18)}),${inputCell(11)}/2,0)`;
      case 15:
        return `IF(${column}$4>=${inputCell(17)},${inputCell(12)}/(${inputCell(15)}-${inputCell(17)}+1),0)`;
      case 16:
        return `${inputCell(13)}/${inputCell(15)}`;
      case 18:
        return `SUM(${column}10:${column}16)`;
      case 20:
        return `${column}8-${column}18`;
      case 21:
        return col === 4 ? `${column}20` : `${prevColumn}21+${column}20`;
      case 22:
        return `MIN($D$21:${column}21)`;
      case 23:
        return `MIN(${inputCell(14)},MAX(0,-${column}22))`;
      default:
        return "";
    }
  };

  rowDefs.forEach((rowDef) => {
    const { row, category, item, key } = rowDef;
    const totalFormula = "totalFormula" in rowDef ? rowDef.totalFormula : undefined;
    sheet.getCell(row, 1).value = category;
    sheet.getCell(row, 2).value = item;
    const totalResult =
      key === "cumulative"
        ? results[MONTH_COUNT - 1]![key]
        : key === "maxMinusToDate"
          ? Math.min(...results.map((result) => result.cumulative))
          : key === "pfDrawNeed"
            ? Math.max(...results.map((result) => result.pfDrawNeed))
            : results.reduce((sum, result) => sum + Number(result[key]), 0);
    setFormula(
      sheet.getCell(row, 3),
      totalFormula?.slice(1) ?? `SUM(D${row}:AA${row})`,
      totalResult,
    );
    setMoneyFormat(sheet.getCell(row, 3));

    for (let month = 1; month <= MONTH_COUNT; month += 1) {
      const col = month + 3;
      const result = Number(results[month - 1]![key]);
      setFormula(sheet.getCell(row, col), formulaByRow(row, col), result);
      setMoneyFormat(sheet.getCell(row, col));
    }
  });

  [8, 18, 20, 21, 22, 23].forEach((rowIndex) => {
    sheet.getRow(rowIndex).font = { bold: true };
    sheet.getRow(rowIndex).fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: rowIndex >= 18 ? "FFDBEAFE" : "FFF8FAFC" },
    };
  });

  for (let rowIndex = 7; rowIndex <= 23; rowIndex += 1) {
    for (let col = 3; col <= MONTH_COUNT + 3; col += 1) {
      sheet.getCell(rowIndex, col).fill = {
        type: "pattern",
        pattern: "solid",
        fgColor: { argb: "FFEFF6FF" },
      };
    }
  }

  sheet.views = [{ state: "frozen", xSplit: 3, ySplit: 3 }];
  sheet.autoFilter = {
    from: "A3",
    to: `${columnName(MONTH_COUNT + 3)}3`,
  };
  styleSheet(sheet);
}

function buildPfScheduleResults() {
  const monthlyResults = buildMonthlyResults();
  let beginningBalance = 0;
  let cumulativeDraw = 0;
  let previousNeed = 0;
  let cumulativeRepayment = 0;

  return monthlyResults.map((result) => {
    const drawNeed = result.pfDrawNeed;
    const draw = Math.min(
      assumptions.pfLoan - cumulativeDraw,
      Math.max(0, drawNeed - previousNeed),
    );
    const interest = (beginningBalance + draw / 2) * assumptions.pfInterestRate / 12;
    const repaymentReserve =
      result.salesRevenue *
      (result.month < assumptions.completionMonth
        ? assumptions.preCompletionRepaymentReserveRatio
        : assumptions.postCompletionRepaymentReserveRatio);
    const principalRepayment = Math.min(
      beginningBalance + draw,
      Math.max(0, repaymentReserve - interest),
    );
    const endingBalance = beginningBalance + draw - principalRepayment;
    const interestShortfall = Math.max(0, interest - repaymentReserve);
    cumulativeDraw += draw;
    cumulativeRepayment += principalRepayment;
    previousNeed = drawNeed;

    const row = {
      month: result.month,
      beginningBalance,
      drawNeed,
      draw,
      interest,
      repaymentReserve,
      principalRepayment,
      endingBalance,
      interestShortfall,
      cumulativeRepayment,
    };

    beginningBalance = endingBalance;
    return row;
  });
}

function addPfScheduleSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("03B_PF인출상환");
  const monthColumns = Array.from({ length: MONTH_COUNT }, (_, index) => ({
    key: `m${index + 1}`,
    width: 12,
  }));

  sheet.columns = [
    { key: "category", width: 22 },
    { key: "item", width: 30 },
    { key: "total", width: 16 },
    ...monthColumns,
  ];

  sheet.mergeCells(`A1:${columnName(MONTH_COUNT + 3)}1`);
  sheet.getCell("A1").value = "PF 인출 및 상환 시뮬레이션";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["구분", "항목", "합계/기말", ...Array.from({ length: MONTH_COUNT }, (_, index) => `M${index + 1}`)]);
  styleHeader(sheet.getRow(3));

  sheet.getCell(4, 1).value = "월";
  sheet.getCell(4, 2).value = "월 인덱스";
  for (let month = 1; month <= MONTH_COUNT; month += 1) {
    sheet.getCell(4, month + 3).value = month;
  }

  const results = buildPfScheduleResults();
  const rowDefs = [
    { row: 5, category: "PF", item: "기초 PF 잔액", key: "beginningBalance", totalFormula: "=AV5" },
    { row: 6, category: "PF", item: "누적 PF 필요액", key: "drawNeed", totalFormula: "=MAX(D6:AV6)" },
    { row: 7, category: "PF", item: "월별 PF 인출", key: "draw", totalFormula: "=SUM(D7:AV7)" },
    { row: 8, category: "PF", item: "월별 이자비용", key: "interest", totalFormula: "=SUM(D8:AV8)" },
    { row: 9, category: "상환", item: "상환적립 재원", key: "repaymentReserve", totalFormula: "=SUM(D9:AV9)" },
    { row: 10, category: "상환", item: "원금상환", key: "principalRepayment", totalFormula: "=SUM(D10:AV10)" },
    { row: 11, category: "PF", item: "기말 PF 잔액", key: "endingBalance", totalFormula: "=AV11" },
    { row: 12, category: "리스크", item: "이자재원 부족액", key: "interestShortfall", totalFormula: "=SUM(D12:AV12)" },
    { row: 13, category: "상환", item: "누적 원금상환", key: "cumulativeRepayment", totalFormula: "=AV13" },
  ] as const;

  const formulaByRow = (row: number, col: number) => {
    const column = columnName(col);
    const previousColumn = columnName(col - 1);
    switch (row) {
      case 5:
        return col === 4 ? "0" : `${previousColumn}11`;
      case 6:
        return `'03_월별현금흐름'!${column}23`;
      case 7:
        return col === 4
          ? `MIN(${inputCell(14)},MAX(0,${column}6))`
          : `MIN(${inputCell(14)}-SUM($D$7:${previousColumn}7),MAX(0,${column}6-${previousColumn}6))`;
      case 8:
        return `(${column}5+${column}7/2)*${inputCell(19)}/12`;
      case 9:
        return `IF(${column}$4<${inputCell(18)},'03_월별현금흐름'!${column}7*${inputCell(20)},'03_월별현금흐름'!${column}7*${inputCell(21)})`;
      case 10:
        return `MIN(${column}5+${column}7,MAX(0,${column}9-${column}8))`;
      case 11:
        return `${column}5+${column}7-${column}10`;
      case 12:
        return `MAX(0,${column}8-${column}9)`;
      case 13:
        return col === 4 ? `${column}10` : `${previousColumn}13+${column}10`;
      default:
        return "";
    }
  };

  rowDefs.forEach((rowDef) => {
    const { row, category, item, key, totalFormula } = rowDef;
    sheet.getCell(row, 1).value = category;
    sheet.getCell(row, 2).value = item;
    const totalResult =
      key === "beginningBalance" || key === "endingBalance" || key === "cumulativeRepayment"
        ? results[MONTH_COUNT - 1]![key]
        : key === "drawNeed"
          ? Math.max(...results.map((result) => result.drawNeed))
          : results.reduce((sum, result) => sum + Number(result[key]), 0);
    setFormula(sheet.getCell(row, 3), totalFormula.slice(1), totalResult);
    setMoneyFormat(sheet.getCell(row, 3));

    for (let month = 1; month <= MONTH_COUNT; month += 1) {
      const col = month + 3;
      const result = Number(results[month - 1]![key]);
      setFormula(sheet.getCell(row, col), formulaByRow(row, col), result);
      setMoneyFormat(sheet.getCell(row, col));
    }
  });

  [7, 10, 11, 12, 13].forEach((rowIndex) => {
    sheet.getRow(rowIndex).font = { bold: true };
    sheet.getRow(rowIndex).fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: rowIndex === 12 ? "FFFEE2E2" : "FFEFF6FF" },
    };
  });

  sheet.views = [{ state: "frozen", xSplit: 3, ySplit: 3 }];
  styleSheet(sheet);
}

function addTrancheWaterfallSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("03C_Tranche상환");
  sheet.columns = [
    { key: "priority", width: 10 },
    { key: "tranche", width: 12 },
    { key: "name", width: 14 },
    { key: "principal", width: 16 },
    { key: "interestRate", width: 14 },
    { key: "feeRate", width: 14 },
    { key: "interestClaim", width: 18 },
    { key: "feeClaim", width: 18 },
    { key: "totalClaim", width: 18 },
    { key: "cashBefore", width: 18 },
    { key: "payment", width: 18 },
    { key: "shortfall", width: 18 },
    { key: "recoveryRatio", width: 16 },
    { key: "status", width: 18 },
  ];

  sheet.mergeCells("A1:N1");
  sheet.getCell("A1").value = "Tranche별 상환 Waterfall";
  styleTitle(sheet.getCell("A1"));
  sheet.getCell("A2").value = "상환가능재원";
  setFormula(sheet.getCell("B2"), "'03B_PF인출상환'!C9", 0);
  setMoneyFormat(sheet.getCell("B2"));
  sheet.getCell("D2").value = "기준";
  sheet.getCell("E2").value =
    "03B_PF인출상환의 총 상환적립 재원을 Tr.A → Tr.B → Tr.C 순서로 배분";

  sheet.addRow([]);
  sheet.addRow([
    "순위",
    "Tranche",
    "구분",
    "원금",
    "금리",
    "수수료율",
    "이자 Claim",
    "수수료 Claim",
    "총 Claim",
    "배분 전 재원",
    "상환액",
    "부족액",
    "회수율",
    "상태",
  ]);
  styleHeader(sheet.getRow(4));

  trancheStructure.forEach((tranche, index) => {
    const row = index + 5;
    const previousRow = row - 1;
    sheet.getCell(row, 1).value = tranche.priority;
    sheet.getCell(row, 2).value = tranche.tranche;
    sheet.getCell(row, 3).value = tranche.name;
    sheet.getCell(row, 4).value = tranche.principal;
    sheet.getCell(row, 5).value = tranche.interestRate;
    sheet.getCell(row, 6).value = tranche.feeRate;
    setFormula(sheet.getCell(row, 7), `D${row}*E${row}`, tranche.principal * tranche.interestRate);
    setFormula(sheet.getCell(row, 8), `D${row}*F${row}`, tranche.principal * tranche.feeRate);
    setFormula(sheet.getCell(row, 9), `SUM(D${row},G${row},H${row})`, tranche.principal * (1 + tranche.interestRate + tranche.feeRate));
    setFormula(
      sheet.getCell(row, 10),
      row === 5 ? "$B$2" : `MAX(0,J${previousRow}-K${previousRow})`,
      0,
    );
    setFormula(sheet.getCell(row, 11), `MIN(J${row},I${row})`, 0);
    setFormula(sheet.getCell(row, 12), `MAX(0,I${row}-K${row})`, 0);
    setFormula(sheet.getCell(row, 13), `IF(I${row}=0,0,K${row}/I${row})`, 0);
    setFormula(
      sheet.getCell(row, 14),
      `IF(L${row}>0,"부족","상환완료")`,
      "상환완료",
    );
  });

  sheet.getCell("A9").value = "합계";
  setFormula(sheet.getCell("D9"), "SUM(D5:D7)", assumptions.pfLoan);
  setFormula(sheet.getCell("G9"), "SUM(G5:G7)", 0);
  setFormula(sheet.getCell("H9"), "SUM(H5:H7)", 0);
  setFormula(sheet.getCell("I9"), "SUM(I5:I7)", 0);
  setFormula(sheet.getCell("K9"), "SUM(K5:K7)", 0);
  setFormula(sheet.getCell("L9"), "SUM(L5:L7)", 0);
  setFormula(sheet.getCell("M9"), "IF(I9=0,0,K9/I9)", 0);

  for (let row = 5; row <= 9; row += 1) {
    [4, 7, 8, 9, 10, 11, 12].forEach((col) => setMoneyFormat(sheet.getCell(row, col)));
    [5, 6, 13].forEach((col) => setPercentFormat(sheet.getCell(row, col)));
  }

  sheet.getRow(9).font = { bold: true };
  sheet.views = [{ state: "frozen", ySplit: 4 }];
  styleSheet(sheet);
}

function addMonthlyTrancheRepaymentSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("03D_Tranche월별상환");
  const monthColumns = Array.from({ length: MONTH_COUNT }, (_, index) => ({
    key: `m${index + 1}`,
    width: 12,
  }));

  sheet.columns = [
    { key: "category", width: 22 },
    { key: "item", width: 28 },
    { key: "total", width: 16 },
    ...monthColumns,
  ];

  sheet.mergeCells(`A1:${columnName(MONTH_COUNT + 3)}1`);
  sheet.getCell("A1").value = "월별 Tranche 원금상환 배분";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["구분", "항목", "합계/기말", ...Array.from({ length: MONTH_COUNT }, (_, index) => `M${index + 1}`)]);
  styleHeader(sheet.getRow(3));

  sheet.getCell(4, 1).value = "상환재원";
  sheet.getCell(4, 2).value = "원금상환 가능액";
  setFormula(sheet.getCell(4, 3), "SUM(D4:AV4)", 0);
  setMoneyFormat(sheet.getCell(4, 3));

  const rowDefs = [
    { row: 6, category: "기초잔액", item: "Tr.A 선순위", principal: trancheStructure[0]!.principal },
    { row: 7, category: "기초잔액", item: "Tr.B 중순위", principal: trancheStructure[1]!.principal },
    { row: 8, category: "기초잔액", item: "Tr.C 후순위", principal: trancheStructure[2]!.principal },
    { row: 10, category: "원금상환", item: "Tr.A 선순위", principal: trancheStructure[0]!.principal },
    { row: 11, category: "원금상환", item: "Tr.B 중순위", principal: trancheStructure[1]!.principal },
    { row: 12, category: "원금상환", item: "Tr.C 후순위", principal: trancheStructure[2]!.principal },
    { row: 14, category: "기말잔액", item: "Tr.A 선순위", principal: trancheStructure[0]!.principal },
    { row: 15, category: "기말잔액", item: "Tr.B 중순위", principal: trancheStructure[1]!.principal },
    { row: 16, category: "기말잔액", item: "Tr.C 후순위", principal: trancheStructure[2]!.principal },
  ] as const;

  rowDefs.forEach((rowDef) => {
    sheet.getCell(rowDef.row, 1).value = rowDef.category;
    sheet.getCell(rowDef.row, 2).value = rowDef.item;
  });

  sheet.getCell(18, 1).value = "합계";
  sheet.getCell(18, 2).value = "기말 PF 잔액";
  setFormula(sheet.getCell(18, 3), "AV18", 0);
  setMoneyFormat(sheet.getCell(18, 3));

  for (let month = 1; month <= MONTH_COUNT; month += 1) {
    const col = month + 3;
    const column = columnName(col);
    const prevColumn = columnName(col - 1);

    setFormula(sheet.getCell(4, col), `'03B_PF인출상환'!${column}10`, 0);

    trancheStructure.forEach((tranche, index) => {
      const beginRow = 6 + index;
      const repayRow = 10 + index;
      const endingRow = 14 + index;
      const previousEndingRow = endingRow;

      setFormula(
        sheet.getCell(beginRow, col),
        col === 4 ? `${tranche.principal}` : `${prevColumn}${previousEndingRow}`,
        tranche.principal,
      );

      const priorRepaymentRows =
        index === 0
          ? "0"
          : index === 1
            ? `${column}10`
            : `SUM(${column}10:${column}11)`;
      setFormula(
        sheet.getCell(repayRow, col),
        `MIN(${column}${beginRow},MAX(0,${column}4-${priorRepaymentRows}))`,
        0,
      );
      setFormula(
        sheet.getCell(endingRow, col),
        `${column}${beginRow}-${column}${repayRow}`,
        tranche.principal,
      );
    });

    setFormula(sheet.getCell(18, col), `SUM(${column}14:${column}16)`, assumptions.pfLoan);
    [4, 6, 7, 8, 10, 11, 12, 14, 15, 16, 18].forEach((row) =>
      setMoneyFormat(sheet.getCell(row, col)),
    );
  }

  [4, 10, 11, 12, 14, 15, 16, 18].forEach((row) => {
    sheet.getRow(row).font = { bold: true };
  });
  sheet.views = [{ state: "frozen", xSplit: 3, ySplit: 3 }];
  styleSheet(sheet);
}

function addDiscountSensitivitySheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("04A_할인민감도");
  const scenarioStartCol = 4;
  const scenarioEndCol = scenarioStartCol + discountScenarios.length - 1;

  sheet.columns = [
    { key: "category", width: 22 },
    { key: "metric", width: 34 },
    { key: "base", width: 18 },
    ...discountScenarios.map((_, index) => ({
      key: `scenario${index}`,
      width: 16,
    })),
  ];

  sheet.mergeCells(`A1:${columnName(scenarioEndCol)}1`);
  sheet.getCell("A1").value = "할인분양 민감도 및 Exit 분양률";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow([
    "구분",
    "지표",
    "기준",
    ...discountScenarios.map((rate) => `${Math.round(rate * 100)}% 할인`),
  ]);
  styleHeader(sheet.getRow(3));

  const labels = [
    [4, "가정", "할인율"],
    [5, "수입", "조정 총분양수입"],
    [6, "수익성", "시행이익"],
    [7, "수익성", "매출이익률"],
    [9, "Exit", "전체 PF Exit 분양률"],
    [10, "Exit", "Tr.A 선순위 Exit 분양률"],
    [11, "Exit", "Tr.B 중순위 누적 Exit 분양률"],
    [12, "Exit", "Tr.C 후순위 누적 Exit 분양률"],
    [14, "판정", "선순위 회수 안정성"],
    [15, "판정", "중순위 회수 안정성"],
    [16, "판정", "후순위 회수 안정성"],
  ] as const;

  labels.forEach(([row, category, metric]) => {
    sheet.getCell(row, 1).value = category;
    sheet.getCell(row, 2).value = metric;
  });

  discountScenarios.forEach((discountRate, index) => {
    const col = scenarioStartCol + index;
    const column = columnName(col);
    const adjustedRevenue = assumptions.totalRevenue * (1 - discountRate);
    const totalCost =
      assumptions.landCost +
      assumptions.constructionCost +
      assumptions.designCost +
      assumptions.salesCost +
      assumptions.chargesCost +
      assumptions.financeCost +
      assumptions.otherCost;
    const seniorClaim = trancheStructure[0]!.principal * (1 + trancheStructure[0]!.interestRate + trancheStructure[0]!.feeRate);
    const mezzClaim = seniorClaim + trancheStructure[1]!.principal * (1 + trancheStructure[1]!.interestRate + trancheStructure[1]!.feeRate);
    const juniorClaim = mezzClaim + trancheStructure[2]!.principal * (1 + trancheStructure[2]!.interestRate + trancheStructure[2]!.feeRate);

    sheet.getCell(3, col).value = `${Math.round(discountRate * 100)}% 할인`;
    sheet.getCell(4, col).value = discountRate;
    setFormula(sheet.getCell(5, col), `${inputCell(6)}*(1-${column}4)`, adjustedRevenue);
    setFormula(sheet.getCell(6, col), `${column}5-'02_사업수지'!$C$13`, adjustedRevenue - totalCost);
    setFormula(sheet.getCell(7, col), `IF(${column}5=0,0,${column}6/${column}5)`, adjustedRevenue === 0 ? 0 : (adjustedRevenue - totalCost) / adjustedRevenue);
    setFormula(sheet.getCell(9, col), `IF(${column}5=0,0,${inputCell(14)}/${column}5)`, adjustedRevenue === 0 ? 0 : assumptions.pfLoan / adjustedRevenue);
    setFormula(sheet.getCell(10, col), `IF(${column}5=0,0,'03C_Tranche상환'!I5/${column}5)`, adjustedRevenue === 0 ? 0 : seniorClaim / adjustedRevenue);
    setFormula(sheet.getCell(11, col), `IF(${column}5=0,0,SUM('03C_Tranche상환'!I5:I6)/${column}5)`, adjustedRevenue === 0 ? 0 : mezzClaim / adjustedRevenue);
    setFormula(sheet.getCell(12, col), `IF(${column}5=0,0,SUM('03C_Tranche상환'!I5:I7)/${column}5)`, adjustedRevenue === 0 ? 0 : juniorClaim / adjustedRevenue);
    setFormula(sheet.getCell(14, col), `IF(${column}10<=0.65,"안정",IF(${column}10<=0.75,"주의","위험"))`, "안정");
    setFormula(sheet.getCell(15, col), `IF(${column}11<=0.7,"안정",IF(${column}11<=0.8,"주의","위험"))`, "주의");
    setFormula(sheet.getCell(16, col), `IF(${column}12<=0.8,"안정",IF(${column}12<=0.9,"주의","위험"))`, "위험");
  });

  sheet.getCell("C4").value = "입력가정";
  sheet.getCell("C5").value = "총분양수입";
  sheet.getCell("C6").value = "조정수입-총사업비";
  sheet.getCell("C7").value = "시행이익/조정수입";
  sheet.getCell("C9").value = "PF대출금/조정수입";
  sheet.getCell("C10").value = "선순위 Claim/조정수입";
  sheet.getCell("C11").value = "선+중순위 Claim/조정수입";
  sheet.getCell("C12").value = "전체 Claim/조정수입";

  for (let row = 4; row <= 16; row += 1) {
    for (let col = scenarioStartCol; col <= scenarioEndCol; col += 1) {
      if ([4, 7, 9, 10, 11, 12].includes(row)) {
        setPercentFormat(sheet.getCell(row, col));
      } else if ([5, 6].includes(row)) {
        setMoneyFormat(sheet.getCell(row, col));
      }
    }
  }

  [9, 10, 11, 12, 14, 15, 16].forEach((row) => {
    sheet.getRow(row).font = { bold: true };
  });
  styleSheet(sheet);
}

function addDiscountTriggerSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("04B_할인Trigger");
  const monthStartRow = 13;
  const monthColumns = Array.from({ length: MONTH_COUNT }, (_, index) => ({
    key: `m${index + 1}`,
    width: 12,
  }));

  sheet.columns = [
    { key: "section", width: 20 },
    { key: "label", width: 28 },
    { key: "value", width: 16 },
    { key: "note", width: 46 },
    ...monthColumns,
  ];

  sheet.mergeCells(`A1:${columnName(MONTH_COUNT + 4)}1`);
  sheet.getCell("A1").value = "할인분양 Trigger 및 조정 분양수입";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["구분", "시점", "목표분양률", "할인율", "판정"]);
  styleHeader(sheet.getRow(3));

  discountTriggers.forEach((trigger, index) => {
    const row = index + 4;
    sheet.getCell(row, 1).value = "Trigger";
    sheet.getCell(row, 2).value = trigger.label;
    sheet.getCell(row, 3).value = trigger.targetSalesRate;
    sheet.getCell(row, 4).value = trigger.discountRate;
    setFormula(
      sheet.getCell(row, 5),
      `IF(INDEX('03A_공정률'!$D$9:$AV$9,1,${trigger.month})<$C${row},"발동","미발동")`,
      "미발동",
    );
    setPercentFormat(sheet.getCell(row, 3));
    setPercentFormat(sheet.getCell(row, 4));
  });

  sheet.getCell(10, 1).value = "월별";
  sheet.getCell(10, 2).value = "항목";
  sheet.getCell(10, 3).value = "합계";
  for (let month = 1; month <= MONTH_COUNT; month += 1) {
    sheet.getCell(10, month + 4).value = `M${month}`;
  }
  styleHeader(sheet.getRow(10));

  const rowLabels = [
    [monthStartRow, "원 분양수입"],
    [monthStartRow + 1, "누적분양률"],
    [monthStartRow + 2, "발동 할인율"],
    [monthStartRow + 3, "조정 분양수입"],
    [monthStartRow + 4, "할인 감소액"],
  ] as const;

  rowLabels.forEach(([row, label]) => {
    sheet.getCell(row, 1).value = "월별";
    sheet.getCell(row, 2).value = label;
  });

  setFormula(sheet.getCell(monthStartRow, 3), `SUM(E${monthStartRow}:${columnName(MONTH_COUNT + 4)}${monthStartRow})`, assumptions.totalRevenue);
  setFormula(sheet.getCell(monthStartRow + 3, 3), `SUM(E${monthStartRow + 3}:${columnName(MONTH_COUNT + 4)}${monthStartRow + 3})`, assumptions.totalRevenue);
  setFormula(sheet.getCell(monthStartRow + 4, 3), `SUM(E${monthStartRow + 4}:${columnName(MONTH_COUNT + 4)}${monthStartRow + 4})`, 0);

  for (let month = 1; month <= MONTH_COUNT; month += 1) {
    const col = month + 4;
    const column = columnName(col);
    const sourceColumn = columnName(month + 3);
    const collectionColumn = columnName(month + COLLECTION_START_COLUMN - 1);
    const activeDiscount = discountTriggers
      .filter((trigger) => month >= trigger.month)
      .reduce((maxRate, trigger) => Math.max(maxRate, trigger.discountRate), 0);
    const originalRevenue = salesCollectionForMonth(month);

    setFormula(
      sheet.getCell(monthStartRow, col),
      `'02B_수납스케줄'!${collectionColumn}${collectionTotalRow}`,
      originalRevenue,
    );
    setFormula(sheet.getCell(monthStartRow + 1, col), `'03A_공정률'!${sourceColumn}9`, 0);
    setFormula(
      sheet.getCell(monthStartRow + 2, col),
      `MAX(IF(AND(COLUMN()-4>=${discountTriggers[0]!.month},$E$4=\"발동\"),$D$4,0),IF(AND(COLUMN()-4>=${discountTriggers[1]!.month},$E$5=\"발동\"),$D$5,0),IF(AND(COLUMN()-4>=${discountTriggers[2]!.month},$E$6=\"발동\"),$D$6,0),IF(AND(COLUMN()-4>=${discountTriggers[3]!.month},$E$7=\"발동\"),$D$7,0))`,
      activeDiscount,
    );
    setFormula(
      sheet.getCell(monthStartRow + 3, col),
      `${column}${monthStartRow}*(1-${column}${monthStartRow + 2})`,
      originalRevenue * (1 - activeDiscount),
    );
    setFormula(
      sheet.getCell(monthStartRow + 4, col),
      `${column}${monthStartRow}-${column}${monthStartRow + 3}`,
      originalRevenue * activeDiscount,
    );
    [monthStartRow, monthStartRow + 3, monthStartRow + 4].forEach((row) =>
      setMoneyFormat(sheet.getCell(row, col)),
    );
    [monthStartRow + 1, monthStartRow + 2].forEach((row) =>
      setPercentFormat(sheet.getCell(row, col)),
    );
  }

  [monthStartRow, monthStartRow + 3, monthStartRow + 4].forEach((row) =>
    setMoneyFormat(sheet.getCell(row, 3)),
  );
  sheet.getRow(monthStartRow + 3).font = { bold: true };
  sheet.views = [{ state: "frozen", xSplit: 4, ySplit: 10 }];
  styleSheet(sheet);
}

function addChecksSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("04_검증");
  sheet.columns = [
    { key: "check", width: 42 },
    { key: "calculated", width: 20 },
    { key: "source", width: 20 },
    { key: "diff", width: 16 },
    { key: "status", width: 18 },
    { key: "note", width: 54 },
  ];

  sheet.mergeCells("A1:F1");
  sheet.getCell("A1").value = "검증 체크";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["검증 항목", "계산값", "기준값", "차이", "상태", "설명"]);
  styleHeader(sheet.getRow(3));

  const checkRows = [
    ["분양수입 월별 합계 일치", "='03_월별현금흐름'!C7", `=${inputCell(6)}`, "=B4-C4", '=IF(ABS(D4)<0.01,"OK","확인필요")', "월별 분양수입 합계와 입력가정 총분양수입 비교"],
    ["상품별 분양수입 합계 일치", "='02A_분양수입'!C9", `=${inputCell(6)}`, "=B5-C5", '=IF(ABS(D5)<0.05,"OK","확인필요")', "아파트/오피스텔/근린생활시설 분양수입 합계와 총분양수입 비교"],
    ["총사업비 산식 일치", "='02_사업수지'!C13", `=SUM(${inputCell(7)},${inputCell(8)},${inputCell(9)},${inputCell(10)},${inputCell(11)},${inputCell(12)},${inputCell(13)})`, "=B6-C6", '=IF(ABS(D6)<0.01,"OK","확인필요")', "대지비, 공사비, 용역비, 분양제경비, 각종부담금, 금융비용, 기타사업비 합계 비교"],
    ["월별 비용 합계 일치", "='03_월별현금흐름'!C18", "='02_사업수지'!C13", "=B7-C7", '=IF(ABS(D7)<0.01,"OK","확인필요")', "월별 Cash Out 합계와 사업수지 총사업비 비교"],
    ["시행이익 산식 일치", "='02_사업수지'!C15", "='02_사업수지'!C4-'02_사업수지'!C13", "=B8-C8", '=IF(ABS(D8)<0.01,"OK","확인필요")', "총분양수입에서 총사업비를 차감"],
    ["Max Minus 산식 일치", "='03_월별현금흐름'!C22", "=MIN('03_월별현금흐름'!D21:AV21)", "=B9-C9", '=IF(ABS(D9)<0.01,"OK","확인필요")', "누적 현금흐름 최저점 확인"],
    ["PF 인출한도 초과 여부", "='03B_PF인출상환'!C7", `=${inputCell(14)}`, "=B10-C10", '=IF(B10<=C10+0.01,"OK","확인필요")', "누적 PF 인출액이 승인 PF 한도를 초과하는지 확인"],
    ["PF 기말잔액 확인", "='03B_PF인출상환'!C11", "0", "=B11-C11", '=IF(ABS(D11)<0.01,"OK","상환잔액")', "분양수입 상환적립 후 기말 PF 잔액 확인"],
    ["Tranche 원금 합계", "='03C_Tranche상환'!D9", `=${inputCell(14)}`, "=B12-C12", '=IF(ABS(D12)<0.01,"OK","확인필요")', "Tr.A/B/C 원금 합계와 PF 대출금 비교"],
    ["Waterfall 부족액", "='03C_Tranche상환'!L9", "0", "=B13-C13", '=IF(ABS(D13)<0.01,"OK","부족액")', "상환가능재원으로 Tranche별 Claim 전액 회수 가능한지 확인"],
    ["월별 Tranche 잔액 대사", "='03D_Tranche월별상환'!C18", "='03B_PF인출상환'!C11", "=B14-C14", '=IF(ABS(D14)<0.01,"OK","확인필요")', "월별 Tranche 기말잔액 합계와 단일 PF 기말잔액 비교"],
    ["할인 후 수입 감소 확인", "='04B_할인Trigger'!C16", "='04B_할인Trigger'!C13", "=B15-C15", '=IF(B15<=C15+0.01,"OK","확인필요")', "할인 Trigger 반영 후 조정 분양수입이 원 분양수입 이하인지 확인"],
    ["수입 시나리오 선택값", `=${inputCell(22)}`, '="Base 또는 Trigger"', "=0", '=IF(OR(B16="Base",B16="Trigger"),"OK","확인필요")', "메인 Cash Flow의 분양수입 선택값이 허용 목록인지 확인"],
  ];

  checkRows.forEach((row, index) => {
    const excelRow = index + 4;
    sheet.getCell(excelRow, 1).value = row[0];
    setFormula(sheet.getCell(excelRow, 2), row[1]!.slice(1), 0);
    setFormula(sheet.getCell(excelRow, 3), row[2]!.slice(1), 0);
    setFormula(sheet.getCell(excelRow, 4), row[3]!.slice(1), 0);
    setFormula(sheet.getCell(excelRow, 5), row[4]!.slice(1), "OK");
    sheet.getCell(excelRow, 6).value = row[5];
    [2, 3, 4].forEach((col) => setMoneyFormat(sheet.getCell(excelRow, col)));
  });

  for (let rowIndex = 4; rowIndex <= 16; rowIndex += 1) {
    sheet.getCell(rowIndex, 5).fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: "FFDCFCE7" },
    };
  }

  styleSheet(sheet);
}

function addFormulaMapSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("05_수식지도");
  sheet.columns = [
    { key: "sheetCell", width: 28 },
    { key: "output", width: 28 },
    { key: "formula", width: 54 },
    { key: "meaning", width: 58 },
    { key: "source", width: 34 },
  ];

  sheet.mergeCells("A1:E1");
  sheet.getCell("A1").value = "수식지도";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["시트/셀", "산출물", "수식", "의미", "원천 입력"]);
  styleHeader(sheet.getRow(3));

  const formulaRows = [
    ["02A_분양수입!C9", "상품별 분양수입 합계", "=SUM(C4:C6)", "아파트, 오피스텔, 근린생활시설 분양수입 합계", "샘플 분양/사업수지 시트"],
    ["02B_수납스케줄!G:AY", "월별 분양대금 수납", "=IF(월=수납월,단계별 수납액,0)", "상품별 계약금, 중도금, 잔금 납입월에 분양대금 배분", "샘플 2.현금흐름 분양수입 구조"],
    ["02_사업수지!C13", "총사업비", "=SUM(C6:C12)", "대지비, 공사비, 용역비, 분양제경비, 각종부담금, 금융비용, 기타사업비 합계", "01_입력가정 C7:C13"],
    ["02_사업수지!C15", "시행이익", "=C4-C13", "총분양수입에서 총사업비를 차감한 개발이익", "02_사업수지 C4, C13"],
    ["02_사업수지!C19", "LTC", `=${inputCell(14)}/C13`, "PF 대출금이 총사업비에서 차지하는 비율", "01_입력가정 C14, 02_사업수지 C13"],
    ["02_사업수지!C21", "Base Exit 분양률", `=${inputCell(14)}/C4`, "PF 대출금을 회수하기 위해 필요한 최소 분양률", "01_입력가정 C14, 02_사업수지 C4"],
    ["03A_공정률!D5:AV6", "월공정/누적공정", "=S-curve 월공정률", "샘플 공정율 시트를 단순화한 S-curve 공정률 가정", "01_입력가정 C17:C18"],
    ["03_월별현금흐름!D7:AV7", "월별 분양수입", "=IF(시나리오=\"Trigger\",Trigger 조정수입,Base 수납스케줄)", "입력가정의 수입 시나리오 선택값에 따라 Base/Trigger 분양수입을 Cash In으로 연결", "01_입력가정 C22, 02B_수납스케줄, 04B_할인Trigger"],
    ["03_월별현금흐름!D11:AV11", "월별 공사비", "=공사비 총액*월공정률", "공정률 기반 공사비 배분", "03A_공정률 5행"],
    ["03_월별현금흐름!D21:AV21", "누적 현금흐름", "=전월 누적현금흐름+월별 순현금흐름", "월별 자금 부족 또는 잉여를 누적", "03_월별현금흐름 20행"],
    ["03_월별현금흐름!C22", "Max Minus", "=MIN(D21:AV21)", "사업기간 중 누적 현금흐름의 최저점", "03_월별현금흐름 21행"],
    ["03B_PF인출상환!D7:AV11", "PF 인출 및 상환", "=인출필요액, 이자율, 상환적립률 기반", "월별 PF 인출, 이자, 상환적립, 원금상환, 잔액 계산", "03_월별현금흐름, 01_입력가정 PF 조건"],
    ["03C_Tranche상환!I5:L9", "Tranche Waterfall", "=선순위부터 총 Claim에 상환가능재원 배분", "Tr.A/B/C별 총 Claim, 상환액, 부족액, 회수율 계산", "03B_PF인출상환 상환적립 재원"],
    ["03D_Tranche월별상환!D10:AV18", "월별 Tranche 원금상환", "=월별 원금상환 가능액을 Tr.A → Tr.B → Tr.C 순서로 배분", "단일 PF 상환액을 월별 선중후순위 잔액으로 분해", "03B_PF인출상환 10행"],
    ["04A_할인민감도!D5:H16", "할인분양 민감도", "=조정 총분양수입 및 Tranche별 Exit 분양률", "0~20% 할인 시 분양수입, 시행이익, Exit 분양률, 회수 안정성 판정", "02_사업수지, 03C_Tranche상환"],
    ["04B_할인Trigger!E4:E7", "할인 Trigger 발동", "=누적분양률<목표분양률이면 발동", "6/12/24개월 및 준공월 목표분양률 미달 시 할인율 적용", "03A_공정률 누적분양률"],
    ["04B_할인Trigger!E16:AY17", "Trigger 반영 수입", "=원 분양수입*(1-발동 할인율)", "발동된 할인율이 이후 월별 분양수입을 낮추는 민감도 표", "02B_수납스케줄, 할인 Trigger"],
    ["04_검증!E4:E16", "검증 상태", '=IF(ABS(차이)<0.01,"OK","확인필요")', "입력값과 계산값이 일치하는지 자동 확인", "04_검증 B:D열"],
  ];

  formulaRows.forEach((row) => sheet.addRow(row));

  for (let rowIndex = 4; rowIndex <= sheet.rowCount; rowIndex += 1) {
    sheet.getCell(rowIndex, 3).font = { name: "Consolas", size: 10 };
  }

  styleSheet(sheet);
}

function addSampleTraceSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("06_샘플대사");
  sheet.columns = [
    { key: "sampleSheet", width: 26 },
    { key: "sampleCell", width: 20 },
    { key: "sampleMeaning", width: 38 },
    { key: "modelCell", width: 28 },
    { key: "modelMeaning", width: 42 },
    { key: "note", width: 58 },
  ];

  sheet.mergeCells("A1:F1");
  sheet.getCell("A1").value = "샘플 Workbook 반영 대사표";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["샘플 시트", "샘플 셀/범위", "샘플 의미", "Export 대응 위치", "Export 의미", "비고"]);
  styleHeader(sheet.getRow(3));

  const traceRows = [
    ["입력", "E8:F13", "사업면적, 실대지면적, 아파트/오피스텔/상가 대지 배분", "01_입력가정, 02A_분양수입", "기본 사업개요와 상품별 구조", "현재 export는 면적 세부 배분은 요약만 반영"],
    ["입력", "E24", "사업기간 42개월", "01_입력가정!C18", "준공월", "월별 CF 분석기간은 준공 후 3개월을 포함해 45개월"],
    ["입력", "32:59", "아파트/오피스텔 세대수와 면적", "02A_분양수입!A4:B5", "상품별 세대/호실수", "아파트 230세대, 오피스텔 76실"],
    ["분양", "J21,J30,J40,J41", "상품별 및 총 분양수입", "02A_분양수입!C4:C9, 01_입력가정!C6", "상품별 분양수입 및 총분양수입", "단위는 샘플 천원에서 억원으로 변환"],
    ["2.현금흐름", "8:43행", "상품별 분양률 및 분양수입", "02B_수납스케줄, 03_월별현금흐름!7행", "계약금/중도금/잔금 기반 월별 Cash In", "샘플 원본은 상품별 분양률과 납입회차를 더 세밀하게 계산"],
    ["사업수지", "D18", "공사비 소계", "01_입력가정!C8, 02_사업수지!C7", "공사비", "720.36억원"],
    ["사업수지", "D26", "대지비 소계", "01_입력가정!C7, 02_사업수지!C6", "대지비", "460.32억원"],
    ["사업수지", "D33", "용역비 소계", "01_입력가정!C9, 02_사업수지!C8", "용역비", "35.61억원"],
    ["사업수지", "D41", "분양제경비 소계", "01_입력가정!C10, 02_사업수지!C9", "분양제경비", "63.18억원"],
    ["사업수지", "D52", "각종부담금 소계", "01_입력가정!C11, 02_사업수지!C10", "각종부담금", "26.54억원"],
    ["사업수지", "D62", "금융비용 소계", "01_입력가정!C12, 02_사업수지!C11", "금융비용", "162.98억원"],
    ["사업수지", "D71", "기타사업비 소계", "01_입력가정!C13, 02_사업수지!C12", "기타사업비", "49.42억원"],
    ["사업수지", "D72", "총사업비", "02_사업수지!C13, 04_검증!B6", "총사업비 검증", "1518.42억원"],
    ["2.현금흐름", "J:BR", "월차, 공정률, 분양률, 투입금액, 금융비용", "03_월별현금흐름!D:AV", "월별 현금흐름", "샘플의 상세 월별 로직을 MVP용 균등/단순 배분으로 축약"],
    ["2.현금흐름", "93:109행", "대출상환, 대출이자, 수수료, 신탁차입금", "03B_PF인출상환!D:AV", "PF 인출, 이자, 상환적립, 원금상환", "03C/03D에서 Tr.A/B/C 기준으로 회수 구조를 추가 분해"],
    ["PF 심사보고서", "주요 금융조건", "Tr.A/B/C 선순위·중순위·후순위 대출금, 금리, 수수료", "03C_Tranche상환", "Tranche별 Claim, 상환액, 부족액, 회수율", "선순위 630억원, 중순위 120억원, 후순위 150억원 구조 반영"],
    ["PF 심사보고서", "월별 Tranche 상환", "상환순위에 따른 월별 선중후순위 잔액 관리", "03D_Tranche월별상환", "월별 Tr.A/B/C 원금상환 및 기말잔액", "03B의 원금상환 가능액을 선순위부터 배분"],
    ["PF 심사보고서", "할인분양 Trigger", "목표분양률 미달 시 5~20% 할인분양 시나리오", "04A_할인민감도, 04B_할인Trigger", "할인율별 조정수입, Exit 분양률, 회수 안정성, 월별 조정수입", "Trigger 발동 여부와 월별 조정 분양수입 표까지 연결"],
    ["Export 모델", "01_입력가정!C22", "Base/Trigger 수입 시나리오 선택", "03_월별현금흐름!7행", "선택 시나리오에 따른 메인 Cash In 전환", "Base는 원 수납스케줄, Trigger는 할인 Trigger 조정수입 적용"],
    ["공정율", "A:BO", "공사기간별 월공정률 lookup", "03A_공정률!D5:AV6", "월공정률/누적공정률", "현재는 42개월 S-curve 공정률, 향후 샘플 lookup table 직접 반영 예정"],
  ];

  traceRows.forEach((row) => sheet.addRow(row));
  styleSheet(sheet);
}

function addImportContractSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("07_Import계약");
  sheet.columns = [
    { key: "name", width: 34 },
    { key: "location", width: 32 },
    { key: "access", width: 16 },
    { key: "purpose", width: 58 },
    { key: "validation", width: 44 },
  ];

  sheet.mergeCells("A1:E1");
  sheet.getCell("A1").value = "REALYTICS Feasibility Workbook Import Contract";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRow(["계약 항목", "Named Range / 위치", "읽기/쓰기", "용도", "검증 기준"]);
  styleHeader(sheet.getRow(3));

  const rows = [
    ["schema_version", "realytics_fs_schema_version", "read", "워크북 스키마 버전", "realytics-fs-xlsx-v1-draft"],
    ["source_workbook", "realytics_source_workbook", "read", "샘플 또는 원본 workbook 출처", "파일명 또는 evidence ID 필요"],
    ["total_revenue", "fs_total_revenue", "read/write", "총분양수입", "0보다 커야 함"],
    ["total_project_cost", "fs_total_project_cost", "read", "총사업비", "비용 항목 합계와 일치"],
    ["pf_loan", "fs_pf_loan", "read/write", "PF 대출금", "Tranche 원금 합계와 일치"],
    ["revenue_scenario", "fs_revenue_scenario", "read/write", "Cash Flow 수입 시나리오", "Base 또는 Trigger"],
    ["monthly_revenue", "cf_monthly_revenue", "read", "월별 분양수입", "선택 시나리오와 일치"],
    ["cumulative_cash_flow", "cf_cumulative_cash_flow", "read", "누적 현금흐름", "Max Minus 계산 근거"],
    ["max_minus", "cf_max_minus", "read", "Max Minus", "누적 현금흐름 최저점과 일치"],
    ["pf_ending_balance", "pf_ending_balance", "read", "기말 PF 잔액", "월별 Tranche 잔액 합계와 일치"],
    ["tranche_shortfall_total", "pf_tranche_shortfall_total", "read", "Waterfall 부족액", "0이면 전액 회수"],
    ["trigger_adjusted_revenue", "trigger_adjusted_revenue_total", "read", "Trigger 조정 분양수입", "원 수입 이하"],
  ];

  rows.forEach((row) => sheet.addRow(row));

  sheet.getCell("A18").value = "Round-trip 검증";
  sheet.getCell("A18").font = { bold: true };
  sheet.getCell("A19").value = "스키마 버전";
  setFormula(sheet.getCell("B19"), "realytics_fs_schema_version", "realytics-fs-xlsx-v1-draft");
  sheet.getCell("C19").value = "스키마 버전 named range 확인";
  sheet.getCell("A20").value = "총사업비";
  setFormula(sheet.getCell("B20"), "fs_total_project_cost", 0);
  sheet.getCell("C20").value = "사업수지 총사업비 named range 확인";
  sheet.getCell("A21").value = "수입 시나리오";
  setFormula(sheet.getCell("B21"), "fs_revenue_scenario", assumptions.revenueScenario);
  sheet.getCell("C21").value = "Base/Trigger 선택값 확인";
  sheet.getCell("A23").value = "Canonical schema";
  sheet.getCell("B23").value = "realytics-fs-xlsx-v1-draft";
  sheet.getCell("C23").value = "Import adapter는 이 값을 schemaVersion으로 읽음";

  styleSheet(sheet);
}

function addTemplateMetadataSheet(
  workbook: ExcelJS.Workbook,
  templateMetadata: WorkbookTemplateMetadata,
) {
  const sheet = workbook.addWorksheet("08_Template계약");
  sheet.columns = [
    { key: "field", width: 34 },
    { key: "value", width: 90 },
    { key: "note", width: 60 },
  ];

  sheet.getCell("A1").value = "Template Source Contract";
  sheet.getCell("A1").font = { bold: true, size: 14 };
  sheet.mergeCells("A1:C1");
  sheet.addRow(["Field", "Value", "Note"]);
  styleHeader(sheet.getRow(2));

  const rows = [
    ["template_mode", templateMetadata.templateMode, "sample_package이면 원본 샘플 시트 패키지를 먼저 복사"],
    ["template_id", templateMetadata.templateId, "템플릿 레지스트리 식별자"],
    ["template_version", templateMetadata.templateVersion, "템플릿 버전"],
    ["source_type", templateMetadata.sourceType, "controlled_template_path / controlled_template_dir / local override"],
    ["source_file_name", templateMetadata.sourceFileName || "", "원본 템플릿 파일명"],
    ["source_path", templateMetadata.sourcePath || "", "서버/로컬 템플릿 경로"],
    ["source_sha256", templateMetadata.sourceSha256 || "", "원본 템플릿 SHA-256"],
    ["copied_sheet_count", templateMetadata.copiedSheetCount, "복사된 원본 샘플 시트 수"],
    ["copied_sheet_names", templateMetadata.copiedSheetNames.join(", "), "복사된 시트명"],
    ["resolved_at", templateMetadata.resolvedAt, "템플릿 해석 시각"],
    [
      "production_note",
      "Template path should move to controlled server/object storage for production.",
      "REALYTICS_SAMPLE_WORKBOOK_PATH is only a pilot override.",
    ],
  ];

  rows.forEach((row) => sheet.addRow(row));
  styleSheet(sheet);
}

function defineWorkbookNames(workbook: ExcelJS.Workbook) {
  defineWorkbookName(workbook, "realytics_fs_schema_version", "07_Import계약", "$B$23");
  defineWorkbookName(workbook, "realytics_source_workbook", "01_입력가정", "$C$23");
  defineWorkbookName(workbook, "fs_total_revenue", "01_입력가정", "$C$6");
  defineWorkbookName(workbook, "fs_total_project_cost", "02_사업수지", "$C$13");
  defineWorkbookName(workbook, "fs_pf_loan", "01_입력가정", "$C$14");
  defineWorkbookName(workbook, "fs_revenue_scenario", "01_입력가정", "$C$22");
  defineWorkbookName(workbook, "cf_monthly_revenue", "03_월별현금흐름", "$D$7:$AV$7");
  defineWorkbookName(workbook, "cf_cumulative_cash_flow", "03_월별현금흐름", "$D$21:$AV$21");
  defineWorkbookName(workbook, "cf_max_minus", "03_월별현금흐름", "$C$22");
  defineWorkbookName(workbook, "pf_ending_balance", "03B_PF인출상환", "$C$11");
  defineWorkbookName(workbook, "pf_tranche_shortfall_total", "03C_Tranche상환", "$L$9");
  defineWorkbookName(workbook, "trigger_adjusted_revenue_total", "04B_할인Trigger", "$C$16");
}

function defineTemplateMetadataWorkbookNames(workbook: ExcelJS.Workbook) {
  if (!workbook.getWorksheet("08_Template계약")) {
    return;
  }
  defineWorkbookName(workbook, "realytics_template_mode", "08_Template계약", "$B$3");
  defineWorkbookName(workbook, "realytics_template_id", "08_Template계약", "$B$4");
  defineWorkbookName(workbook, "realytics_template_version", "08_Template계약", "$B$5");
  defineWorkbookName(workbook, "realytics_template_sha256", "08_Template계약", "$B$9");
}

async function protectFormulaWorkbook(workbook: ExcelJS.Workbook) {
  await Promise.all(
    workbook.worksheets.map((sheet) =>
      sheet.protect("realytics", {
        selectLockedCells: true,
        selectUnlockedCells: true,
        formatCells: false,
        formatColumns: false,
        formatRows: false,
        insertColumns: false,
        insertRows: false,
        deleteColumns: false,
        deleteRows: false,
        sort: false,
        autoFilter: true,
      }),
    ),
  );
}

export async function GET() {
  const workbook = new ExcelJS.Workbook();
  workbook.creator = "REALYTICS Platform9";
  workbook.created = new Date();
  workbook.modified = new Date();
  workbook.calcProperties.fullCalcOnLoad = true;

  addReadMeSheet(workbook);
  const templateMetadata = await addSampleWorkbookParitySheets(workbook);
  if (!templateMetadata) {
    addLegacyInputMirrorSheet(workbook);
    addLegacySalesMirrorSheet(workbook);
    addLegacyBusinessPlanMirrorSheet(workbook);
    addLegacyCashFlowMirrorSheet(workbook);
  }
  addInputSheet(workbook);
  addProductMixSheet(workbook);
  addCollectionScheduleSheet(workbook);
  addFeasibilitySheet(workbook);
  addProgressSheet(workbook);
  addCashFlowSheet(workbook);
  addPfScheduleSheet(workbook);
  addTrancheWaterfallSheet(workbook);
  addMonthlyTrancheRepaymentSheet(workbook);
  addDiscountSensitivitySheet(workbook);
  addDiscountTriggerSheet(workbook);
  addChecksSheet(workbook);
  addFormulaMapSheet(workbook);
  addSampleTraceSheet(workbook);
  addImportContractSheet(workbook);
  addTemplateMetadataSheet(
    workbook,
    templateMetadata || {
      templateMode: "fallback_mirror",
      templateId: "realytics-fallback-mirror",
      templateVersion: "v1",
      sourceType: "generated_fallback",
      sourcePath: null,
      sourceFileName: null,
      sourceSha256: null,
      copiedSheetCount: 0,
      copiedSheetNames: [],
      resolvedAt: new Date().toISOString(),
    },
  );
  defineWorkbookNames(workbook);
  defineTemplateMetadataWorkbookNames(workbook);
  await protectFormulaWorkbook(workbook);

  const buffer = await workbook.xlsx.writeBuffer();

  return new NextResponse(buffer, {
    status: 200,
    headers: {
      "Content-Disposition":
        'attachment; filename="REALYTICS_Formula_CashFlow_Model.xlsx"',
      "Content-Type":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
  });
}
