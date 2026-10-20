import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = process.env.B2_R48_OUT ?? "outputs/stock_automl/b2_daily_validation_join_r48_20261020";
const payload = JSON.parse(await fs.readFile(path.join(root, "workbook_data.json"), "utf8"));
const wb = Workbook.create();
const ws = wb.worksheets.add("逐笔验证");
ws.showGridLines = false;
ws.tabColor = "#174E5B";
ws.getRange("A2").values = [[payload.title]];
ws.getRange("A2").format.font = { name: "Arial", size: 14, bold: true, color: "#174E5B" };
ws.getRange("A3").values = [[payload.note]];
ws.getRange("A3").format.font = { name: "Arial", size: 10, italic: true, color: "#52636B" };

const headers = payload.headers;
const rows = payload.rows;
if (headers.length !== 34 || rows.some(row => row.length !== headers.length)) {
  throw new Error("R48 header and row width mismatch");
}
const dateCols = [0, 1, 11, 18, 19, 21, 22];
const date = value => value ? new Date(`${value}T12:00:00`) : null;
const data = rows.map(row => {
  const out = [...row];
  for (const col of dateCols) out[col] = date(out[col]);
  return out;
});
const header = ws.getRangeByIndexes(3, 0, 1, headers.length);
header.values = [headers];
header.format = { fill: "#174E5B", font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
ws.getRange("S4:AH4").format.fill = "#315577";
header.format.rowHeight = 27;
header.format.horizontalAlignment = "center";
const body = ws.getRangeByIndexes(4, 0, data.length, headers.length);
body.values = data;
body.format.font = { name: "Arial", size: 10, color: "#22313A" };
body.format.rowHeight = 21;
body.format.verticalAlignment = "center";
for (const col of dateCols) {
  ws.getRangeByIndexes(4, col, data.length, 1).format.numberFormat = "yyyy-mm-dd";
}
for (const col of [5, 6, 7, 10, 13, 24, 27, 28, 31, 32]) {
  ws.getRangeByIndexes(4, col, data.length, 1).format.numberFormat = "0.00%";
}
ws.getRangeByIndexes(4, 3, data.length, 1).format.numberFormat = "0.0000";
for (const col of [9, 12]) {
  ws.getRangeByIndexes(4, col, data.length, 1).format.numberFormat = "0.00";
}
for (const col of [14, 16]) {
  ws.getRangeByIndexes(4, col, data.length, 1).format.numberFormat = "#,##0";
}
const widths = headers.map((_, i) => dateCols.includes(i) ? 15 : 12);
widths[4] = 15;
widths[14] = 15;
widths[16] = 15;
widths[23] = 15;
widths[28] = 17;
widths[32] = 17;
widths[33] = 19;
widths.forEach((width, i) => ws.getRangeByIndexes(0, i, 1, 1).format.columnWidth = width);
ws.tables.add(`A4:AH${data.length + 4}`, true, "R48_TradeValidation");
ws.freezePanes.freezeRows(4);

wb.recalculate();
const check = await wb.inspect({ kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 50 }, maxChars: 2000 });
console.log(check.ndjson);
for (const [name, range] of [["trade", "A1:R17"], ["validation", "S1:AH17"]]) {
  const preview = await wb.render({ sheetName: "逐笔验证", range, scale: 1.25, format: "png" });
  await fs.writeFile(path.join(root, `preview_${name}.png`),
                     new Uint8Array(await preview.arrayBuffer()));
}
const filename = path.join(root, "B2_日更6训2验_逐笔验证_R48_20261020.xlsx");
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(filename);
console.log(filename);
