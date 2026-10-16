import fs from "node:fs/promises";
import path from "node:path";
import {
  SpreadsheetFile,
  Workbook,
} from "/Users/chenghe/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const repo = process.cwd();
const e2ePath = path.join(repo, "outputs/evals/requirement_design_coding_isolated_20260724.json");
const directDir = path.join(repo, "outputs/evals/coding_current_20260724");
const output = path.join(repo, "outputs/evals/fin_agent_coding_eval_20260724.xlsx");
const e2e = JSON.parse(await fs.readFile(e2ePath, "utf8"));
const directFiles = (await fs.readdir(directDir))
  .filter((name) => name.endsWith(".json") && !name.startsWith("._"))
  .sort();
const directs = await Promise.all(
  directFiles.map(async (name) => JSON.parse(await fs.readFile(path.join(directDir, name), "utf8"))),
);

const text = (value, max = 16000) => {
  const result = value == null ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return result.length > max ? `${result.slice(0, max)}\n…[已截断]` : result;
};

const directReview = {
  golden_cross_current: {
    process: "stock_code、查询状态、样本数、起止日期、观察窗口、信号数量",
    judgment: "基本可行",
    note: "核心指标数量适中，直接对应数据完整性、窗口和金叉结果；首次实现即通过。",
  },
  volume_breakout_current: {
    process: "查询状态、样本数、20日窗口、1.5倍阈值、实际日期、命中数、跳过数",
    judgment: "基本可行但实现有一次真实返工",
    note: "核心指标清晰；首次测试暴露缺失目标日未计入 skipped_count，Codex 修复后通过。",
  },
  valuation_percentile_current: {
    process: "股票标识、PE/PB当前值、PE/PB有效样本数、估值分位和评价",
    judgment: "基本可行",
    note: "过程指标围绕当前值、样本有效性和分位结果，没有铺开原始历史序列。",
  },
};

const wb = Workbook.create();
const overview = wb.worksheets.add("评测概览");
const e2eTurns = wb.worksheets.add("串联逐轮");
const directSheet = wb.worksheets.add("直接Coding");
const findings = wb.worksheets.add("发现与判断");

const overviewRows = [
  ["Fin Agent Coding 主线评测", "结果"],
  ["测试日期", "2026-07-24"],
  ["基础回归", "75 / 75 通过"],
  ["真实 Chat API 串联", "未进入 Agent：aiia_system 拒绝当前公网 IP 连接"],
  ["隔离串联案例", e2e.summary.cases],
  ["隔离串联通过", e2e.summary.passed],
  ["直接 Coding 案例", directs.length],
  ["直接 Coding 通过", directs.filter((item) => item.execution_ok && item.contract_ok).length],
  ["直接 Coding 平均耗时(ms)", { formula: `=AVERAGE('直接Coding'!F2:F${directs.length + 1})` }],
  ["当前结论", "基础功能链路基本可用；完整 Chat/DB 恢复链路仍被数据库访问权限阻断，交互效果尚不能宣告完成。"],
];
overview.getRangeByIndexes(0, 0, overviewRows.length, 2).values = overviewRows.map((row) => [
  row[0],
  typeof row[1] === "object" ? null : row[1],
]);
overview.getRange("B9").formulas = [[overviewRows[8][1].formula]];

const e2eHeader = [
  "case_id", "turn", "stage", "用户输入", "Agent回答", "需求摘要/Design",
  "待确认问题", "本轮耗时(ms)", "阶段耗时", "技术测试", "key_process_info",
  "实现说明", "需求-Design-Code对齐", "我的判断",
];
const e2eRows = [e2eHeader];
for (const item of e2e.cases || []) {
  for (const turn of item.turns || []) {
    const test = turn.test_result || {};
    const actual = (test.cases || [])[0]?.actual || test.data || {};
    const isProfitEmpty = item.case_id === "e2e_profit_cash_quality" && turn.stage === "coding";
    let judgment = "阶段方向正常。";
    if (turn.stage === "coding") {
      judgment = isProfitEmpty
        ? "技术通过但用户不可验证：实际财务数据为空，仅展示计数，不能证明盈利质量逻辑。"
        : "基本可行：真实输出包含三个收益率、最大回撤、判断标签和紧凑过程依据。";
    } else if (turn.stage === "requirement") {
      judgment = "需求能继续推进；但默认扩展为全市场筛选、固定报告期和排除规则，存在业务发散。";
    } else if (turn.stage === "design") {
      judgment = "设计完整但首屏文本偏长，后续应折叠细节并突出目标、核心规则和预期结果。";
    }
    e2eRows.push([
      item.case_id,
      turn.turn,
      turn.stage,
      text(turn.user_input, 5000),
      text(turn.agent_message, 5000),
      text(turn.requirement_brief || turn.design?.document, 12000),
      text(turn.questions, 4000),
      Number(turn.elapsed_ms || 0),
      text(turn.stage_duration_ms, 3000),
      text(test.summary || test.error, 3000),
      text(actual.key_process_info, 6000),
      text(turn.implementation_explanation, 5000),
      text(turn.implementation_review, 5000),
      judgment,
    ]);
  }
}
e2eTurns.getRangeByIndexes(0, 0, e2eRows.length, e2eHeader.length).values = e2eRows;

const directHeader = [
  "case", "Design来源", "工具", "技术执行", "契约检查", "耗时(ms)", "Codex工具调用",
  "API资料读取", "测试调用", "失败测试", "修复模块次数", "首次通过", "核心过程指标",
  "我的判断", "说明",
];
const directRows = [directHeader];
for (const item of directs) {
  const review = directReview[item.label] || {};
  directRows.push([
    item.label,
    item.design_path,
    item.tool_name,
    item.execution_ok ? "通过" : "失败",
    item.contract_ok ? "通过" : "失败",
    Number(item.duration_ms || 0),
    Number(item.tool_calls || 0),
    Number(item.api_reference_calls || 0),
    Number(item.test_calls || 0),
    Number(item.failed_test_calls || 0),
    Number(item.repair_module_patch_calls || 0),
    item.first_pass ? "是" : "否",
    review.process || "",
    review.judgment || "",
    review.note || item.error || "",
  ]);
}
directSheet.getRangeByIndexes(0, 0, directRows.length, directHeader.length).values = directRows;

const findingRows = [
  ["优先级", "类别", "现场", "判断", "下一步"],
  ["P0", "环境阻断", "Chat API 创建会话时 MySQL 1130：当前公网 IP 不允许连接 aiia_system。", "完整前端/数据库恢复端到端尚未验证。", "恢复系统库访问后重跑同一组拟真案例。"],
  ["P1", "命名协议", "动量串联案例最终工具名为 ct_run。", "Coding 从自然语言 Design 中没有稳定取得工具标识，虽然代码可运行，但资产不可管理。", "检查自然语言 Design 到 Coding 的工具命名输入是否充分，不用字符串补丁修复。"],
  ["P1", "测试证据", "盈利质量案例技术通过，但 current_count/base_count/calculated_count 全为0。", "运行不报错成立，但用户无法凭空结果判断工具是否满足需求。", "测试阶段应主动选择有有效样本的报告期或典型公司，再向用户展示核心财务值。"],
  ["P1", "需求收敛", "盈利质量需求被扩展为全市场、固定2025/2024、排除金融和ST。", "明显超出原始需求，Requirement 阶段仍有发散。", "后续效果优化时收紧 Requirement Skill，只补完成核心功能所需的默认。"],
  ["P2", "交互长度", "动量 Requirement+Design+Flowchart 单轮约122.6秒，设计文档约2510字。", "功能通畅但首轮等待和信息密度偏高。", "前端按阶段流式展示目标、核心规则、流程图；细节默认折叠。"],
  ["P2", "过程可解释性", "动量、金叉、突破、估值的 key_process_info 均为6至10个核心事实。", "数量总体合适，基本能让用户理解结论来源。", "保持按业务选指标，不统一扩张固定字段。"],
  ["P2", "自动修复", "放量突破首次测试失败，Codex识别 skipped_count 边界并修复后通过。", "闭环有效，且反馈具体；应保留这种模块级修复。", "报告测试失败原因和修复结果，避免前端只显示最终通过。"],
];
findings.getRangeByIndexes(0, 0, findingRows.length, 5).values = findingRows;

function style(sheet, range, widths, numericColumns = []) {
  sheet.showGridLines = false;
  const body = sheet.getRange(range);
  body.format.wrapText = true;
  body.format.font = { name: "Aptos", size: 10, color: "#1F2937" };
  body.format.borders = {
    insideHorizontal: { style: "thin", color: "#E5E7EB" },
    bottom: { style: "thin", color: "#CBD5E1" },
  };
  const endColumn = range.split(":")[1].replace(/\d+$/, "");
  const header = sheet.getRange(`A1:${endColumn}1`);
  header.format.fill = "#173B57";
  header.format.font = { name: "Aptos Display", size: 10, bold: true, color: "#FFFFFF" };
  header.format.rowHeight = 26;
  for (const [column, width] of Object.entries(widths)) {
    sheet.getRange(`${column}:${column}`).format.columnWidth = width;
  }
  for (const column of numericColumns) {
    sheet.getRange(`${column}2:${column}${range.match(/\d+$/)[0]}`).format.numberFormat = "#,##0";
  }
  sheet.freezePanes.freezeRows(1);
}

style(overview, `A1:B${overviewRows.length}`, { A: 34, B: 110 }, ["B"]);
overview.getRange("A1:B1").format.fill = "#0F766E";
overview.getRange("A1:B1").format.font = { name: "Aptos Display", size: 13, bold: true, color: "#FFFFFF" };
style(e2eTurns, `A1:N${e2eRows.length}`, {
  A: 28, B: 8, C: 12, D: 48, E: 55, F: 85, G: 48, H: 16, I: 28, J: 28, K: 55, L: 52, M: 52, N: 55,
}, ["H"]);
style(directSheet, `A1:O${directRows.length}`, {
  A: 28, B: 65, C: 32, D: 12, E: 12, F: 16, G: 14, H: 14, I: 12, J: 12, K: 14, L: 12, M: 65, N: 32, O: 70,
}, ["F", "G", "H", "I", "J", "K"]);
style(findings, `A1:E${findingRows.length}`, { A: 10, B: 22, C: 72, D: 72, E: 72 });

directSheet.getRange(`D2:E${directRows.length}`).conditionalFormats.add("containsText", {
  text: "通过",
  format: { fill: "#DCFCE7", font: { color: "#166534", bold: true } },
});
findings.getRange(`A2:A${findingRows.length}`).conditionalFormats.add("containsText", {
  text: "P0",
  format: { fill: "#FEE2E2", font: { color: "#991B1B", bold: true } },
});
findings.getRange(`A2:A${findingRows.length}`).conditionalFormats.add("containsText", {
  text: "P1",
  format: { fill: "#FEF3C7", font: { color: "#92400E", bold: true } },
});

await fs.mkdir(path.dirname(output), { recursive: true });
const exported = await SpreadsheetFile.exportXlsx(wb);
await exported.save(output);

const inspections = [];
for (const [sheetName, range] of [
  ["评测概览", `A1:B${overviewRows.length}`],
  ["串联逐轮", `A1:N${e2eRows.length}`],
  ["直接Coding", `A1:O${directRows.length}`],
  ["发现与判断", `A1:E${findingRows.length}`],
]) {
  inspections.push((await wb.inspect({
    kind: "table",
    sheetId: sheetName,
    range,
    include: "values,formulas",
    tableMaxRows: 12,
    tableMaxCols: 15,
  })).ndjson);
  const preview = await wb.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(
    output.replace(/\.xlsx$/, `_${sheetName}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}
const errors = await wb.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(JSON.stringify({ output, inspections, formula_errors: errors.ndjson }));
