// Render the supplied reference, then produce the v2 human-label workbook.
// Artifact Tool comes from the desktop's bundled runtime, never repository deps.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const options = Object.fromEntries(process.argv.slice(2).map((value, index, all) =>
  value.startsWith('--') ? [value.slice(2), all[index + 1]?.startsWith('--')
    ? true : all[index + 1] ?? true] : null).filter(Boolean));
for (const key of ['input-xlsx', 'runtime-node-modules', 'preview-dir']) {
  if (typeof options[key] !== 'string') throw new Error(`Missing --${key}`);
}
const require = createRequire(path.resolve(options['runtime-node-modules'], '__entry__.cjs'));
const { FileBlob, SpreadsheetFile } = await import(pathToFileURL(
  require.resolve('@oai/artifact-tool')).href);
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(options['input-xlsx']));
const previewDir = path.resolve(options['preview-dir']);
await fs.mkdir(previewDir, { recursive: true });

if (options['inspect-original']) {
  console.log((await workbook.inspect({ kind: 'sheet', include: 'id,name', maxChars: 2000 })).ndjson);
  for (const [sheetName, range, name] of [
    ['Разметка', 'A2:K15', 'reference-labels.png'],
    ['Вакансии', 'A2:D15', 'reference-source.png'],
  ]) {
    const rendered = await workbook.render({ sheetName, range, scale: 1.5 });
    await fs.writeFile(path.join(previewDir, name), new Uint8Array(await rendered.arrayBuffer()));
  }
  process.exit(0);
}

for (const key of ['data', 'output']) {
  if (typeof options[key] !== 'string') throw new Error(`Missing --${key}`);
}
const outputPath = path.resolve(options.output);
if ([options['input-xlsx'], options.data].some(value => path.resolve(value) === outputPath)) {
  throw new Error('Output must not replace an input');
}
try {
  await fs.access(outputPath);
  throw new Error('Output already exists; choose a new path');
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}
const data = JSON.parse(await fs.readFile(options.data, 'utf8'));
if (data.schema !== 'vacancy-gold-workbook-v2' || data.header_row !== 10
    || !Array.isArray(data.cases) || !data.cases.length || !Array.isArray(data.roles)) {
  throw new Error('Unsupported workbook data');
}
const sheet = workbook.worksheets.getItem('Разметка');
const firstRow = data.first_data_row;
const lastRow = firstRow + data.cases.length - 1;
const originalIds = sheet.getRange(`K${firstRow}:K${lastRow}`).values.map(row => row[0]);
if (JSON.stringify(originalIds) !== JSON.stringify(data.cases.map(item => item.job_uid))) {
  throw new Error('The selection or order differs from the reference workbook');
}
const sourceSheet = workbook.worksheets.getItem('Вакансии');
const originalSource = JSON.stringify(sourceSheet.getUsedRange().values);
// Keep the original native navigation in J and stable IDs in K. Clearing
// contents intentionally preserves these links, and source row positions stay.
for (const table of [...sheet.tables.items]) table.delete();
sheet.getRange(`A1:M${lastRow}`).clear({ applyTo: 'all' });
// Range.clear(all) does not remove validation. Clear it explicitly before
// replacing the four former score columns with the v2 input controls.
sheet.getRange(`A1:M${lastRow}`).dataValidation = null;

const font = 'Arial';
sheet.getRange(`A1:M${lastRow}`).format.font = { name: font, size: 11, color: '#243B53' };
sheet.getRange(`A1:M${lastRow}`).format.verticalAlignment = 'center';
sheet.getRange('A2').values = [['Разметка вакансий. Одна роль и одна оценка']];
sheet.getRange('A2').format.font = { name: font, size: 16, bold: true, color: '#243B53' };
sheet.getRange('A2:H2').format.rowHeight = 28;
sheet.getRange('A3:H3').format.borders = {
  bottom: { style: 'thin', color: '#BCCCDC' },
};
const instructions = [
  'Заполните жёлтые ячейки D–G. Сначала выберите одну роль, затем оцените релевантность.',
  'Шкала: 0 — не подходит; 1 — сомнительно; 2 — стоит рассмотреть; 3 — сильное совпадение.',
  'Условия: Подходят — подтверждены; Не подходят — есть препятствие; Неясно — сведений мало.',
  'Метка — основная профессия вакансии. Other / unclear — если подходящей роли нет или она неясна.',
  '«Читать» открывает исходное описание. При переносе ответов укажите дату и способ разметки.',
];
instructions.forEach((text, index) => {
  sheet.getRange(`A${index + 4}`).values = [[text]];
  sheet.getRange(`A${index + 4}:H${index + 4}`).format.rowHeight = 22;
  sheet.getRange(`A${index + 4}`).format.font = { name: font, size: 11, color: '#52667A' };
});
const headers = ['№', 'Компания', 'Вакансия', 'Метка роли', 'Релевантность',
  'Условия найма', 'Комментарий', 'География', 'Режим работы', 'Описание',
  'job_uid', 'source_hash', 'label_scope'];
sheet.getRange('A10:M10').values = [headers];
const roleNames = new Map(data.roles.map(role => [role.id, role.name]));
const gates = { eligible: 'Подходят', ineligible: 'Не подходят', unknown: 'Неясно' };
const literal = value => typeof value === 'string' && value.startsWith('=') ? `'${value}` : value;
sheet.getRange(`A${firstRow}:M${lastRow}`).values = data.cases.map((item, index) => [
  index + 1, literal(item.source.company), literal(item.source.title),
  item.role_label == null ? null : roleNames.get(item.role_label),
  item.relevance, item.gate_outcome == null ? null : gates[item.gate_outcome],
  literal(item.notes || ''), literal(item.source.location_text || ''),
  literal(item.source.remote_text || ''), 'Читать', item.job_uid, item.source_hash, item.label_scope,
]);
// Include immutable identity in the same table: native Excel sorting must move
// answers and their job_uid/source_hash together.
const table = sheet.tables.add(`A10:M${lastRow}`, true, 'GoldLabelsV2');
table.style = 'TableStyleMedium2';
sheet.getRange('A10:M10').format = {
  fill: '#243B53', font: { name: font, size: 11, bold: true, color: '#FFFFFF' },
  wrapText: true, horizontalAlignment: 'center', verticalAlignment: 'center', rowHeight: 38,
};
sheet.getRange(`A${firstRow}:C${lastRow}`).format.fill = '#F1F5F9';
sheet.getRange(`D${firstRow}:G${lastRow}`).format.fill = '#FFF2CC';
sheet.getRange(`H${firstRow}:I${lastRow}`).format.fill = '#F1F5F9';
sheet.getRange(`K${firstRow}:M${lastRow}`).format.font = { name: font, size: 9, color: '#6B7C8F' };
sheet.getRange(`J${firstRow}:J${lastRow}`).format = {
  fill: '#EAF3FA', font: { name: font, size: 11, color: '#225F9C' },
  horizontalAlignment: 'center',
};
sheet.getRange(`A${firstRow}:M${lastRow}`).format.wrapText = true;
sheet.getRange(`A${firstRow}:A${lastRow}`).setNumberFormat('0');
sheet.getRange(`E${firstRow}:E${lastRow}`).setNumberFormat('0');
sheet.getRange(`A${firstRow}:A${lastRow}`).format.horizontalAlignment = 'right';
sheet.getRange(`E${firstRow}:E${lastRow}`).format.horizontalAlignment = 'right';
sheet.getRange(`D${firstRow}:D${lastRow}`).dataValidation = {
  rule: { type: 'list', values: data.roles.map(role => role.name) },
};
sheet.getRange(`E${firstRow}:E${lastRow}`).dataValidation = {
  rule: { type: 'list', values: ['0', '1', '2', '3'] },
};
sheet.getRange(`F${firstRow}:F${lastRow}`).dataValidation = {
  rule: { type: 'list', values: Object.values(gates) },
};
for (const [column, width] of Object.entries({
  A: 5, B: 24, C: 43, D: 31, E: 17, F: 21, G: 36, H: 26, I: 18, J: 12, K: 69, L: 69, M: 22,
})) sheet.getRange(`${column}1:${column}${lastRow}`).format.columnWidth = width;
data.cases.forEach((item, index) => {
  sheet.getRange(`A${firstRow + index}:M${firstRow + index}`).format.rowHeight = Math.max(
    48, Math.ceil(item.source.title.length / 42) * 14 + 12,
    Math.ceil(item.source.company.length / 23) * 14 + 12,
    Math.ceil((item.source.location_text || '').length / 25) * 14 + 12,
    Math.ceil((item.source.remote_text || '').length / 17) * 14 + 12,
  );
});
sheet.freezePanes.freezeRows(10);
sheet.freezePanes.freezeColumns(3);
if (JSON.stringify(sourceSheet.getUsedRange().values) !== originalSource) {
  throw new Error('The source worksheet changed');
}
workbook.recalculate();
console.log((await workbook.inspect({ kind: 'region', sheetId: 'Разметка',
  range: 'A10:G13', maxChars: 3000, tableMaxCellChars: 100 })).ndjson);
console.log((await workbook.inspect({ kind: 'match',
  searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',
  options: { useRegex: true, maxResults: 20 }, summary: 'Final formula error scan' })).ndjson);
const preview = await workbook.render({ sheetName: 'Разметка', range: 'A2:J16', scale: 1.5 });
await fs.writeFile(path.join(previewDir, 'gold-v2-labels.png'), new Uint8Array(await preview.arrayBuffer()));
const finalSource = await workbook.render({ sheetName: 'Вакансии', range: 'A2:B15', scale: 1.5 });
await fs.writeFile(path.join(previewDir, 'gold-v2-source.png'), new Uint8Array(await finalSource.arrayBuffer()));
await fs.mkdir(path.dirname(outputPath), { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
// Publish only a new destination. The reference can still be open in Excel.
const temporary = path.join(path.dirname(outputPath), `.${randomUUID()}.xlsx`);
try {
  await xlsx.save(temporary);
  await fs.chmod(temporary, 0o600);
  await fs.link(temporary, outputPath);
} finally {
  await fs.unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
}
console.log(JSON.stringify({ output: outputPath, selectedCases: data.cases.length,
  schema: data.gold_schema, sourceWorksheetPreserved: true }));
