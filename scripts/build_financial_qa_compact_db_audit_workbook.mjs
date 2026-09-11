import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [auditPath, outputPath] = process.argv.slice(2);
if (!auditPath || !outputPath) throw new Error("usage: node build_financial_qa_compact_db_audit_workbook.mjs audit.json output.xlsx");
const audit = JSON.parse(await fs.readFile(auditPath, "utf8"));
const cases = audit.cases || [];
const wb = Workbook.create();
const main = wb.worksheets.add("逐题审计");
const zeros = wb.worksheets.add("零行核对");

const C = { navy: "#17365D", teal: "#0F6B78", blue: "#D9EAF7", pale: "#F4F7FA", green: "#E2F0D9", amber: "#FFF2CC", red: "#FCE4D6", white: "#FFFFFF", ink: "#1F2937" };
const safe = (v, n = 12000) => String(v ?? "").slice(0, n);
const compactJson = (v, n = 1400) => safe(JSON.stringify(v ?? {}, null, 0), n);
const sqlStatements = (record) => {
  const output = [];
  for (const execution of record.db_executions || []) {
    const query = (execution.sql || []).filter(x => /^SELECT|^WITH/i.test(x.sql || "")).at(-1);
    if (query) output.push(`${execution.result_name || "result"} ${execution.api}\n${safe(query.sql, 7000)}\nparams=${compactJson(query.params, 1000)}`);
    else if (execution.error) output.push(`${execution.result_name || "result"} ${execution.api}\n未生成SQL：${safe(execution.error, 500)}`);
  }
  for (const diag of record.zero_diagnostics || []) {
    output.push(`${diag.result_name || "result"} ${diag.api} [独立零行核对]\n${safe(diag.best_sql, 5000)}\nparams=${compactJson(diag.params, 1000)}`);
  }
  return output.join("\n\n");
};
const dbCounts = (record) => {
  const output = (record.db_executions || []).map(e => `${e.result_name || "result"} ${e.api}=${e.row_count ?? "错误"}`);
  for (const d of record.zero_diagnostics || []) output.push(`${d.result_name || "result"} 独立SQL=${d.matched_rows}`);
  return output.join("\n");
};
const datasetCounts = (record) => (record.original_datasets || []).map(d => `${d.result_name} ${d.api}=${d.row_count}行`).join("\n");
const examples = (record) => (record.original_datasets || []).map(d => {
  const rows = d.examples || [];
  return `${d.result_name} ${d.api}\n${rows.length ? rows.map((row, i) => `${i + 1}. ${compactJson(row)}`).join("\n") : "无数据"}`;
}).join("\n\n");
const analysisText = (record) => {
  const a = record.structured_analysis || {};
  return `路由=${safe(a.路由, 300)}\n原结果=${datasetCounts(record).replaceAll("\n", "；")}\nDB证据=${safe(a.证据, 900)}\n判断=${safe(a.结论, 500)}`;
};

const headers = ["题号", "问题", "所选工具", "执行时间(秒)", "各数据集条数", "数据事例（各前2条）", "直查SQL与参数", "DB记录数", "结构化判断与核心证据"];
const rows = cases.map(c => [
  c.case_id,
  c.question,
  (c.selected_tools || []).join(" → "),
  Number(c.elapsed_ms || 0) / 1000,
  datasetCounts(c),
  examples(c),
  sqlStatements(c),
  dbCounts(c),
  analysisText(c),
]);
main.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...rows];
main.getRange("A1:I1").format = { fill: C.navy, font: { bold: true, color: C.white }, wrapText: true, verticalAlignment: "center", borders: { preset: "inside", style: "thin", color: "#D9E2F3" } };
main.getRange("A2:I185").format = { font: { color: C.ink, size: 9 }, wrapText: true, verticalAlignment: "top", borders: { insideHorizontal: { style: "thin", color: "#D9E2F3" } } };
main.getRange("D2:D185").format.numberFormat = "0.00";
main.getRange("A2:I185").format.rowHeight = 105;
main.getRange("A1:I1").format.rowHeight = 30;
main.getRange("A:A").format.columnWidth = 12;
main.getRange("B:B").format.columnWidth = 46;
main.getRange("C:C").format.columnWidth = 30;
main.getRange("D:D").format.columnWidth = 15;
main.getRange("E:E").format.columnWidth = 29;
main.getRange("F:F").format.columnWidth = 72;
main.getRange("G:G").format.columnWidth = 88;
main.getRange("H:H").format.columnWidth = 26;
main.getRange("I:I").format.columnWidth = 62;
main.tables.add("A1:I185", true, "CaseAudit");
main.freezePanes.freezeRows(1);
main.freezePanes.freezeColumns(2);
main.showGridLines = false;

const zeroHeaders = ["题号", "问题", "数据集", "原记录数", "独立核对SQL", "参数", "DB记录数", "核心证据", "判断"];
const zeroRows = [];
for (const record of cases) {
  for (const d of record.zero_diagnostics || []) {
    zeroRows.push([record.case_id, record.question, `${d.result_name} ${d.api}`, 0, d.best_sql, compactJson(d.params, 1200), d.matched_rows, d.evidence, d.verdict]);
  }
}
zeros.getRangeByIndexes(0, 0, zeroRows.length + 1, zeroHeaders.length).values = [zeroHeaders, ...zeroRows];
zeros.getRange(`A1:I1`).format = { fill: C.teal, font: { bold: true, color: C.white }, wrapText: true, verticalAlignment: "center", borders: { preset: "inside", style: "thin", color: "#D9E2F3" } };
zeros.getRange(`A2:I${zeroRows.length + 1}`).format = { font: { color: C.ink, size: 10 }, wrapText: true, verticalAlignment: "top", borders: { insideHorizontal: { style: "thin", color: "#D9E2F3" } } };
zeros.getRange(`A2:I${zeroRows.length + 1}`).format.rowHeight = 66;
zeros.getRange("A:A").format.columnWidth = 12;
zeros.getRange("B:B").format.columnWidth = 48;
zeros.getRange("C:C").format.columnWidth = 30;
zeros.getRange("D:D").format.columnWidth = 12;
zeros.getRange("E:E").format.columnWidth = 86;
zeros.getRange("F:F").format.columnWidth = 34;
zeros.getRange("G:G").format.columnWidth = 14;
zeros.getRange("H:H").format.columnWidth = 58;
zeros.getRange("I:I").format.columnWidth = 48;
zeros.tables.add(`A1:I${zeroRows.length + 1}`, true, "ZeroAudit");
zeros.freezePanes.freezeRows(1);
zeros.freezePanes.freezeColumns(2);
zeros.showGridLines = false;
zeros.getRange(`I2:I${zeroRows.length + 1}`).conditionalFormats.add("containsText", { text: "不正确", format: { fill: C.red, font: { bold: true, color: "#9C0006" } } });
zeros.getRange(`I2:I${zeroRows.length + 1}`).conditionalFormats.add("containsText", { text: "不应作为结论", format: { fill: C.amber, font: { bold: true, color: "#9C6500" } } });
zeros.getRange(`I2:I${zeroRows.length + 1}`).conditionalFormats.add("containsText", { text: "正确", format: { fill: C.green, font: { color: "#375623" } } });

await fs.mkdir(new URL(".", `file://${outputPath}`).pathname, { recursive: true }).catch(() => {});
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(outputPath);
const inspect = await wb.inspect({ kind: "table", range: "零行核对!A1:I29", include: "values,formulas", tableMaxRows: 30, tableMaxCols: 9, tableMaxCellChars: 220 });
await fs.writeFile(`${outputPath}.inspection.ndjson`, inspect.ndjson);
const errors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 300 }, summary: "formula error scan" });
await fs.writeFile(`${outputPath}.errors.ndjson`, errors.ndjson);
for (const [sheetName, range] of Object.entries({ "逐题审计": "A1:I12", "零行核对": "A1:I29" })) {
  const image = await wb.render({ sheetName, range, scale: 0.75, format: "png" });
  await fs.writeFile(`${outputPath}.${sheetName}.png`, new Uint8Array(await image.arrayBuffer()));
}
