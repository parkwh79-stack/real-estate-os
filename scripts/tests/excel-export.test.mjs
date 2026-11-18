import test from 'node:test';
import assert from 'node:assert/strict';
import ExcelJS from 'exceljs';

test('Excel export preserves numeric values, formulas and evidence links after dependency update', async () => {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet('검토용 CF');
  sheet.addRow(['매출', '비용', '이익', '근거']);
  sheet.addRow([853951767703.25, 779350253545.2485,
    {formula: 'A2-B2', result: 74601514158.00154},
    {text: '원본 I24', hyperlink: 'https://example.com/evidence#I24'}]);
  const bytes = await workbook.xlsx.writeBuffer();
  assert.ok(bytes.byteLength > 1000);
  const restored = new ExcelJS.Workbook();
  await restored.xlsx.load(bytes);
  const result = restored.getWorksheet('검토용 CF');
  assert.equal(result.getCell('A2').value, 853951767703.25);
  assert.deepEqual(result.getCell('C2').value, {formula: 'A2-B2', result: 74601514158.00154});
  assert.equal(result.getCell('D2').value.hyperlink, 'https://example.com/evidence#I24');
});
