import { NextResponse } from "next/server";
import ExcelJS from "exceljs";
import {
  resolveExcelWorkbookTemplate,
  type WorkbookTemplateMetadata,
} from "../../../lib/excel-template-repository";

export const runtime = "nodejs";

type RiskControl = {
  no: number;
  category: string;
  item: string;
  owner: string;
  requiredEvidence: string;
  sourceSheet: string;
  sourceLocator: string;
  riskLevel: "낮음" | "보통" | "높음" | "중대";
  reportSection: string;
  reportUse: string;
};

const project = {
  id: "P9-2026-05A",
  name: "대구 본동 주상복합 PF",
  location: "대구광역시 달서구 본동",
  sourceWorkbook: "19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx",
  totalRevenue: 1655.97,
  totalProjectCost: 1518.42,
  pfLoan: 900,
  seniorLoan: 630,
  mezzanineLoan: 120,
  juniorLoan: 150,
  baseExitSaleRate: 0.5435,
  seniorExitSaleRate: 0.5917,
  mezzanineExitSaleRate: 0.6696,
  juniorExitSaleRate: 0.768,
  maxMinus: -267.49,
  firstMonthCashIn: 50,
  firstMonthCashOut: 52.6749,
  firstMonthNetCf: -2.6749,
  dataDate: "2026-06-05",
};

const metrics = [
  ["총분양수입", project.totalRevenue, "억원", "사업수지 및 분양 시트 집계", "사업수지!D8"],
  ["총사업비", project.totalProjectCost, "억원", "사업수지 비용 항목 집계", "사업수지!D18:D71"],
  ["시행이익", project.totalRevenue - project.totalProjectCost, "억원", "총분양수입 - 총사업비", "사업수지!D74"],
  ["시행이익률", (project.totalRevenue - project.totalProjectCost) / project.totalRevenue, "%", "시행이익 / 총분양수입", "계산"],
  ["PF 대출금", project.pfLoan, "억원", "Tr.A/B/C 합계", "2.현금흐름!48:55"],
  ["Base Exit 분양률", project.baseExitSaleRate, "%", "PF 원금 회수 필요 분양률", "금융조건표/계산"],
  ["Tr.A Exit 분양률", project.seniorExitSaleRate, "%", "선순위 원리금 회수 기준", "금융조건표/계산"],
  ["Tr.B Exit 분양률", project.mezzanineExitSaleRate, "%", "중순위 원리금 회수 기준", "금융조건표/계산"],
  ["Tr.C Exit 분양률", project.juniorExitSaleRate, "%", "후순위 원리금 회수 기준", "금융조건표/계산"],
  ["1개월차 누적 CF", project.maxMinus, "백만원", "샘플 현금흐름 key cell", "2.현금흐름!F124"],
] as const;

const riskControls: RiskControl[] = [
  {
    no: 1,
    category: "토지",
    item: "토지소유권 확보",
    owner: "토지/법무",
    requiredEvidence: "매매계약서, 등기부, 신탁계약",
    sourceSheet: "입력",
    sourceLocator: "입력!D5:E13",
    riskLevel: "보통",
    reportSection: "사업부지 확보",
    reportUse: "토지 확보 현황 및 미확인 증빙 보완사항",
  },
  {
    no: 2,
    category: "토지",
    item: "지분합계 및 공유자 동의",
    owner: "토지/법무",
    requiredEvidence: "토지조서, 공유자 동의서",
    sourceSheet: "입력",
    sourceLocator: "입력!D8:I13",
    riskLevel: "보통",
    reportSection: "사업부지 확보",
    reportUse: "공유지분 및 권리관계 확인 필요사항",
  },
  {
    no: 3,
    category: "토지",
    item: "제한물권·압류·가처분 해소",
    owner: "법무",
    requiredEvidence: "등기부등본, 말소확약",
    sourceSheet: "근거셀매핑",
    sourceLocator: "registry evidence required",
    riskLevel: "높음",
    reportSection: "주요 채권보전방안",
    reportUse: "최초 인출 전 제한물권 말소 조건",
  },
  {
    no: 4,
    category: "토지",
    item: "국공유지·특수필지 처리",
    owner: "인허가/토지",
    requiredEvidence: "국공유지 매입 협의자료",
    sourceSheet: "입력",
    sourceLocator: "입력!D8:I13",
    riskLevel: "보통",
    reportSection: "사업부지 확보",
    reportUse: "국공유지 또는 특수필지 존재 시 별도 조건",
  },
  {
    no: 5,
    category: "인허가",
    item: "인허가 일정 및 승인 가능성",
    owner: "인허가",
    requiredEvidence: "인허가 로드맵, 사전협의 기록",
    sourceSheet: "2개요",
    sourceLocator: "2개요!A1:T55",
    riskLevel: "보통",
    reportSection: "사업추진 일정",
    reportUse: "착공/준공/분양 일정 전제",
  },
  {
    no: 6,
    category: "인허가",
    item: "용도지역·건폐율·용적률 적합성",
    owner: "설계/인허가",
    requiredEvidence: "토지이용계획, 건축개요",
    sourceSheet: "입력",
    sourceLocator: "입력!D19:E23",
    riskLevel: "보통",
    reportSection: "사업개요",
    reportUse: "건축 가능 규모 및 법규 적합성",
  },
  {
    no: 7,
    category: "시행사",
    item: "시행사 재무 및 신용도",
    owner: "심사",
    requiredEvidence: "재무제표, 신용평가",
    sourceSheet: "검토서",
    sourceLocator: "검토서!A1:L41",
    riskLevel: "보통",
    reportSection: "차주 분석",
    reportUse: "차주 신용 및 자금보충 능력",
  },
  {
    no: 8,
    category: "시공사",
    item: "시공사 책임준공 및 신용도",
    owner: "심사/기술",
    requiredEvidence: "도급계약, 책임준공 확약",
    sourceSheet: "검토서",
    sourceLocator: "검토서!A1:L41",
    riskLevel: "보통",
    reportSection: "시공사 분석",
    reportUse: "책임준공 및 채무인수 조건",
  },
  {
    no: 9,
    category: "공사비",
    item: "공사비 증액 및 원가 검증",
    owner: "기술/심사",
    requiredEvidence: "공사비 견적, 원가검토서",
    sourceSheet: "사업수지",
    sourceLocator: "사업수지!D26:D33",
    riskLevel: "높음",
    reportSection: "자금 사용 용도",
    reportUse: "공사비 증액 및 유보금 조건",
  },
  {
    no: 10,
    category: "시장",
    item: "입지 및 경쟁단지",
    owner: "분양성",
    requiredEvidence: "시장조사, 경쟁단지표",
    sourceSheet: "분양",
    sourceLocator: "분양!A1:AA635",
    riskLevel: "보통",
    reportSection: "분양성 분석",
    reportUse: "경쟁 공급 및 입지 수요 판단",
  },
  {
    no: 11,
    category: "분양",
    item: "분양가 적정성",
    owner: "분양성",
    requiredEvidence: "분양가 비교표, 감정/시세자료",
    sourceSheet: "분양",
    sourceLocator: "분양!J21,J30,J40",
    riskLevel: "높음",
    reportSection: "분양성 분석",
    reportUse: "제안 분양가 적정성 및 조정 필요성",
  },
  {
    no: 12,
    category: "분양",
    item: "분양률 Trigger 및 할인분양",
    owner: "심사/분양",
    requiredEvidence: "할인분양 trigger table",
    sourceSheet: "Import계약",
    sourceLocator: "할인 Trigger 6/12/24개월",
    riskLevel: "높음",
    reportSection: "할인분양 Trigger",
    reportUse: "목표분양률 미달 시 할인분양 조건",
  },
  {
    no: 13,
    category: "금융",
    item: "PF 조달구조 및 Tranche",
    owner: "금융",
    requiredEvidence: "금융조건표, 대주단 term sheet",
    sourceSheet: "2.현금흐름",
    sourceLocator: "2.현금흐름!48:55",
    riskLevel: "보통",
    reportSection: "주요 금융조건 요약표",
    reportUse: "Tr.A/B/C 선중후순위 구조",
  },
  {
    no: 14,
    category: "금융",
    item: "LTV·LTC·DSCR 기준",
    owner: "심사",
    requiredEvidence: "PF metrics 산출표",
    sourceSheet: "핵심지표",
    sourceLocator: "핵심지표!B6:B13",
    riskLevel: "보통",
    reportSection: "사업성 분석",
    reportUse: "LTV/LTC 및 Exit 분양률 판단",
  },
  {
    no: 15,
    category: "금융",
    item: "금리·수수료·All-in Cost",
    owner: "금융",
    requiredEvidence: "대출조건, 수수료 산정표",
    sourceSheet: "핵심지표",
    sourceLocator: "핵심지표!B15:B17",
    riskLevel: "보통",
    reportSection: "주요 금융조건 요약표",
    reportUse: "Tranche별 금리/수수료 조건",
  },
  {
    no: 16,
    category: "Exit",
    item: "현금흐름 및 Max Minus",
    owner: "사업성",
    requiredEvidence: "월별 CF, 자금수지표",
    sourceSheet: "현금흐름요약",
    sourceLocator: "현금흐름요약!A1:H16",
    riskLevel: "높음",
    reportSection: "요약 현금흐름표",
    reportUse: "부족자금 및 PF 인출 필요성",
  },
  {
    no: 17,
    category: "신탁/담보",
    item: "자금집행 순서 및 변제순위",
    owner: "신탁/금융",
    requiredEvidence: "자금관리계좌, waterfall",
    sourceSheet: "리스크보고서첨부",
    sourceLocator: "리스크보고서첨부!A29:C35",
    riskLevel: "높음",
    reportSection: "자금집행 순서 및 변제 충당 순위",
    reportUse: "상환계좌/운영계좌 배분 조건",
  },
  {
    no: 18,
    category: "신탁/담보",
    item: "신탁·담보·Covenant",
    owner: "법무/금융",
    requiredEvidence: "신탁계약, covenant list",
    sourceSheet: "리스크보고서첨부",
    sourceLocator: "리스크보고서첨부!A29:C35",
    riskLevel: "높음",
    reportSection: "주요 채권보전방안",
    reportUse: "담보신탁 및 우선수익권 조건",
  },
  {
    no: 19,
    category: "법률",
    item: "EOD 및 법률문서 완결성",
    owner: "법무",
    requiredEvidence: "대출약정서, EOD 조항",
    sourceSheet: "리스크보고서첨부",
    sourceLocator: "리스크보고서첨부!A29:C35",
    riskLevel: "보통",
    reportSection: "채무불이행 사유(EOD)",
    reportUse: "기한이익상실 및 인출중단 조건",
  },
  {
    no: 20,
    category: "Exit",
    item: "Exit·미분양·상환 시나리오",
    owner: "심사",
    requiredEvidence: "Exit sale rate, 미분양 LTV matrix",
    sourceSheet: "핵심지표",
    sourceLocator: "핵심지표!B10:B13",
    riskLevel: "중대",
    reportSection: "Exit 전략 및 리스크",
    reportUse: "선순위/중순위/후순위 회수 가능성",
  },
];

function quoteSheet(sheetName: string) {
  return `'${sheetName.replaceAll("'", "''")}'`;
}

function defineWorkbookName(workbook: ExcelJS.Workbook, name: string, sheetName: string, address: string) {
  workbook.definedNames.add(`${quoteSheet(sheetName)}!${address}`, name);
}

function styleTitle(cell: ExcelJS.Cell) {
  cell.font = { bold: true, size: 16, color: { argb: "FF0F172A" } };
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

function styleGrid(sheet: ExcelJS.Worksheet) {
  sheet.eachRow((row) => {
    row.eachCell((cell) => {
      cell.border = {
        top: { style: "thin", color: { argb: "FFE2E8F0" } },
        left: { style: "thin", color: { argb: "FFE2E8F0" } },
        bottom: { style: "thin", color: { argb: "FFE2E8F0" } },
        right: { style: "thin", color: { argb: "FFE2E8F0" } },
      };
      cell.alignment = { vertical: "middle", wrapText: true };
    });
  });
}

function moneyFormat(cell: ExcelJS.Cell) {
  cell.numFmt = '#,##0.0;[Red]-#,##0.0;"-"';
}

function percentFormat(cell: ExcelJS.Cell) {
  cell.numFmt = "0.0%;[Red]-0.0%";
}

function riskFill(level: RiskControl["riskLevel"]) {
  if (level === "중대") return "FFFFE4E6";
  if (level === "높음") return "FFFFF1F2";
  if (level === "보통") return "FFFFF7ED";
  return "FFECFDF5";
}

function addReportAddendumSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("리스크보고서첨부");
  sheet.columns = [
    { key: "a", width: 22 },
    { key: "b", width: 18 },
    { key: "c", width: 90 },
  ];
  sheet.mergeCells("A1:C1");
  sheet.getCell("A1").value = "리스크 보고서 첨부용 요약";
  styleTitle(sheet.getCell("A1"));
  sheet.addRow([]);
  sheet.addRows([
    ["사업명", project.name, "샘플 사업수지 및 현금흐름표에서 추출한 리스크 보고서 첨부용 핵심 정보입니다."],
    ["총분양수입", project.totalRevenue, "억원"],
    ["총사업비", project.totalProjectCost, "억원"],
    ["PF 대출금", project.pfLoan, "억원"],
    ["Base Exit 분양률", project.baseExitSaleRate, "PF 원금 회수를 위한 기준 분양률"],
    ["Tr.A / Tr.B / Tr.C Exit", `${(project.seniorExitSaleRate * 100).toFixed(2)}% / ${(project.mezzanineExitSaleRate * 100).toFixed(2)}% / ${(project.juniorExitSaleRate * 100).toFixed(2)}%`, "Tranche별 회수 기준"],
    ["첨부 판단", "조건부 검토", "토지 증빙, 분양가 근거, 현금흐름 및 Exit 리스크 확인 후 승인조건 반영 필요"],
  ]);

  [4, 5, 6].forEach((row) => moneyFormat(sheet.getCell(`B${row}`)));
  percentFormat(sheet.getCell("B7"));

  sheet.addRow([]);
  sheet.addRow(["보고서 삽입 문구", "구분", "문구"]);
  styleHeader(sheet.getRow(11));
  const paragraphs = [
    [
      "Cash Flow 검토",
      "요약",
      `본건은 총분양수입 ${project.totalRevenue.toLocaleString("ko-KR")}억원, 총 PF ${project.pfLoan.toLocaleString("ko-KR")}억원을 전제로 한 개발사업으로, 현금흐름상 토지비 및 초기 사업비 집행구간에서 자금부족이 발생할 수 있어 PF 인출조건과 상환계좌 배분 구조의 확인이 필요합니다.`,
    ],
    [
      "Exit 검토",
      "Tranche",
      `Base 기준 Exit 분양률은 약 ${(project.baseExitSaleRate * 100).toFixed(1)}%이며, Tr.A/Tr.B/Tr.C 회수를 위한 필요 분양률은 각각 ${(project.seniorExitSaleRate * 100).toFixed(1)}%, ${(project.mezzanineExitSaleRate * 100).toFixed(1)}%, ${(project.juniorExitSaleRate * 100).toFixed(1)}% 수준입니다.`,
    ],
    [
      "분양 리스크",
      "Trigger",
      "목표 분양률 미달 시 6개월 40%, 12개월 60%, 24개월 75% 기준의 단계적 할인분양 Trigger와 상환계좌 Cash Sweep 조건을 약정에 반영할 필요가 있습니다.",
    ],
    [
      "토지/법무",
      "증빙",
      "본 첨부 엑셀은 사업수지와 현금흐름 검토용 요약자료이며, PNU, 공시지가, 등기부 및 제한물권 증빙이 별도로 확인되기 전까지 법무검토 완료자료로 간주하지 않습니다.",
    ],
  ];
  paragraphs.forEach((row) => sheet.addRow(row));

  sheet.addRow([]);
  sheet.addRow(["승인조건 후보", "담당", "조건"]);
  styleHeader(sheet.getRow(17));
  [
    ["토지", "법무", "PF 최초 인출 전 소유권 이전, 제한물권 말소 및 담보신탁 등기 접수 확인"],
    ["분양", "심사/분양", "목표분양률 미달 시 할인분양 Trigger 및 분양수입금 상환적립 비율 강화"],
    ["금융", "대주단", "Stress Case에서 선순위 shortfall이 발생하지 않도록 PF 한도, Equity, DSRA 또는 신용보강 재검토"],
    ["시공", "기술/심사", "책임준공 확약, 미이행 시 채무인수, 공사비 유보 조건 반영"],
  ].forEach((row) => sheet.addRow(row));

  styleGrid(sheet);
  sheet.getRow(1).height = 28;
  sheet.views = [{ state: "frozen", ySplit: 1 }];
  return sheet;
}

function addMetricsSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("핵심지표");
  sheet.columns = [
    { key: "metric", width: 26 },
    { key: "value", width: 18 },
    { key: "unit", width: 12 },
    { key: "basis", width: 42 },
    { key: "source", width: 28 },
  ];
  sheet.addRow(["지표", "값", "단위", "산정 기준", "샘플 근거"]);
  styleHeader(sheet.getRow(1));
  metrics.forEach((row) => sheet.addRow([...row]));
  for (let row = 2; row <= metrics.length + 1; row += 1) {
    const unit = sheet.getCell(`C${row}`).value;
    if (unit === "%") {
      percentFormat(sheet.getCell(`B${row}`));
    } else {
      moneyFormat(sheet.getCell(`B${row}`));
    }
  }
  styleGrid(sheet);
  sheet.views = [{ state: "frozen", ySplit: 1 }];
  return sheet;
}

function addRiskControlsSheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("20대리스크");
  sheet.columns = [
    { key: "no", width: 8 },
    { key: "category", width: 14 },
    { key: "item", width: 28 },
    { key: "level", width: 12 },
    { key: "owner", width: 16 },
    { key: "evidence", width: 34 },
    { key: "source", width: 20 },
    { key: "locator", width: 28 },
    { key: "section", width: 28 },
    { key: "use", width: 45 },
  ];
  sheet.addRow([
    "No",
    "리스크 구분",
    "관리 항목",
    "위험도",
    "담당",
    "필요 증빙",
    "근거 시트",
    "근거 위치",
    "보고서 섹션",
    "보고서 반영 방식",
  ]);
  styleHeader(sheet.getRow(1));
  riskControls.forEach((risk) => {
    const row = sheet.addRow([
      risk.no,
      risk.category,
      risk.item,
      risk.riskLevel,
      risk.owner,
      risk.requiredEvidence,
      risk.sourceSheet,
      risk.sourceLocator,
      risk.reportSection,
      risk.reportUse,
    ]);
    row.getCell(4).fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: riskFill(risk.riskLevel) },
    };
  });
  styleGrid(sheet);
  sheet.views = [{ state: "frozen", ySplit: 1 }];
  sheet.autoFilter = "A1:J21";
  return sheet;
}

function addCashFlowSummarySheet(workbook: ExcelJS.Workbook) {
  const sheet = workbook.addWorksheet("현금흐름요약");
  sheet.columns = [
    { key: "period", width: 16 },
    { key: "cashIn", width: 16 },
    { key: "cashOut", width: 16 },
    { key: "net", width: 16 },
    { key: "cumulative", width: 16 },
    { key: "source", width: 28 },
    { key: "risk", width: 34 },
  ];
  sheet.addRow(["구분", "Cash-In", "Cash-Out", "Net CF", "누적 CF", "샘플 근거", "리스크 보고서 반영"]);
  styleHeader(sheet.getRow(1));
  const rows = [
    ["1개월차", project.firstMonthCashIn, project.firstMonthCashOut, project.firstMonthNetCf, project.maxMinus / 100, "2.현금흐름!F56/F120/F122/F124", "초기 토지비 및 사업비 집행구간 자금부족 확인"],
    ["Base Case", project.totalRevenue, project.totalProjectCost, project.totalRevenue - project.totalProjectCost, project.totalRevenue - project.totalProjectCost, "사업수지!D8:D74", "기준 사업수지상 시행이익과 PF 회수 가능성 검토"],
    ["10% 할인", project.totalRevenue * 0.9, project.totalProjectCost, project.totalRevenue * 0.9 - project.totalProjectCost, project.totalRevenue * 0.9 - project.totalProjectCost, "할인 Trigger 산정", "할인분양 시 중순위 이하 회수여력 약화 가능성"],
    ["15% 할인", project.totalRevenue * 0.85, project.totalProjectCost, project.totalRevenue * 0.85 - project.totalProjectCost, project.totalRevenue * 0.85 - project.totalProjectCost, "할인 Trigger 산정", "후순위 회수 및 시행이익 훼손 가능성"],
  ];
  rows.forEach((row) => sheet.addRow(row));
  for (let row = 2; row <= rows.length + 1; row += 1) {
    ["B", "C", "D", "E"].forEach((col) => moneyFormat(sheet.getCell(`${col}${row}`)));
  }
  styleGrid(sheet);
  sheet.views = [{ state: "frozen", ySplit: 1 }];
  return sheet;
}

function addSourceMapSheet(workbook: ExcelJS.Workbook, templateMetadata: WorkbookTemplateMetadata) {
  const sheet = workbook.addWorksheet("근거셀매핑");
  sheet.columns = [
    { key: "field", width: 28 },
    { key: "source", width: 32 },
    { key: "locator", width: 30 },
    { key: "precision", width: 18 },
    { key: "note", width: 58 },
  ];
  sheet.addRow(["필드", "원천", "위치", "정밀도", "비고"]);
  styleHeader(sheet.getRow(1));
  [
    ["총분양수입", "샘플 사업수지", "사업수지!D8", "excel_cell", "리스크 보고서 사업성 요약에 사용"],
    ["아파트 분양수입", "샘플 분양", "분양!J21", "excel_cell", "상품별 수입 검증"],
    ["오피스텔 분양수입", "샘플 분양", "분양!J30", "excel_cell", "상품별 수입 검증"],
    ["근린생활시설 분양수입", "샘플 분양", "분양!J40", "excel_cell", "상품별 수입 검증"],
    ["1개월차 Cash-In", "샘플 현금흐름", "2.현금흐름!F56", "excel_cell", "월별 CF 요약"],
    ["1개월차 Cash-Out", "샘플 현금흐름", "2.현금흐름!F120", "excel_cell", "월별 CF 요약"],
    ["1개월차 Net CF", "샘플 현금흐름", "2.현금흐름!F122", "excel_cell", "Max Minus 검토"],
    ["1개월차 누적 CF", "샘플 현금흐름", "2.현금흐름!F124", "excel_cell", "Max Minus 검토"],
    ["템플릿 SHA-256", templateMetadata.sourceFileName || "fallback", templateMetadata.sourceSha256 || "", "file_hash", "원본 템플릿 감사 추적"],
  ].forEach((row) => sheet.addRow(row));
  styleGrid(sheet);
  sheet.views = [{ state: "frozen", ySplit: 1 }];
  return sheet;
}

function addImportContractSheet(workbook: ExcelJS.Workbook, templateMetadata: WorkbookTemplateMetadata) {
  const sheet = workbook.addWorksheet("Import계약");
  sheet.columns = [
    { key: "key", width: 34 },
    { key: "value", width: 52 },
    { key: "note", width: 58 },
  ];
  sheet.addRow(["Key", "Value", "Note"]);
  styleHeader(sheet.getRow(1));
  [
    ["schema_version", "realytics-risk-report-xlsx-v1", "경량 리스크 보고서 첨부/서버 import 스키마"],
    ["project_id", project.id, "프로젝트 ID"],
    ["project_name", project.name, "사업명"],
    ["source_workbook", project.sourceWorkbook, "원본 샘플 workbook"],
    ["template_mode", templateMetadata.templateMode, "sample_package or fallback_mirror"],
    ["template_id", templateMetadata.templateId, "템플릿 ID"],
    ["template_version", templateMetadata.templateVersion, "템플릿 버전"],
    ["template_sha256", templateMetadata.sourceSha256 || "", "원본 템플릿 SHA-256"],
    ["risk_row_count", riskControls.length, "20대 리스크 항목 수"],
    ["generated_at", new Date().toISOString(), "생성 시각"],
    ["data_policy", "not_legal_final", "PNU/공시지가/등기 증빙 완료 전 법무/PF 확정자료로 간주하지 않음"],
  ].forEach((row) => sheet.addRow(row));
  styleGrid(sheet);
  return sheet;
}

function defineNamedRanges(workbook: ExcelJS.Workbook) {
  defineWorkbookName(workbook, "risk_schema_version", "Import계약", "$B$2");
  defineWorkbookName(workbook, "risk_project_id", "Import계약", "$B$3");
  defineWorkbookName(workbook, "risk_project_name", "Import계약", "$B$4");
  defineWorkbookName(workbook, "risk_total_revenue", "핵심지표", "$B$2");
  defineWorkbookName(workbook, "risk_total_project_cost", "핵심지표", "$B$3");
  defineWorkbookName(workbook, "risk_pf_loan", "핵심지표", "$B$6");
  defineWorkbookName(workbook, "risk_base_exit_sale_rate", "핵심지표", "$B$7");
  defineWorkbookName(workbook, "risk_junior_exit_sale_rate", "핵심지표", "$B$10");
  defineWorkbookName(workbook, "risk_control_table", "20대리스크", "$A$1:$J$21");
}

async function resolveTemplateMetadata(): Promise<WorkbookTemplateMetadata> {
  const template = await resolveExcelWorkbookTemplate();
  if (!template) {
    return {
      templateMode: "fallback_mirror",
      templateId: "realytics-risk-report-compact",
      templateVersion: "v1-fallback",
      sourceType: "no_template_found",
      sourcePath: null,
      sourceFileName: null,
      sourceSha256: null,
      copiedSheetCount: 0,
      copiedSheetNames: [],
      resolvedAt: new Date().toISOString(),
    };
  }
  return {
    templateMode: "sample_package",
    templateId: template.templateId,
    templateVersion: template.templateVersion,
    sourceType: template.sourceType,
    sourcePath: template.sourcePath,
    sourceFileName: template.sourceFileName,
    sourceSha256: template.sourceSha256,
    copiedSheetCount: 0,
    copiedSheetNames: [],
    resolvedAt: new Date().toISOString(),
  };
}

export async function GET() {
  const workbook = new ExcelJS.Workbook();
  workbook.creator = "REALYTICS Platform9";
  workbook.created = new Date();
  workbook.modified = new Date();

  const templateMetadata = await resolveTemplateMetadata();

  addReportAddendumSheet(workbook);
  addMetricsSheet(workbook);
  addRiskControlsSheet(workbook);
  addCashFlowSummarySheet(workbook);
  addSourceMapSheet(workbook, templateMetadata);
  addImportContractSheet(workbook, templateMetadata);
  defineNamedRanges(workbook);

  workbook.worksheets.forEach((sheet) => {
    sheet.pageSetup = {
      paperSize: 9,
      orientation: "landscape",
      fitToPage: true,
      fitToWidth: 1,
      fitToHeight: 0,
      margins: {
        left: 0.3,
        right: 0.3,
        top: 0.5,
        bottom: 0.5,
        header: 0.2,
        footer: 0.2,
      },
    };
  });

  const buffer = await workbook.xlsx.writeBuffer();
  return new NextResponse(buffer, {
    headers: {
      "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "Content-Disposition": 'attachment; filename="REALYTICS_Risk_Report_Compact.xlsx"',
    },
  });
}
