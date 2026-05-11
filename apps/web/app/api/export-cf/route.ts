import { NextResponse } from "next/server";
import ExcelJS from "exceljs";
import { demoProject, generateCashFlowRows } from "../../../lib/platform-data";

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

export async function GET() {
  const workbook = new ExcelJS.Workbook();
  workbook.creator = "REALYTICS Platform9";
  workbook.created = new Date();

  const sheet = workbook.addWorksheet("Cash Flow Schedule");
  const monthColumns = Array.from({ length: 24 }, (_, index) => ({
    header: `M${index + 1}`,
    key: `m${index + 1}`,
    width: 12,
  }));

  sheet.columns = [
    { header: "구분", key: "category", width: 18 },
    { header: "항목", key: "subCategory", width: 22 },
    { header: "Total", key: "total", width: 14 },
    ...monthColumns,
  ];

  sheet.mergeCells("A1:C1");
  sheet.getCell("A1").value = `${demoProject.name} Cash Flow`;
  sheet.getCell("A1").font = { bold: true, size: 14 };

  sheet.spliceRows(2, 0, [
    "구분",
    "항목",
    "Total",
    ...Array.from({ length: 24 }, (_, index) => `M${index + 1}`),
  ]);

  const headerRow = sheet.getRow(2);
  headerRow.font = { bold: true, color: { argb: "FF0F172A" } };
  headerRow.alignment = { horizontal: "center", vertical: "middle" };
  headerRow.fill = {
    type: "pattern",
    pattern: "solid",
    fgColor: { argb: "FFE2E8F0" },
  };

  const rows = generateCashFlowRows();
  rows.forEach((row) => {
    sheet.addRow({
      category: row.category,
      subCategory: row.subCategory,
      total: row.total,
      ...Object.fromEntries(row.values.map((value, index) => [`m${index + 1}`, value])),
    });
  });

  for (let rowIndex = 3; rowIndex <= rows.length + 2; rowIndex += 1) {
    const row = sheet.getRow(rowIndex);
    const source = rows[rowIndex - 3];

    if (source?.isTotal) {
      row.font = { bold: true };
      row.fill = {
        type: "pattern",
        pattern: "solid",
        fgColor: { argb: source.category === "Cumulative CF" ? "FFDBEAFE" : "FFF8FAFC" },
      };
    }

    for (let columnIndex = 3; columnIndex <= 27; columnIndex += 1) {
      row.getCell(columnIndex).numFmt = '#,##0.0;[Red]-#,##0.0;"-"';
    }
  }

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
      };
    });
  });

  sheet.views = [{ state: "frozen", xSplit: 2, ySplit: 2 }];
  sheet.autoFilter = {
    from: "A2",
    to: `${columnName(27)}2`,
  };

  const buffer = await workbook.xlsx.writeBuffer();

  return new NextResponse(buffer, {
    status: 200,
    headers: {
      "Content-Disposition": 'attachment; filename="Platform9_CashFlow_Schedule.xlsx"',
      "Content-Type":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
  });
}
