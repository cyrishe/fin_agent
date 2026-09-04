import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "/Users/imac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const inputPath = process.argv[2];
const outputPath = process.argv[3];
if (!inputPath || !outputPath) throw new Error("usage: node build... analysis.json output.xlsx");
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const summary = workbook.worksheets.add("汇总");
const cases = workbook.worksheets.add("逐题结果");
const calls = workbook.worksheets.add("API调用明细");
const turns = workbook.worksheets.add("Turn耗时");
const catalogReview = workbook.worksheets.add("目录审视");
const definitions = workbook.worksheets.add("口径说明");

const navy = "#17324D";
const blue = "#D9EAF7";
const green = "#E2F0D9";
const amber = "#FFF2CC";
const red = "#FCE4D6";
const gray = "#F2F2F2";

function title(sheet, range, value) {
  sheet.getRange(range).merge();
  sheet.getRange(range.split(":")[0]).values = [[value]];
  sheet.getRange(range).format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 }, verticalAlignment: "center" };
  sheet.getRange(range).format.rowHeight = 30;
}

function header(range) {
  range.format = { fill: navy, font: { bold: true, color: "#FFFFFF" }, verticalAlignment: "center", wrapText: true };
  range.format.rowHeight = 28;
}

function addTable(sheet, range, name) {
  try { sheet.tables.add(range, true, name); } catch (_) {}
}

title(summary, "A1:H1", "研报查询 CC 回归（low / max turns 12）");
summary.getRange("A2:H2").merge();
summary.getRange("A2").values = [["抽样运行因发现问题提前停止；结果重点衡量 API 选择、协议/静态校验、修正重试与耗时。数据为空不计为 API 选择错误。"]];
summary.getRange("A2:H2").format = { fill: blue, font: { color: "#17324D" }, wrapText: true };
summary.getRange("A4:B14").values = [
  ["核心指标", "结果"],
  ["已完成样本数", null],
  ["API选择正确", null],
  ["API选择错误", null],
  ["未选择研报API", null],
  ["出现静态失败的题", null],
  ["静态失败后修正成功", null],
  ["出现provider/API失败的题", null],
  ["存在空数据的题", null],
  ["未生成最终回答", null],
  ["端到端中位耗时（秒）", null],
];
header(summary.getRange("A4:B4"));
summary.getRange("B5:B14").formulas = [
  ["=COUNTA('逐题结果'!A2:A500)"],
  ["=COUNTIF('逐题结果'!F2:F500,\"正确\")"],
  ["=COUNTIF('逐题结果'!F2:F500,\"错误\")"],
  ["=COUNTIF('逐题结果'!F2:F500,\"未选择研报API\")"],
  ["=COUNTIF('逐题结果'!J2:J500,\">0\")"],
  ["=COUNTIF('逐题结果'!L2:L500,\"是\")"],
  ["=COUNTIF('逐题结果'!M2:M500,\">0\")"],
  ["=COUNTIF('逐题结果'!N2:N500,\">0\")"],
  ["=COUNTIF('逐题结果'!X2:X500,\"否\")"],
  ["=MEDIAN('逐题结果'!W2:W500)/1000"],
];
summary.getRange("D4:H10").values = [
  ["关键结论", "现象", "影响", "建议", "证据入口"],
  ["静态修正链路有效", "8题首次静态失败，8题均后续修正通过", "说明机械契约错误可被反馈闭环纠正", "保留现有静态检查与恢复提示", "API调用明细"],
  ["12 turns 对复杂题偏紧", "至少1题工具执行后未形成最终答复", "复杂估值/组合题可能在最后合成前耗尽预算", "直查维持12；复杂Skill单独提高预算或约束调用数", "逐题结果"],
  ["研报入口选择不稳定", "若干观点/风险/布局题未调用 stock.report", "可能在无证据或替代数据上完成回答", "加强目录路由提示，不增加业务validator", "逐题结果"],
  ["字段协议仍有摩擦", "is not null、别名字段、mode放入filter等触发静态失败", "增加turn与延迟，但静态层已阻止错误执行", "优先统一目录示例和字段命名", "API调用明细"],
  ["API执行是主要可测外部耗时", "静态检查通常毫秒级，API常为秒级", "性能优化应优先看provider和多次调用", "按API分布进一步聚合P50/P95", "Turn耗时"],
  ["空数据与协议正确需分离", "部分正确请求返回0行", "不能把数据缺失误判为选错API", "报告中独立保留empty_result_count", "逐题结果"],
];
header(summary.getRange("D4:H4"));
summary.getRange("D5:H10").format = { wrapText: true, verticalAlignment: "top" };
summary.getRange("A16:H20").values = [
  ["配置与时间口径", "值", "说明", null, null, null, null, null],
  ["CC effort", "low", "金融问答专用配置", null, null, null, null, null],
  ["最大 turns", 12, "SDK agentic turn 上限", null, null, null, null, null],
  ["CC思考耗时", "估算", "SDK不暴露隐藏推理span；用端到端减冷启动与可观察工具wall time估算", null, null, null, null, null],
  ["API/静态耗时", "精确埋点", "本次在运行时分别记录 static_validation_ms 与 api_execution_ms", null, null, null, null, null],
];
header(summary.getRange("A16:H16"));
summary.getRange("A17:H20").format = { wrapText: true };

const caseHeaders = ["题号","问题","预期数据族","预期模式","实际API","API选择判定","首次DSL","最终DSL","finance_query次数","静态失败次数","发生静态重试","重试修正成功","provider失败次数","空结果次数","Assistant消息数","ToolResult消息数","总工具调用数","冷启动ms","CC非工具耗时估算ms","静态校验ms","API执行ms","可观察工具wall ms","端到端ms","已生成最终回答","错误","最终回答"];
const yn = value => value ? "是" : "否";
const caseRows = data.cases.map(r => [r.case_id,r.question,r.expected_family,r.expected_mode,r.actual_apis,r.api_selection,r.first_request,r.final_request,r.finance_query_count,r.static_fail_count,yn(r.static_retry),yn(r.static_retry_corrected),r.provider_fail_count,r.empty_result_count,r.assistant_message_count,r.tool_result_message_count,r.tool_call_count,r.cold_start_ms,r.cc_non_tool_ms_est,r.static_validation_ms,r.api_execution_ms,r.observed_tool_ms,r.total_duration_ms,yn(r.execution_completed),r.error,r.answer]);
cases.getRangeByIndexes(0,0,caseRows.length+1,caseHeaders.length).values = [caseHeaders,...caseRows];
header(cases.getRangeByIndexes(0,0,1,caseHeaders.length));
addTable(cases, `A1:Z${caseRows.length+1}`, "CaseResults");
cases.freezePanes.freezeRows(1); cases.freezePanes.freezeColumns(2); cases.showGridLines = false;
cases.getRange(`A2:Z${caseRows.length+1}`).format = { verticalAlignment: "top" };
cases.getRange(`B2:H${caseRows.length+1}`).format.wrapText = true;
cases.getRange(`Y2:Z${caseRows.length+1}`).format.wrapText = true;
cases.getRange(`R2:W${caseRows.length+1}`).format.numberFormat = "0.0";

const callHeaders = ["题号","问题","尝试序号","Flow步骤","目标","API","提交DSL","规范化DSL","静态状态","静态错误","静态校验ms","API执行ms","provider重试次数","执行错误","行数","结果名"];
const callRows = data.calls.map(r => [r.case_id,r.question,r.attempt,r.flow_step,r.goal,r.api,r.submitted_request,r.normalized_request,r.static_status,r.static_errors,r.static_validation_ms,r.api_execution_ms,r.provider_retry_count,r.execution_error,r.row_count,r.result_name]);
calls.getRangeByIndexes(0,0,callRows.length+1,callHeaders.length).values = [callHeaders,...callRows];
header(calls.getRangeByIndexes(0,0,1,callHeaders.length));
addTable(calls, `A1:P${callRows.length+1}`, "ApiCalls");
calls.freezePanes.freezeRows(1); calls.freezePanes.freezeColumns(2); calls.showGridLines = false;
calls.getRange(`B2:J${callRows.length+1}`).format.wrapText = true;
calls.getRange(`K2:L${callRows.length+1}`).format.numberFormat = "0.000";

const turnHeaders = ["题号","问题","段序号","下一动作/工具","动作前CC耗时估算ms","工具开始ms","工具结束ms","工具wall ms"];
const turnRows = data.turns.map(r => [r.case_id,r.question,r.segment,r.tool,r.cc_before_tool_ms_est,r.tool_start_ms,r.tool_end_ms,r.tool_wall_ms]);
turns.getRangeByIndexes(0,0,turnRows.length+1,turnHeaders.length).values = [turnHeaders,...turnRows];
header(turns.getRangeByIndexes(0,0,1,turnHeaders.length));
addTable(turns, `A1:H${turnRows.length+1}`, "TurnTiming");
turns.freezePanes.freezeRows(1); turns.freezePanes.freezeColumns(2); turns.showGridLines = false;
turns.getRange(`B2:B${turnRows.length+1}`).format.wrapText = true;

definitions.getRange("A1:D1").values = [["字段","定义","判定边界","注意事项"]];
header(definitions.getRange("A1:D1"));
definitions.getRange("A2:D12").values = [
  ["API选择判定","按问题所需研报事实族与实际 stock.report* 调用比较","只看入口/族，不判断用户语义是否完全一致","自动初筛，复杂混合题需人工复核"],
  ["正确","选择到对应 report 或 report_metric 数据族","聚合题允许原始查询后由模型分析","不代表数据一定存在"],
  ["错误","选择了研报API但数据族明显不对应","例如预测指标使用 stock.report 而非 report_metric","需看DSL明细"],
  ["未选择研报API","该题未调用四类研报接口","可能是漏选，也可能采用了可接受替代数据","不能一律视为最终答案错误"],
  ["静态失败","DSL可解析后，目录字段/参数/输出等机械契约未通过","不检查是否符合用户语义","失败请求不会执行provider"],
  ["重试修正成功","静态失败后，CC再次调用且有后续静态通过请求","衡量loop恢复能力","不代表最终业务结论正确"],
  ["API执行ms","execute_api_call 的精确wall time","不含CC生成DSL时间","provider重试只保留最终一次埋点，重试次数另列"],
  ["CC非工具耗时估算","总耗时减冷启动和可观察工具wall time","含模型推理、生成、消息编排等","不是隐藏CoT精确耗时"],
  ["Assistant消息数","SDK AssistantMessage计数","近似agentic turns的观测值","与max_turns内部计费语义不保证完全一一对应"],
  ["空结果","查询协议与执行成功但row_count为0","独立于API选择正确性","本次数据覆盖不完整属预期"],
  ["样本范围","从199题中并行抽样完成95题后停止","覆盖RPT001-042、051-068、101-118、151-167","不是全量通过率"],
];
definitions.getRange("A2:D12").format = { wrapText: true, verticalAlignment: "top" };
definitions.freezePanes.freezeRows(1); definitions.showGridLines = false;

title(catalogReview, "A1:F1", "研报 Catalog 审视与精简建议");
catalogReview.getRange("A2:F2").merge();
catalogReview.getRange("A2").values = [["本次 case 在完整金融目录、全部金融查询工具有效的真实路由条件下运行；未预设 report/report_metric，也未屏蔽行情、财务、新闻等替代入口。"]];
catalogReview.getRange("A2:F2").format = { fill: blue, wrapText: true };
catalogReview.getRange("A4:F4").values = [["层级","现状","主要问题","证据/表现","建议","优先级"]];
header(catalogReview.getRange("A4:F4"));
catalogReview.getRange("A5:F11").values = [
  ["路由索引","只暴露 dataview 名称：report、report_metric 等","名称不足以让模型稳定区分观点研报与预测指标","部分观点/风险/布局题未进入 report；个别预测指标误进 report","索引改为极短语义标签：report=观点/评级/目标价/风险；report_metric=预测指标/年度/数值", "P0"],
  ["入口模型","四类API能力完整，但选择规则分散在desc、rules和API class","模型需要拼接多处信息后才能形成2×2选择","直查总体较稳，复杂题和冷门指标更易漏选","在dataview顶部用两行明确：明细/聚合 × 研报/指标；不增加新API", "P0"],
  ["Filter语法","字段可筛选，但支持的表达式边界没有在研报示例中集中呈现","模型自然生成 is not null，静态层拒绝","3个目标价case出现同类静态失败后重试","增加一条公共语法提示和一个目标价可用示例；不要为null语义新增业务validator", "P0"],
  ["典型案例","已有明细、聚合、公司扫描、预测指标示例","缺少高频端到端案例矩阵","最新评级、目标价区间、多年度预测、观点风险、公司扫描覆盖不均","保留6个短例：最新研报、目标价聚合、评级分布、单机构指标、多机构年度聚合、按研报筛公司", "P1"],
  ["说明长度","dataview rules 与 API class rules 部分重复","加载一次目录时token偏多，关键选择信息不够靠前","low推理下更容易抓住局部字段而忽略入口边界","dataview保留粒度/入口/时间；API class保留参数/输出/示例；公共限制上移", "P1"],
  ["指标域","8个标准metric_code清晰且可静态约束","评测中毛利率、研发投入、股息等确实不在域内","模型可能转向report或直接放弃查询","目录明确“域外指标→report内容证据或不支持”，不要静态猜测映射", "P1"],
  ["真实测试","完整目录竞争、真实provider、数据可为空","自动API判定对混合分析题可能过严","未选report不一定等于错误，可能使用合理替代数据","保持全工具测试为主；另加受控研报协议测试定位DSL能力，两类结果分开", "P0"],
];
catalogReview.getRange("A5:F11").format = { wrapText: true, verticalAlignment: "top" };
catalogReview.getRange("A13:F13").values = [["建议的精简入口文案","内容",null,null,null,null]];
header(catalogReview.getRange("A13:F13"));
catalogReview.getRange("A14:F17").values = [
  ["report","一行一篇研报：标题、机构、分析师、评级/变动、投资要点、风险、目标价与来源。",null,null,null,null],
  ["report.agg","对研报行做数量、评级分布、目标价统计或按公司扫描；一次一个聚合目标。",null,null,null,null],
  ["report_metric","一行一个研报指标事实：公司、机构、发布日期、指标编码、年份、预测/实际值、单位与来源。",null,null,null,null],
  ["report_metric.agg","对单一指标事实做均值/中位数/区间/数量或按公司扫描；需要观点结论时返回原始数据交给后续分析。",null,null,null,null],
];
catalogReview.getRange("A14:F17").format = { wrapText: true };
catalogReview.freezePanes.freezeRows(4); catalogReview.showGridLines = false;

for (const sheet of [summary,cases,calls,turns,catalogReview,definitions]) {
  const used = sheet.getUsedRange();
  used.format.autofitRows();
}
summary.getRange("A:A").format.columnWidth = 24; summary.getRange("B:B").format.columnWidth = 16;
summary.getRange("D:D").format.columnWidth = 22; summary.getRange("E:G").format.columnWidth = 28; summary.getRange("H:H").format.columnWidth = 16;
cases.getRange("A:A").format.columnWidth = 10; cases.getRange("B:B").format.columnWidth = 42; cases.getRange("C:F").format.columnWidth = 18; cases.getRange("G:H").format.columnWidth = 58; cases.getRange("Y:Z").format.columnWidth = 42;
calls.getRange("A:A").format.columnWidth = 10; calls.getRange("B:B").format.columnWidth = 36; calls.getRange("E:E").format.columnWidth = 30; calls.getRange("F:F").format.columnWidth = 24; calls.getRange("G:H").format.columnWidth = 58; calls.getRange("J:J").format.columnWidth = 55; calls.getRange("N:N").format.columnWidth = 40;
turns.getRange("A:A").format.columnWidth = 10; turns.getRange("B:B").format.columnWidth = 44; turns.getRange("D:D").format.columnWidth = 24; turns.getRange("E:H").format.columnWidth = 18;
definitions.getRange("A:A").format.columnWidth = 22; definitions.getRange("B:D").format.columnWidth = 46;
catalogReview.getRange("A:A").format.columnWidth = 20; catalogReview.getRange("B:E").format.columnWidth = 38; catalogReview.getRange("F:F").format.columnWidth = 10;
cases.getRange(`2:${caseRows.length+1}`).format.rowHeight = 54;
calls.getRange(`2:${callRows.length+1}`).format.rowHeight = 54;
turns.getRange(`2:${turnRows.length+1}`).format.rowHeight = 34;
definitions.getRange("2:12").format.rowHeight = 46;
catalogReview.getRange("5:11").format.rowHeight = 58;
catalogReview.getRange("14:17").format.rowHeight = 38;
summary.getRange("5:10").format.rowHeight = 48;
summary.getRange("17:20").format.rowHeight = 38;

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
const previewDir = `${outputPath}.previews`;
await fs.mkdir(previewDir, { recursive: true });
const previewRanges = {"汇总":"A1:H20","逐题结果":"A1:Z14","API调用明细":"A1:P14","Turn耗时":"A1:H18","目录审视":"A1:F17","口径说明":"A1:D12"};
for (const sheetName of Object.keys(previewRanges)) {
  const preview = await workbook.render({ sheetName, range: previewRanges[sheetName], autoCrop: "all", scale: 0.8, format: "png" });
  await fs.writeFile(`${previewDir}/${sheetName}.png`, new Uint8Array(await preview.arrayBuffer()));
}
const inspect = await workbook.inspect({ kind: "workbook,sheet,table", maxChars: 6000, tableMaxRows: 4, tableMaxCols: 8 });
console.log(inspect.ndjson || JSON.stringify(inspect));
