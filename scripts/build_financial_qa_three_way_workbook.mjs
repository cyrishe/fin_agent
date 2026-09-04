import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "/Users/imac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const analysisPath = process.argv[2];
const outputPath = process.argv[3];
if (!analysisPath || !outputPath) {
  throw new Error("usage: node build_financial_qa_three_way_workbook.mjs comparison_analysis.json output.xlsx");
}

const analysis = JSON.parse(await fs.readFile(analysisPath, "utf8"));
let firstPass = null;
if (analysis.dsh_opt_first_pass?.source) {
  firstPass = JSON.parse(await fs.readFile(analysis.dsh_opt_first_pass.source, "utf8"));
}

const runtimeDefs = [
  ["cc", "CC"],
  ["dsh_low", "DSH Low"],
  ["dsh_opt", "DSH Opt"],
];
const summary = analysis.summaries;
const workbook = Workbook.create();
const overview = workbook.worksheets.add("总览");
const runDetail = workbook.worksheets.add("运行明细");
const caseCompare = workbook.worksheets.add("逐题对比");
const apiDetail = workbook.worksheets.add("API执行明细");
const llmStages = workbook.worksheets.add("LLM阶段");
const answers = workbook.worksheets.add("答案摘录");
const optFailure = workbook.worksheets.add("Opt首跑异常");
const methodology = workbook.worksheets.add("配置与口径");

const C = {
  navy: "#17324D",
  teal: "#0F6B78",
  blue: "#D9EAF7",
  paleBlue: "#EEF6FB",
  paleGreen: "#E2F0D9",
  paleAmber: "#FFF2CC",
  paleRed: "#FCE4D6",
  gray: "#F2F2F2",
  text: "#243447",
  white: "#FFFFFF",
};

function safeText(value, limit = 30000) {
  if (value === null || value === undefined) return "";
  let text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (text.startsWith("=")) text = `'${text}`;
  return text.length > limit ? `${text.slice(0, limit)}\n…[已截断，仅影响 Excel 展示]` : text;
}

function yesNo(value) {
  return value ? "是" : "否";
}

function title(sheet, range, value) {
  sheet.getRange(range).merge();
  sheet.getRange(range.split(":")[0]).values = [[value]];
  sheet.getRange(range).format = {
    fill: C.navy,
    font: { bold: true, color: C.white, size: 16 },
    verticalAlignment: "center",
  };
  sheet.getRange(range).format.rowHeight = 32;
}

function header(range, fill = C.navy) {
  range.format = {
    fill,
    font: { bold: true, color: C.white },
    verticalAlignment: "center",
    wrapText: true,
  };
  range.format.rowHeight = 30;
}

function addTable(sheet, range, name) {
  try { sheet.tables.add(range, true, name); } catch (_) {}
}

function styleBody(sheet, range) {
  sheet.getRange(range).format = {
    font: { color: C.text },
    verticalAlignment: "top",
  };
}

// 运行明细：全部汇总公式的审计源。
const runHeaders = [
  "题号", "运行时", "状态", "问题", "分类", "Golden首入口", "可接受入口", "必需入口",
  "端到端秒", "Runtime秒", "Prompt tokens", "Completion tokens", "上报总tokens", "Cache read",
  "累计上下文", "Reasoning tokens", "可见Completion", "模型请求数", "工具调用数", "目录读取数",
  "数据查询数", "结果加载数", "首个数据API", "API集合", "首入口正确", "最终可接受", "覆盖全部必需入口",
  "零行查询数", "结果行数", "Max-token命中", "可观测请求", "提示注入请求", "提示注入率", "错误",
];
const runRows = [];
for (const item of analysis.cases) {
  for (const [key, label] of runtimeDefs) {
    const r = item.runs[key];
    runRows.push([
      item.case_id, label, r.status, item.question, item.category, item.primary_entry,
      item.acceptable_first_entries.join("\n"), item.required_entries.join("\n"),
      r.total_elapsed_ms / 1000, r.runtime_duration_ms / 1000,
      r.prompt_tokens, r.completion_tokens, r.total_tokens, r.cache_read_tokens,
      r.cumulative_context_tokens, r.reasoning_tokens, r.non_reasoning_completion_tokens,
      r.model_call_count, r.tool_call_count, r.catalog_read_count, r.finance_query_count, r.result_load_count,
      r.first_data_api, r.api_set.join("\n"), Number(r.first_entry_correct), Number(r.eventual_acceptable),
      Number(r.all_required_entries), r.zero_row_query_count, r.result_row_count, r.max_token_hit_count,
      r.prompt_observed_request_count, r.prompt_injected_request_count,
      r.prompt_injection_rate === null ? "" : r.prompt_injection_rate, safeText(r.error),
    ]);
  }
}
runDetail.getRangeByIndexes(0, 0, runRows.length + 1, runHeaders.length).values = [runHeaders, ...runRows];
header(runDetail.getRangeByIndexes(0, 0, 1, runHeaders.length));
addTable(runDetail, `A1:AH${runRows.length + 1}`, "RuntimeDetail");
runDetail.freezePanes.freezeRows(1);
runDetail.freezePanes.freezeColumns(3);
runDetail.showGridLines = false;
styleBody(runDetail, `A2:AH${runRows.length + 1}`);
runDetail.getRange(`D2:H${runRows.length + 1}`).format.wrapText = true;
runDetail.getRange(`W2:X${runRows.length + 1}`).format.wrapText = true;
runDetail.getRange(`I2:J${runRows.length + 1}`).format.numberFormat = "0.00";
runDetail.getRange(`AG2:AG${runRows.length + 1}`).format.numberFormat = "0.0%";
for (let i = 0; i < runRows.length; i += 1) {
  const fill = runRows[i][1] === "CC" ? C.paleBlue : runRows[i][1] === "DSH Low" ? C.gray : C.paleGreen;
  runDetail.getRangeByIndexes(i + 1, 0, 1, runHeaders.length).format.fill = fill;
}

// 总览：公式均回指运行明细；中位数/P95采用分析器保留的确定值。
title(overview, "A1:N1", "金融查数：CC / DSH Low / DSH Opt 随机20题对比");
overview.getRange("A2:N2").merge();
overview.getRange("A2").values = [["同一模型 deepseek-v4-flash；排除 news；固定随机种子 20260902。Opt 为“阶段策略编排”。CC usage 是整轮聚合，DSH 是逐请求累加；不同口径不可强行补齐。"]];
overview.getRange("A2:N2").format = { fill: C.blue, font: { color: C.navy }, wrapText: true, verticalAlignment: "center" };
overview.getRange("A4:F4").values = [["指标", "单位", "CC", "DSH Low", "DSH Opt", "Opt关键变化"]];
header(overview.getRange("A4:F4"));
const overviewRows = [
  ["完成率", "%", null, null, null, "三组均20/20"],
  ["首入口符合Golden", "%", null, null, null, "Opt 18/20"],
  ["最终包含可接受入口", "%", null, null, null, "Opt 20/20"],
  ["覆盖全部Required入口", "%", null, null, null, "Opt 19/20"],
  ["平均端到端耗时", "秒", null, null, null, "Opt vs CC -22.6%；vs Low -42.9%"],
  ["中位端到端耗时", "秒", summary.cc.metrics.total_elapsed_ms.median / 1000, summary.dsh_low.metrics.total_elapsed_ms.median / 1000, summary.dsh_opt.metrics.total_elapsed_ms.median / 1000, ""],
  ["P95端到端耗时", "秒", summary.cc.metrics.total_elapsed_ms.p95 / 1000, summary.dsh_low.metrics.total_elapsed_ms.p95 / 1000, summary.dsh_opt.metrics.total_elapsed_ms.p95 / 1000, ""],
  ["上报Prompt tokens", "20题合计", null, null, null, "Opt 高于两组"],
  ["上报Completion tokens", "20题合计", null, null, null, "Opt vs Low -45.1%"],
  ["上报总tokens", "20题合计", null, null, null, "Opt vs CC +22.5%；vs Low +14.9%"],
  ["累计上下文tokens", "DSH合计", "", null, null, "Opt vs Low -16.5%"],
  ["Reasoning tokens", "DSH合计", "", null, null, "Opt vs Low -64.2%"],
  ["模型请求数", "合计", "", null, null, "CC仅有整轮聚合usage，不可比"],
  ["工具调用数", "合计", null, null, null, "Opt vs CC -23.7%；vs Low -31.5%"],
  ["目录读取数", "合计", null, null, null, ""],
  ["数据查询数", "合计", null, null, null, ""],
  ["结果加载数", "合计", null, null, null, ""],
  ["Max-token命中", "请求数", "", null, null, "验证重跑均为0"],
  ["阶段提示注入", "请求数", "不适用", null, null, "Opt 81/81；Low策略关闭"],
];
overview.getRangeByIndexes(4, 0, overviewRows.length, 6).values = overviewRows;
const detailEnd = runRows.length + 1;
const runtimeColumns = ["CC", "DSH Low", "DSH Opt"];
for (let j = 0; j < runtimeColumns.length; j += 1) {
  const col = String.fromCharCode("C".charCodeAt(0) + j);
  const label = runtimeColumns[j];
  overview.getRange(`${col}5`).formulas = [[`=COUNTIFS('运行明细'!$B$2:$B$${detailEnd},"${label}",'运行明细'!$C$2:$C$${detailEnd},"ok")/COUNTIF('运行明细'!$B$2:$B$${detailEnd},"${label}")`]];
  overview.getRange(`${col}6`).formulas = [[`=SUMIFS('运行明细'!$Y$2:$Y$${detailEnd},'运行明细'!$B$2:$B$${detailEnd},"${label}")/COUNTIF('运行明细'!$B$2:$B$${detailEnd},"${label}")`]];
  overview.getRange(`${col}7`).formulas = [[`=SUMIFS('运行明细'!$Z$2:$Z$${detailEnd},'运行明细'!$B$2:$B$${detailEnd},"${label}")/COUNTIF('运行明细'!$B$2:$B$${detailEnd},"${label}")`]];
  overview.getRange(`${col}8`).formulas = [[`=SUMIFS('运行明细'!$AA$2:$AA$${detailEnd},'运行明细'!$B$2:$B$${detailEnd},"${label}")/COUNTIF('运行明细'!$B$2:$B$${detailEnd},"${label}")`]];
  overview.getRange(`${col}9`).formulas = [[`=AVERAGEIF('运行明细'!$B$2:$B$${detailEnd},"${label}",'运行明细'!$I$2:$I$${detailEnd})`]];
  const sums = [[12, "K"], [13, "L"], [14, "M"], [15, "O"], [16, "P"], [17, "R"], [18, "S"], [19, "T"], [20, "U"], [21, "V"], [22, "AD"], [23, "AF"]];
  for (const [row, sourceCol] of sums) {
    if (label === "CC" && [15, 16, 17, 22, 23].includes(row)) continue;
    overview.getRange(`${col}${row}`).formulas = [[`=SUMIF('运行明细'!$B$2:$B$${detailEnd},"${label}",'运行明细'!$${sourceCol}$2:$${sourceCol}$${detailEnd})`]];
  }
}
overview.getRange("C5:E8").format.numberFormat = "0.0%";
overview.getRange("C9:E11").format.numberFormat = "0.00";
overview.getRange("C12:E23").format.numberFormat = "#,##0";
overview.getRange("A5:F23").format = { verticalAlignment: "center", wrapText: true };
overview.getRange("A5:B23").format.fill = C.gray;
overview.getRange("C5:C23").format.fill = C.paleBlue;
overview.getRange("D5:D23").format.fill = C.gray;
overview.getRange("E5:E23").format.fill = C.paleGreen;
overview.getRange("F5:F23").format.fill = C.paleAmber;

overview.getRange("A26:D29").values = [
  ["耗时指标", "CC", "DSH Low", "DSH Opt"],
  ["平均耗时(秒)", null, null, null],
  ["中位耗时(秒)", null, null, null],
  ["P95耗时(秒)", null, null, null],
];
header(overview.getRange("A26:D26"), C.teal);
overview.getRange("B27:D29").formulas = [
  ["=C9", "=D9", "=E9"],
  ["=C10", "=D10", "=E10"],
  ["=C11", "=D11", "=E11"],
];
overview.getRange("B27:D29").format.numberFormat = "0.00";
const latencyChart = overview.charts.add("bar", overview.getRange("A26:D29"));
latencyChart.title = "Opt 在平均值与尾部耗时上均更快（秒）";
latencyChart.hasLegend = true;
latencyChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
latencyChart.yAxis = { numberFormatCode: "0", min: 0 };
latencyChart.setPosition("H4", "N16");

overview.getRange("A32:D35").values = [
  ["动作指标", "CC", "DSH Low", "DSH Opt"],
  ["工具调用", null, null, null],
  ["数据查询", null, null, null],
  ["结果加载", null, null, null],
];
header(overview.getRange("A32:D32"), C.teal);
overview.getRange("B33:D35").formulas = [
  ["=C18", "=D18", "=E18"],
  ["=C20", "=D20", "=E20"],
  ["=C21", "=D21", "=E21"],
];
const actionChart = overview.charts.add("bar", overview.getRange("A32:D35"));
actionChart.title = "Opt 通过减少无效动作收敛循环（20题合计）";
actionChart.hasLegend = true;
actionChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
actionChart.yAxis = { numberFormatCode: "0", min: 0 };
actionChart.setPosition("H18", "N30");

overview.getRange("A38:F43").values = [
  ["关键判断", "结论", "证据", "是否达成", "下一步", "优先级"],
  ["管理对象", "管理的是当前阶段的提示词资产、注入时机和版本；不是把工具拆成多套。", "Opt 81/81次请求识别到阶段提示；工具schema保持稳定。", "是", "把提示版本/灰度/评测显化为产品能力。", "P0"],
  ["Max-token", "命中主要是reasoning耗尽，不是真实长答案；验证重跑0命中。", "首跑RTE059两次1536输出均100% reasoning、0可见token。", "已定位", "先修阶段非进展与结果投影，不盲目扩大预算。", "P0"],
  ["速度与循环", "Opt显著快于Low，也快于CC。", "平均38.73s；19/20快于Low；14/20快于CC。", "是", "固定策略版本后扩大样本。", "P0"],
  ["Token目标", "当前上报总tokens没有下降。", "Opt 542,238；Low 472,114；CC 442,734。", "否", "优先压缩DSH结果投影并改善请求前缀缓存。", "P0"],
  ["执行效果", "Opt路由指标未下降，且本样本更高；仍需看逐题API而非只看总分。", "首入口18/20、最终可接受20/20、required 19/20。", "本轮通过", "对API不一致题做人工语义复核。", "P1"],
];
header(overview.getRange("A38:F38"));
overview.getRange("A39:F43").format = { wrapText: true, verticalAlignment: "top" };
overview.freezePanes.freezeRows(4);
overview.showGridLines = false;

// 逐题横向比较：直接回答“问题 -> 具体API是否一致”。
const caseHeaders = [
  "题号", "问题", "Golden首入口", "可接受入口", "必需入口",
  "CC状态", "Low状态", "Opt状态", "CC秒", "Low秒", "Opt秒",
  "CC总tokens", "Low总tokens", "Opt总tokens",
  "CC首API", "Low首API", "Opt首API", "CC API集合", "Low API集合", "Opt API集合",
  "CC=Low首API", "CC=Opt首API", "Low=Opt首API", "三方首API一致",
  "CC首入口正确", "Low首入口正确", "Opt首入口正确",
  "CC覆盖Required", "Low覆盖Required", "Opt覆盖Required",
  "CC工具数", "Low工具数", "Opt工具数", "CC错误", "Low错误", "Opt错误",
];
const caseRows = analysis.cases.map((item) => {
  const cc = item.runs.cc;
  const low = item.runs.dsh_low;
  const opt = item.runs.dsh_opt;
  return [
    item.case_id, item.question, item.primary_entry, item.acceptable_first_entries.join("\n"), item.required_entries.join("\n"),
    cc.status, low.status, opt.status, cc.total_elapsed_ms / 1000, low.total_elapsed_ms / 1000, opt.total_elapsed_ms / 1000,
    cc.total_tokens, low.total_tokens, opt.total_tokens,
    cc.first_data_api, low.first_data_api, opt.first_data_api,
    cc.api_set.join("\n"), low.api_set.join("\n"), opt.api_set.join("\n"),
    yesNo(cc.first_data_api === low.first_data_api), yesNo(cc.first_data_api === opt.first_data_api),
    yesNo(low.first_data_api === opt.first_data_api), yesNo(item.all_three_same_first_data_api),
    yesNo(cc.first_entry_correct), yesNo(low.first_entry_correct), yesNo(opt.first_entry_correct),
    yesNo(cc.all_required_entries), yesNo(low.all_required_entries), yesNo(opt.all_required_entries),
    cc.tool_call_count, low.tool_call_count, opt.tool_call_count, safeText(cc.error), safeText(low.error), safeText(opt.error),
  ];
});
caseCompare.getRangeByIndexes(0, 0, caseRows.length + 1, caseHeaders.length).values = [caseHeaders, ...caseRows];
header(caseCompare.getRangeByIndexes(0, 0, 1, caseHeaders.length));
addTable(caseCompare, `A1:AJ${caseRows.length + 1}`, "CaseComparison");
caseCompare.freezePanes.freezeRows(1);
caseCompare.freezePanes.freezeColumns(5);
caseCompare.showGridLines = false;
styleBody(caseCompare, `A2:AJ${caseRows.length + 1}`);
caseCompare.getRange(`B2:E${caseRows.length + 1}`).format.wrapText = true;
caseCompare.getRange(`O2:T${caseRows.length + 1}`).format.wrapText = true;
caseCompare.getRange(`I2:K${caseRows.length + 1}`).format.numberFormat = "0.00";
caseCompare.getRange(`I2:I${caseRows.length + 1}`).format.fill = C.paleBlue;
caseCompare.getRange(`J2:J${caseRows.length + 1}`).format.fill = C.gray;
caseCompare.getRange(`K2:K${caseRows.length + 1}`).format.fill = C.paleGreen;

// API执行明细：每题每运行时保留完整目录/API顺序及请求DSL。
const apiHeaders = [
  "题号", "运行时", "问题", "Golden首入口", "目录读取序列(subject.dataview:operation)", "首目录入口",
  "首个数据API", "数据API顺序", "API集合", "Query DSL", "目录数", "Query数", "Load数",
  "零行Query数", "Result refs", "结果总行数", "首入口正确", "最终可接受", "覆盖Required",
];
const apiRows = [];
for (const item of analysis.cases) {
  for (const [key, label] of runtimeDefs) {
    const r = item.runs[key];
    apiRows.push([
      item.case_id, label, item.question, item.primary_entry, r.catalog_sequence.join("\n"), r.first_catalog_entry,
      r.first_data_api, r.api_sequence.join("\n"), r.api_set.join("\n"), safeText(r.query_requests.join("\n\n")),
      r.catalog_read_count, r.finance_query_count, r.result_load_count, r.zero_row_query_count, r.result_ref_count,
      r.result_row_count, yesNo(r.first_entry_correct), yesNo(r.eventual_acceptable), yesNo(r.all_required_entries),
    ]);
  }
}
apiDetail.getRangeByIndexes(0, 0, apiRows.length + 1, apiHeaders.length).values = [apiHeaders, ...apiRows];
header(apiDetail.getRangeByIndexes(0, 0, 1, apiHeaders.length));
addTable(apiDetail, `A1:S${apiRows.length + 1}`, "ApiExecutionDetail");
apiDetail.freezePanes.freezeRows(1);
apiDetail.freezePanes.freezeColumns(4);
apiDetail.showGridLines = false;
styleBody(apiDetail, `A2:S${apiRows.length + 1}`);
apiDetail.getRange(`C2:J${apiRows.length + 1}`).format.wrapText = true;

// DSH逐请求上下文、推理、提示注入及工具调用。
const llmHeaders = [
  "题号", "运行时", "请求序号", "阶段", "阶段原因", "Reasoning effort", "Max tokens", "Context tokens",
  "Output tokens", "Reasoning tokens", "可见Output", "Reasoning占比", "命中上限", "阶段提示已注入",
  "注入Surface", "提示SHA256", "提示字符数", "调用工具", "阶段是否由调用回推",
];
const llmRows = [];
for (const item of analysis.cases) {
  for (const [key, label] of runtimeDefs.slice(1)) {
    for (const step of item.runs[key].stage_trace) {
      llmRows.push([
        item.case_id, label, step.request_index, step.stage, safeText(step.stage_reason), step.reasoning_effort,
        step.max_tokens, step.context_tokens, step.output_tokens, step.reasoning_tokens, step.visible_output_tokens,
        step.output_tokens ? step.reasoning_tokens / step.output_tokens : "", yesNo(step.max_token_hit),
        yesNo(step.prompt_injected), step.prompt_surface, step.prompt_sha256, step.prompt_chars,
        (step.called_tools || []).join("\n"), yesNo(step.stage_inferred_from_calls),
      ]);
    }
  }
}
llmStages.getRangeByIndexes(0, 0, llmRows.length + 1, llmHeaders.length).values = [llmHeaders, ...llmRows];
header(llmStages.getRangeByIndexes(0, 0, 1, llmHeaders.length));
addTable(llmStages, `A1:S${llmRows.length + 1}`, "LlmStageTrace");
llmStages.freezePanes.freezeRows(1);
llmStages.freezePanes.freezeColumns(4);
llmStages.showGridLines = false;
styleBody(llmStages, `A2:S${llmRows.length + 1}`);
llmStages.getRange(`L2:L${llmRows.length + 1}`).format.numberFormat = "0.0%";
llmStages.getRange(`E2:E${llmRows.length + 1}`).format.wrapText = true;
llmStages.getRange(`R2:R${llmRows.length + 1}`).format.wrapText = true;

const answerHeaders = ["题号", "问题", "CC回答", "DSH Low回答", "DSH Opt回答"];
const answerRows = analysis.cases.map((item) => [
  item.case_id, item.question, safeText(item.runs.cc.answer, 15000), safeText(item.runs.dsh_low.answer, 15000),
  safeText(item.runs.dsh_opt.answer, 15000),
]);
answers.getRangeByIndexes(0, 0, answerRows.length + 1, answerHeaders.length).values = [answerHeaders, ...answerRows];
header(answers.getRangeByIndexes(0, 0, 1, answerHeaders.length));
addTable(answers, `A1:E${answerRows.length + 1}`, "AnswerExcerpts");
answers.freezePanes.freezeRows(1);
answers.freezePanes.freezeColumns(2);
answers.showGridLines = false;
styleBody(answers, `A2:E${answerRows.length + 1}`);
answers.getRange(`B2:E${answerRows.length + 1}`).format.wrapText = true;

// 首跑异常保留为稳定性证据，不被验证重跑覆盖。
title(optFailure, "A1:J1", "DSH Opt 首跑异常：RTE059");
const failedCase = firstPass?.cases?.find((item) => item.case_id === "RTE059");
const fq = failedCase?.financial_qa || {};
optFailure.getRange("A3:J7").values = [
  ["题号", "问题", "首跑状态", "耗时秒", "错误", "错误Operation", "验证重跑", "根因判断", "是否长答案", "处置"],
  [
    failedCase?.case_id || "RTE059", failedCase?.question || "", failedCase?.status || "error",
    failedCase ? failedCase.total_elapsed_ms / 1000 : "", safeText(failedCase?.error?.message || failedCase?.error),
    "stock.financial_3_table:compute（该dataview仅支持query）", "通过（20/20）",
    "operation误选后没有进展，catalog阶段reasoning耗尽", "否：两次上限命中均0可见token",
    "保留失败证据；不启用maxTokensAsSuccess，不盲目扩容",
  ],
  ["配置", "catalog low/1536；query low/3072；details/final off/2048", "", "", "", "", "", "", "", ""],
  ["提示注入观测", "首跑使用旧观测解析器，trace未识别message注入；验证重跑已证实81/81", "", "", "", "", "", "", "", ""],
  ["解释", "max_tokens是单次completion上限，含reasoning；不是上下文窗口，也不等于最终答案长度。", "", "", "", "", "", "", "", ""],
];
header(optFailure.getRange("A3:J3"));
optFailure.getRange("A4:J7").format = { wrapText: true, verticalAlignment: "top" };
const failSteps = fq.loop_policy?.requests || [];
optFailure.getRange("A10:J10").values = [["请求序号", "阶段", "Reasoning effort", "Max tokens", "Context", "Output", "Reasoning", "可见Output", "命中上限", "调用工具"]];
header(optFailure.getRange("A10:J10"), C.teal);
const failRows = failSteps.map((s) => [
  s.request_index, s.stage, s.reasoning_effort, s.max_tokens, s.context_tokens, s.output_tokens,
  s.reasoning_tokens, s.visible_output_tokens, yesNo(s.max_token_hit), (s.called_tools || []).join("\n"),
]);
if (failRows.length) optFailure.getRangeByIndexes(10, 0, failRows.length, 10).values = failRows;
optFailure.freezePanes.freezeRows(3);
optFailure.showGridLines = false;

title(methodology, "A1:F1", "配置、Prompt资产与统计口径");
methodology.getRange("A3:F3").values = [["项目", "值", "性质", "作用域", "复现/解释", "注意事项"]];
header(methodology.getRange("A3:F3"));
const manifest = analysis.manifest;
const configRows = [
  ["分析版本", analysis.analysis, "HARD旁路证据", "本工作簿", analysis.generated_at, "不进入业务回答"],
  ["题源", manifest.source_case_path, "测试资产", "三组", `${manifest.source_candidate_count}题候选`, "排除stock.news"],
  ["随机种子/样本", `${manifest.seed} / ${manifest.sample_size}`, "测试资产", "三组", manifest.case_ids.join(", "), "固定可复现"],
  ["模型", "deepseek-v4-flash", "运行配置", "三组", "同一模型", "减少模型差异"],
  ["并发/超时", `${manifest.concurrency} / ${manifest.per_case_timeout_seconds}s`, "运行配置", "三组", "真实Chat/SSE", "外部Web/MCP关闭"],
  ["CC", manifest.configurations.cc, "运行配置", "CC", "max_turns + effort", "Fin Agent未显式传max_tokens"],
  ["DSH Low", manifest.configurations.dsh_low, "运行配置", "DSH", "loop policy disabled", "基线"],
  ["DSH Opt", manifest.configurations.dsh_opt, "运行配置", "DSH", "阶段策略编排", "旧JSON字段仍兼容"],
  ["全局Prompt", manifest.prompt_assets.global_system.path, "SOFT资产", "DSH会话", manifest.prompt_assets.global_system.sha256, "保留人工修改"],
  ["阶段Prompt", manifest.prompt_assets.stage_policy.path, "SOFT资产", "DSH每次pre-step", manifest.prompt_assets.stage_policy.sha256, "按阶段精准注入"],
  ["工具Catalog", "共用catalog服务的operation级结果", "HARD事实", "CC + DSH", "subject + dataview + operation", "字段/协议/例子只在选中后披露"],
  ["结果存储", "result_ref + 摘要", "HARD事实", "CC + DSH", "原始row-dict不直接塞回上下文", "需要明细时分页加载"],
  ["CC token口径", "整轮聚合usage", "观测", "CC", "prompt/completion/total", "无逐请求reasoning/cache上下文"],
  ["DSH token口径", "逐请求usage累加", "观测", "DSH", "含cache/context/reasoning", "与CC不可强行补齐"],
  ["max_tokens", "每次模型请求completion上限（含reasoning）", "请求预算", "DSH", "不是总上下文上限", "命中不等于答案过长"],
  ["Opt首跑", "19/20；RTE059 max-tokens", "稳定性证据", "DSH Opt", "原始结果完整保留", "验证重跑20/20不覆盖首跑事实"],
  ["API一致性", "首API / API集合 / API顺序", "效果证据", "三组", "详见逐题对比", "完全一致不是唯一正确性标准"],
];
methodology.getRangeByIndexes(3, 0, configRows.length, 6).values = configRows;
methodology.getRange("A4:F20").format = { wrapText: true, verticalAlignment: "top" };
methodology.getRange("A23:D23").values = [["两组比较", "首API完全一致", "API集合完全一致", "API顺序完全一致"]];
header(methodology.getRange("A23:D23"), C.teal);
const pairRows = [
  ["CC vs DSH Low", analysis.pairwise.cc_vs_dsh_low.same_first_data_api_count, analysis.pairwise.cc_vs_dsh_low.same_api_set_count, analysis.pairwise.cc_vs_dsh_low.same_api_sequence_count],
  ["CC vs DSH Opt", analysis.pairwise.cc_vs_dsh_opt.same_first_data_api_count, analysis.pairwise.cc_vs_dsh_opt.same_api_set_count, analysis.pairwise.cc_vs_dsh_opt.same_api_sequence_count],
  ["DSH Low vs DSH Opt", analysis.pairwise.dsh_low_vs_dsh_opt.same_first_data_api_count, analysis.pairwise.dsh_low_vs_dsh_opt.same_api_set_count, analysis.pairwise.dsh_low_vs_dsh_opt.same_api_sequence_count],
  ["三组同时一致", analysis.all_three.same_first_data_api_count, analysis.all_three.same_api_set_count, "—"],
];
methodology.getRange("A24:D27").values = pairRows;
methodology.showGridLines = false;
methodology.freezePanes.freezeRows(3);

// 全局版式。
overview.getRange("A:A").format.columnWidth = 27;
overview.getRange("B:B").format.columnWidth = 13;
overview.getRange("C:E").format.columnWidth = 15;
overview.getRange("F:F").format.columnWidth = 34;
overview.getRange("A39:F43").format.rowHeight = 92;
runDetail.getRange("A:A").format.columnWidth = 10;
runDetail.getRange("B:C").format.columnWidth = 12;
runDetail.getRange("D:D").format.columnWidth = 46;
runDetail.getRange("E:H").format.columnWidth = 22;
runDetail.getRange("W:X").format.columnWidth = 26;
runDetail.getRange("AH:AH").format.columnWidth = 38;
caseCompare.getRange("A:A").format.columnWidth = 10;
caseCompare.getRange("B:B").format.columnWidth = 46;
caseCompare.getRange("C:E").format.columnWidth = 24;
caseCompare.getRange("O:T").format.columnWidth = 26;
caseCompare.getRange("AH:AJ").format.columnWidth = 32;
apiDetail.getRange("A:B").format.columnWidth = 12;
apiDetail.getRange("C:C").format.columnWidth = 44;
apiDetail.getRange("D:I").format.columnWidth = 26;
apiDetail.getRange("J:J").format.columnWidth = 76;
llmStages.getRange("A:D").format.columnWidth = 14;
llmStages.getRange("E:E").format.columnWidth = 30;
llmStages.getRange("F:O").format.columnWidth = 17;
llmStages.getRange("P:P").format.columnWidth = 34;
llmStages.getRange("P2:P169").format.wrapText = true;
llmStages.getRange("Q:Q").format.columnWidth = 17;
llmStages.getRange("R:R").format.columnWidth = 28;
answers.getRange("A:A").format.columnWidth = 10;
answers.getRange("B:B").format.columnWidth = 46;
answers.getRange("C:E").format.columnWidth = 72;
answers.getRange(`2:${answerRows.length + 1}`).format.rowHeight = 108;
optFailure.getRange("A:A").format.columnWidth = 14;
optFailure.getRange("B:B").format.columnWidth = 46;
optFailure.getRange("C:J").format.columnWidth = 20;
optFailure.getRange("4:7").format.rowHeight = 64;
methodology.getRange("A:A").format.columnWidth = 22;
methodology.getRange("B:B").format.columnWidth = 54;
methodology.getRange("C:F").format.columnWidth = 28;
methodology.getRange("4:20").format.rowHeight = 48;

for (const sheet of [overview, runDetail, caseCompare, apiDetail, llmStages, answers, optFailure, methodology]) {
  const used = sheet.getUsedRange();
  used.format.autofitRows();
}
answers.getRange(`2:${answerRows.length + 1}`).format.rowHeight = 108;
apiDetail.getRange(`2:${apiRows.length + 1}`).format.rowHeight = 68;
caseCompare.getRange(`2:${caseRows.length + 1}`).format.rowHeight = 52;

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

const previewDir = `${outputPath}.previews`;
await fs.mkdir(previewDir, { recursive: true });
const previews = {
  "总览": "A1:N43",
  "运行明细": "A1:AH13",
  "逐题对比": "A1:AJ12",
  "API执行明细": "A1:S12",
  "LLM阶段": "A1:S18",
  "答案摘录": "A1:E8",
  "Opt首跑异常": `A1:J${Math.max(16, 10 + failRows.length)}`,
  "配置与口径": "A1:F27",
};
for (const [sheetName, range] of Object.entries(previews)) {
  const image = await workbook.render({ sheetName, range, autoCrop: "all", scale: 0.72, format: "png" });
  await fs.writeFile(`${previewDir}/${sheetName}.png`, new Uint8Array(await image.arrayBuffer()));
}

const workbookInspect = await workbook.inspect({
  kind: "workbook,sheet,table,drawing",
  maxChars: 12000,
  tableMaxRows: 4,
  tableMaxCols: 8,
  tableMaxCellChars: 100,
});
const summaryInspect = await workbook.inspect({ kind: "region", sheetId: "总览", range: "A1:N43", maxChars: 12000 });
const formulaInspect = await workbook.inspect({ kind: "formula", sheetId: "总览", range: "A1:N43", maxChars: 12000, options: { maxResults: 200 } });
const auditText = [workbookInspect.ndjson, summaryInspect.ndjson, formulaInspect.ndjson].filter(Boolean).join("\n");
await fs.writeFile(`${outputPath}.inspection.ndjson`, auditText, "utf8");
const formulaErrors = (auditText.match(/#(?:REF!|DIV\/0!|VALUE!|NAME\?|N\/A)/g) || []);
if (formulaErrors.length) throw new Error(`formula audit failed: ${formulaErrors.join(", ")}`);

console.log(JSON.stringify({
  outputPath,
  sheetCount: 8,
  runRows: runRows.length,
  caseRows: caseRows.length,
  apiRows: apiRows.length,
  llmRows: llmRows.length,
  previewDir,
  formulaErrorCount: formulaErrors.length,
}, null, 2));
