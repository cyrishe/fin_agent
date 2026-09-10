// Generic rendering of the report projection shared with the standalone exporter.
import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const [input, output] = process.argv.slice(2);
const specs = JSON.parse(await fs.readFile(input, 'utf8'));
const wb = Workbook.create();
const safe = v => typeof v === 'string' && v.startsWith('=') ? "'" + v : v;
const lines = (v, width) => String(v ?? '').split('\n').reduce((n, line) =>
  n + Math.max(1, Math.ceil([...line].reduce((s, c) => s + (c.charCodeAt(0) > 255 ? 2 : 1), 0) / (width - 3))), 0);
for (const [i, spec] of specs.entries()) {
  const sheet = wb.worksheets.add(spec.name);
  const last = spec.rows.length + 6;
  sheet.showGridLines = false;
  const end = String.fromCharCode(64 + spec.headers.length);
  sheet.getRange(`A1:${end}${last}`).format = {font: {name:'Arial', size:11, color:'#253449'}, verticalAlignment:'top'};
  sheet.getRange('A2').values = [[spec.title]];
  sheet.getRange('A2').format.font = {bold:true, size:16};
  sheet.getRange('A2').format.rowHeight = 28;
  sheet.getRange('A3').values = [[spec.context]];
  sheet.getRange('A4').values = [[spec.note]];
  sheet.getRange(`A6:${end}${last}`).values = [spec.headers, ...spec.rows].map(row => row.map(safe));
  sheet.getRange(`A6:${end}${last}`).format.wrapText = true;
  sheet.getRange(`A6:${end}6`).format = {fill:'#24476B', font:{color:'#FFFFFF', bold:true},
    rowHeight:32, horizontalAlignment:'center', verticalAlignment:'center'};
  spec.widths.forEach((w, j) => sheet.getRange(`${String.fromCharCode(65+j)}1:${String.fromCharCode(65+j)}${last}`).format.columnWidth = w);
  spec.rows.forEach((row, j) => sheet.getRange(`A${j+7}:${end}${j+7}`).format.rowHeight =
    Math.min(spec.max_row_height ?? 300, Math.max(36, ...row.map((v,k) => lines(v, spec.widths[k])*15+12))));
  for (const [j, links] of Object.entries(spec.links ?? {})) {
    links.forEach((link, row) => {
      if (!/^完整阅读\.html#case-\d+$/.test(link)) throw new Error('Unexpected report link');
      sheet.getCell(row+6, Number(j)).format.font = {color:'#245FAA',underline:'single'};
    });
  }
  for (const [j, fmt] of Object.entries(spec.formats)) {
    const col = String.fromCharCode(65+Number(j));
    if (last > 6) sheet.getRange(`${col}7:${col}${last}`).setNumberFormat(fmt);
  }
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(i === 0 ? 2 : 1);
  if (last > 6) {
    const table = sheet.tables.add(`A6:${end}${last}`, true, `McpResults${i}`);
    table.style = 'TableStyleMedium2';
    spec.rows.forEach((_,j) => sheet.getRange(`A${j+7}:${end}${j+7}`).format.fill = j%2 ? '#F1F4F8' : '#FFFFFF');
  }
}
wb.recalculate();
console.log((await wb.inspect({kind:'table', range:'评测结果!A6:J9', tableMaxRows:4, tableMaxCols:10})).ndjson);
console.log((await wb.inspect({kind:'match', searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!', options:{useRegex:true,maxResults:20}})).ndjson);
if (process.env.FINANCE_EVAL_RENDER === '1') {
  for (const [i, spec] of specs.entries()) {
    const preview = await wb.render({sheetName:spec.name, range: `A1:${i === 0 ? 'D' : 'H'}8`, scale:1, format:'png'});
    await fs.writeFile(path.join(path.dirname(output), `preview-${i}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
}
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
