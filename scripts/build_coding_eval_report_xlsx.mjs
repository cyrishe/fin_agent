import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "/Users/chenghe/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const input = process.argv[2];
const output = process.argv[3];
if (!input || !output) throw new Error("usage: build_coding_eval_report_xlsx.mjs probe.json output.xlsx");

const probe = JSON.parse(await fs.readFile(input, "utf8"));
const evidence = probe.evidence || {};
const full = evidence.full_test_result || {};
const sample = (full.cases || [])[0] || {};
const actual = sample.actual || full.data || {};
const keyProcess = actual.key_process_info || {};
const meta = evidence.context_bundle || {};
const implementation = evidence.implementation_explanation || {};
const review = evidence.implementation_review || {};

const stringify = (value, limit = 9000) => {
  const text = value == null ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return text.length > limit ? `${text.slice(0, limit)}\n…[已截断]` : text;
};

const wb = Workbook.create();
const summary = wb.worksheets.add("案例汇总");
const turns = wb.worksheets.add("逐轮明细");
const notes = wb.worksheets.add("测试说明");

const summaryRows = [
  ["字段", "结果"],
  ["case", "golden_cross_30_60"],
  ["Design 来源", "tests/fixtures/golden_cross_30_60_design.json"],
  ["工具", evidence.tool?.manifest?.display_name || evidence.tool?.manifest?.tool_name || ""],
  ["Coding 状态", evidence.ok ? "完成并生成 draft" : "失败"],
  ["模型路径", `${evidence.provider || ""} / ${evidence.model || ""} / ${evidence.reasoning_effort || ""}`],
  ["Codex 调用耗时(ms)", Number(evidence.duration_ms || 0)],
  ["技术测试", evidence.test_summary || full.summary || ""],
  ["样例输入", stringify(sample.input)],
  ["样例实际输出", stringify(actual)],
  ["核心过程指标 key_process_info", stringify(keyProcess)],
  ["执行日志", stringify(sample.logs)],
  ["实现说明", implementation.summary || ""],
  ["需求—Design—Code 对齐", review.summary || ""],
  ["结论", evidence.ok && evidence.test_execution_ok ? "基本可行：动态代码已生成，样例运行通过；业务结果仍由用户确认。" : "未完成：需要查看真实错误后继续。"],
];

const turnRows = [[
  "dialogue_id", "turn_id", "stage", "user_question", "context", "agent_answer", "elapsed_ms",
  "provider", "model", "test_status", "actual_output", "key_process_info", "logs", "alignment_review",
]];
turnRows.push([
  "golden_cross_coding_001", 1, "coding",
  "请根据已经确认的近30/60交易日金叉 Design 实现动态工具，并完成必要的样例技术测试。",
  "已有已确认 Design：单股、30/60 个完整交易日、复权收盘价 MA5/MA20 金叉；本轮进入 Coding。\n资料包：" + stringify(meta, 5000),
  evidence.message || "",
  Number(evidence.duration_ms || 0), evidence.provider || "", evidence.model || "",
  evidence.test_execution_ok ? "passed" : "failed", stringify(actual), stringify(keyProcess),
  stringify(sample.logs), `${implementation.summary || ""}\n${review.summary || ""}`,
]);

const noteRows = [
  ["项目", "说明"],
  ["测试类型", "真实 Coding 主链路：从已确认 Design 进入 Codex，读取 API Catalog、生成动态模块并运行样例。不是意图单步回放。"],
  ["测试对象", "golden_cross_30_60_design：判断单只股票最近 30/60 个完整交易日内的 MA5/MA20 金叉。"],
  ["技术判定", "只判断动态入口可执行、输出契约可满足、测试运行不报错；不替用户判断金叉策略是否符合业务预期。"],
  ["核心输出", "样例输出包含 status、found、signal_count、signal 日期/均线，以及 key_process_info；日志同时保留查询数量、日期和价格字段。"],
  ["对齐说明", "Coding 结果说明了行情查询、复权收盘价清洗、均线计算和金叉检测对应的实现模块；本表保留了该说明。"],
  ["上下文资料", stringify(meta, 7000)],
];

function style(sheet, range, widths) {
  sheet.showGridLines = false;
  const body = sheet.getRange(range);
  body.format.wrapText = true;
  body.format.font = { name: "Aptos", size: 10, color: "#1F2937" };
  body.format.borders = { preset: "inside", style: "thin", color: "#E5E7EB" };
  const header = sheet.getRange(`A1:${range.split(":")[1].replace(/\d+$/, "1")}`);
  header.format.fill = "#1D4ED8";
  header.format.font = { name: "Aptos Display", size: 10, bold: true, color: "#FFFFFF" };
  header.format.wrapText = true;
  for (const [column, width] of Object.entries(widths)) sheet.getRange(`${column}:${column}`).format.columnWidth = width;
}

summary.getRangeByIndexes(0, 0, summaryRows.length, 2).values = summaryRows;
style(summary, `A1:B${summaryRows.length}`, { A: 28, B: 110 });
summary.getRange("B7").format.numberFormat = "#,##0";
summary.freezePanes.freezeRows(1);

turns.getRangeByIndexes(0, 0, turnRows.length, turnRows[0].length).values = turnRows;
style(turns, `A1:N${turnRows.length}`, { A: 24, B: 10, C: 12, D: 45, E: 72, F: 80, G: 14, H: 12, I: 18, J: 14, K: 90, L: 52, M: 70, N: 70 });
turns.getRange("G2:G2").format.numberFormat = "#,##0";
turns.freezePanes.freezeRows(1);

notes.getRangeByIndexes(0, 0, noteRows.length, 2).values = noteRows;
style(notes, `A1:B${noteRows.length}`, { A: 22, B: 120 });
notes.freezePanes.freezeRows(1);

const outputDir = output.substring(0, output.lastIndexOf("/"));
await fs.mkdir(outputDir, { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(output);
const inspect = await wb.inspect({ kind: "table", sheetId: "案例汇总", range: `A1:B${summaryRows.length}`, include: "values,formulas", tableMaxRows: 20, tableMaxCols: 2 });
const preview = await wb.render({ sheetName: "案例汇总", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(output.replace(/\.xlsx$/, "_summary_preview.png"), new Uint8Array(await preview.arrayBuffer()));
const turnsPreview = await wb.render({ sheetName: "逐轮明细", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(output.replace(/\.xlsx$/, "_turn_preview.png"), new Uint8Array(await turnsPreview.arrayBuffer()));
console.log(JSON.stringify({ output, summary_preview: output.replace(/\.xlsx$/, "_summary_preview.png"), turn_preview: output.replace(/\.xlsx$/, "_turn_preview.png"), inspect: inspect.ndjson }));
