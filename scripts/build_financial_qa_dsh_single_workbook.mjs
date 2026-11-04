import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [analysisPath, outputPath] = process.argv.slice(2);
if (!analysisPath || !outputPath) throw new Error("usage: node build_financial_qa_dsh_single_workbook.mjs analysis.json output.xlsx");
const analysis = JSON.parse(await fs.readFile(analysisPath, "utf8"));
const cases = analysis.cases || [];
const motherSet = analysis.source_metadata?.mother_set;
const excludedNews = analysis.source_metadata?.excluded_pure_news;
const isMergedFull = analysis.analysis === "dsh_opt_mainland_report_full_no_news_184";
const scopeLabel = isMergedFull ? "完整无News评测" : "评测";
const wb = Workbook.create();
const summary = wb.worksheets.add("总览");
const detail = wb.worksheets.add("逐题结果");
const api = wb.worksheets.add("API执行明细");
const llm = wb.worksheets.add("LLM阶段消耗");
const answers = wb.worksheets.add("回答明细");
const datasets = wb.worksheets.add("数据集概览");
const rawData = wb.worksheets.add("原始数据样例");
const method = wb.worksheets.add("口径与稳定性");

const C = { navy: "#17365D", blue: "#D9EAF7", teal: "#0F6B78", green: "#E2F0D9", amber: "#FFF2CC", red: "#FCE4D6", gray: "#F2F2F2", white: "#FFFFFF", ink: "#1F2937" };
const header = (range, fill = C.navy) => { range.format = { fill, font: { bold: true, color: C.white }, verticalAlignment: "center", wrapText: true, borders: { preset: "inside", style: "thin", color: "#D9E2F3" } }; };
const title = (sheet, range, value) => { sheet.getRange(range).merge(); sheet.getRange(range.split(":")[0]).values = [[value]]; sheet.getRange(range).format = { fill: C.navy, font: { bold: true, color: C.white, size: 16 }, verticalAlignment: "center" }; sheet.getRange(range).format.rowHeight = 34; };
const body = (range) => { range.format = { font: { color: C.ink, size: 10 }, verticalAlignment: "top", borders: { insideHorizontal: { style: "thin", color: "#E5E7EB" } } }; };
const yn = (value) => value ? "是" : "否";
const safe = (value, limit = 12000) => String(value || "").slice(0, limit);
const failureSummary = (analysis.first_pass_failures || []).map(x => {
  const message = String(x.error || "error");
  const category = message.match(/'category':\s*'([^']+)'/)?.[1] || message.match(/category[=:]\s*([^,;}]+)/i)?.[1] || message.split(":")[0];
  return `${x.case_id}（${safe(category, 48)}）`;
}).join("；") || "无";

const detailHeaders = ["题号","问题","分类","原始首跑状态","有效状态","耗时(秒)","Prompt tokens","Completion tokens","总tokens","Cache read tokens","累计上下文tokens","Reasoning tokens","模型调用数","工具调用数","目录读取数","数据查询数","结果加载数","首入口","Golden首入口","首入口正确","最终含可接受入口","覆盖全部Required","零行Query数","结果总行数","错误"];
const detailRows = cases.map(c => [c.case_id,c.question,c.category,c.first_pass_status,c.status,c.elapsed_ms/1000,c.prompt_tokens,c.completion_tokens,c.total_tokens,c.cache_read_tokens,c.cumulative_context_tokens,c.reasoning_tokens,c.model_calls,c.tool_calls,c.catalog_reads,c.finance_queries,c.result_loads,c.first_entry,c.primary_entry,yn(c.first_entry_correct),yn(c.eventual_acceptable),yn(c.all_required_entries),c.zero_row_queries,c.result_rows,safe(c.error)]);
detail.getRangeByIndexes(0,0,detailRows.length+1,detailHeaders.length).values = [detailHeaders,...detailRows];
header(detail.getRangeByIndexes(0,0,1,detailHeaders.length)); body(detail.getRangeByIndexes(1,0,detailRows.length,detailHeaders.length));
detail.tables.add(`A1:Y${detailRows.length+1}`, true, "CaseResults"); detail.freezePanes.freezeRows(1); detail.freezePanes.freezeColumns(2); detail.showGridLines = false;
detail.getRange(`B2:C${detailRows.length+1}`).format.wrapText = true; detail.getRange(`F2:F${detailRows.length+1}`).format.numberFormat = "0.00"; detail.getRange(`G2:Q${detailRows.length+1}`).format.numberFormat = "#,##0";

title(summary,"A1:H1",`DSH Opt 金融数据查询${scopeLabel}（${cases.length}题）`);
summary.getRange("A2:H2").merge(); summary.getRange("A2").values = [[`大陆上市公司研报题集${motherSet ? `共${motherSet}题` : ""}；${excludedNews ? `排除${excludedNews}道纯news任务后` : "纯news任务已排除"}，本报告合并全部${cases.length}道可评测题。同一题独立会话、题级并发3。准确率以Golden入口协议为准，数据答案本身未做逐值人工复核。`]]; summary.getRange("A2:H2").format = { fill: C.blue, wrapText: true, font: { color: C.navy } }; summary.getRange("A2:H2").format.rowHeight = 42;
summary.getRange("A4:D4").values = [["核心指标","数值","分母/单位","说明"]]; header(summary.getRange("A4:D4"));
const n = detailRows.length + 1;
const metrics = [
  ["首跑完成率",`=COUNTIF('逐题结果'!$D$2:$D$${n},"ok")/COUNTA('逐题结果'!$A$2:$A$${n})`,`${cases.length}题`,"保留首次运行稳定性"],
  ["重跑后完成率",`=COUNTIF('逐题结果'!$E$2:$E$${n},"ok")/COUNTA('逐题结果'!$A$2:$A$${n})`,`${cases.length}题`,"仅对首跑瞬时异常各重跑一次"],
  ["首入口准确率",`=COUNTIF('逐题结果'!$T$2:$T$${n},"是")/COUNTA('逐题结果'!$A$2:$A$${n})`,`${cases.length}题`,"第一入口命中可接受Golden"],
  ["最终可接受入口率",`=COUNTIF('逐题结果'!$U$2:$U$${n},"是")/COUNTA('逐题结果'!$A$2:$A$${n})`,`${cases.length}题`,"执行链最终包含可接受入口"],
  ["全部Required覆盖率",`=COUNTIF('逐题结果'!$V$2:$V$${n},"是")/COUNTA('逐题结果'!$A$2:$A$${n})`,`${cases.length}题`,"按去除stock.news后的Required口径"],
  ["平均耗时",`=AVERAGE('逐题结果'!$F$2:$F$${n})`,"秒/题","端到端客户端观测"],
  ["中位耗时",`=MEDIAN('逐题结果'!$F$2:$F$${n})`,"秒/题","端到端客户端观测"],
  ["P95耗时",`=PERCENTILE.INC('逐题结果'!$F$2:$F$${n},0.95)`,"秒/题","端到端客户端观测"],
  ["总耗时",`=SUM('逐题结果'!$F$2:$F$${n})`,"秒（单题累加）","并发运行墙钟时间不同"],
  ["上报总tokens",`=SUM('逐题结果'!$I$2:$I$${n})`,"合计","Prompt + Completion；不含cache read"],
  ["平均总tokens",`=AVERAGE('逐题结果'!$I$2:$I$${n})`,"tokens/题","上报口径"],
  ["累计上下文tokens",`=SUM('逐题结果'!$K$2:$K$${n})`,"合计","各LLM请求上下文处理量累加"],
  ["Reasoning tokens",`=SUM('逐题结果'!$L$2:$L$${n})`,"合计","DSH逐请求上报"],
  ["模型请求数",`=SUM('逐题结果'!$M$2:$M$${n})`,"合计",`${cases.length}题`],
  ["工具调用数",`=SUM('逐题结果'!$N$2:$N$${n})`,"合计","目录、查询、加载"],
];
summary.getRangeByIndexes(4,0,metrics.length,4).values = metrics.map(([a,,c,d]) => [a,null,c,d]);
summary.getRangeByIndexes(4,1,metrics.length,1).formulas = metrics.map(([,f]) => [f]); body(summary.getRange(`A5:D${4+metrics.length}`));
summary.getRange("B5:B9").format.numberFormat = "0.0%"; summary.getRange("B10:B13").format.numberFormat = "0.00"; summary.getRange(`B14:B${4+metrics.length}`).format.numberFormat = "#,##0";
summary.getRange("A5:A19").format.fill = C.gray; summary.getRange("B5:B19").format.fill = C.green; summary.getRange("D5:D19").format.wrapText = true;
summary.getRange("F4:H4").values = [["稳定性观察","数值","判断"]]; header(summary.getRange("F4:H4"), C.teal);
const slowest = [...cases].sort((a,b)=>b.elapsed_ms-a.elapsed_ms)[0];
summary.getRange("F5:H9").values = [["首跑异常",`${analysis.summary.first_pass_error} / ${analysis.summary.case_count}`,`${(analysis.first_pass_failures||[]).map(x=>x.case_id).join("、") || "无"}；异常题单次重跑`],["最长耗时",analysis.summary.metrics.elapsed_ms.max/1000,slowest?.case_id||""],["超过90秒",cases.filter(c=>c.elapsed_ms>90000).length,"题"],["正常空结果",analysis.summary.zero_row_cases,"条件与题意一致时保留空值"],["有效入口",`${analysis.summary.eventual_acceptable} / ${analysis.summary.case_count}`,"最终包含可接受入口"]]; body(summary.getRange("F5:H9")); summary.getRange("G6:G6").format.numberFormat = "0.00";
summary.freezePanes.freezeRows(4); summary.showGridLines = false;

const apiHeaders = ["题号","问题","Golden首入口","可接受入口","Required入口","目录读取序列","数据API序列","Query DSL","首入口正确","最终可接受","覆盖Required"];
const apiRows = cases.map(c=>[c.case_id,c.question,c.primary_entry,c.acceptable_entries.join("\n"),c.required_entries.join("\n"),c.catalog_sequence.join("\n"),c.api_sequence.join("\n"),safe(c.query_requests.join("\n\n")),yn(c.first_entry_correct),yn(c.eventual_acceptable),yn(c.all_required_entries)]);
api.getRangeByIndexes(0,0,apiRows.length+1,apiHeaders.length).values=[apiHeaders,...apiRows]; header(api.getRangeByIndexes(0,0,1,apiHeaders.length)); body(api.getRangeByIndexes(1,0,apiRows.length,apiHeaders.length)); api.tables.add(`A1:K${apiRows.length+1}`,true,"ApiDetail"); api.freezePanes.freezeRows(1); api.freezePanes.freezeColumns(2); api.showGridLines=false; api.getRange(`B2:H${apiRows.length+1}`).format.wrapText=true;

const llmHeaders=["题号","请求序号","阶段","阶段原因","Reasoning effort","Max tokens","Context tokens","Output tokens","Reasoning tokens","可见Output","命中上限","注入提示","调用工具"];
const llmRows=[]; for(const c of cases){ for(const s of c.stage_trace||[]){ llmRows.push([c.case_id,s.request_index,s.stage,s.stage_reason||"",s.reasoning_effort,s.max_tokens,s.context_tokens,s.output_tokens,s.reasoning_tokens,s.visible_output_tokens,yn(s.max_token_hit),yn(s.prompt_injected),(s.called_tools||[]).join("\n")]); }}
llm.getRangeByIndexes(0,0,llmRows.length+1,llmHeaders.length).values=[llmHeaders,...llmRows]; header(llm.getRangeByIndexes(0,0,1,llmHeaders.length)); body(llm.getRangeByIndexes(1,0,llmRows.length,llmHeaders.length)); llm.tables.add(`A1:M${llmRows.length+1}`,true,"LlmSteps"); llm.freezePanes.freezeRows(1); llm.showGridLines=false; llm.getRange(`D2:D${llmRows.length+1}`).format.wrapText=true;

answers.getRangeByIndexes(0,0,cases.length+1,3).values=[["题号","问题","DSH Opt回答"],...cases.map(c=>[c.case_id,c.question,safe(c.answer,15000)])]; header(answers.getRange("A1:C1")); body(answers.getRange(`A2:C${cases.length+1}`)); answers.tables.add(`A1:C${cases.length+1}`,true,"Answers"); answers.freezePanes.freezeRows(1); answers.freezePanes.freezeColumns(2); answers.showGridLines=false; answers.getRange(`B2:C${cases.length+1}`).format.wrapText=true; answers.getRange(`2:${cases.length+1}`).format.rowHeight=90;

const datasetHeaders=["题号","问题","Flow step","结果名","查询目标","API","Result ref","Schema","总行数","完整恢复","样例展示行数"];
const datasetRows=[];
for(const c of cases){ for(const result of c.raw_results||[]){ datasetRows.push([c.case_id,c.question,result.flow_step,result.result_name,result.goal,result.api,result.result_ref,JSON.stringify(result.schema||{}),result.row_count,yn(result.rows_complete),Math.min(5,(result.rows||[]).length)]); }}
datasets.getRangeByIndexes(0,0,datasetRows.length+1,datasetHeaders.length).values=[datasetHeaders,...datasetRows]; header(datasets.getRange("A1:K1")); body(datasets.getRangeByIndexes(1,0,datasetRows.length,datasetHeaders.length)); datasets.tables.add(`A1:K${datasetRows.length+1}`,true,"DatasetOverview"); datasets.freezePanes.freezeRows(1); datasets.freezePanes.freezeColumns(2); datasets.showGridLines=false; datasets.getRange(`B2:H${datasetRows.length+1}`).format.wrapText=true;

const rawHeaders=["题号","问题","Flow step","结果名","查询目标","API","Result ref","总行数","完整恢复","样例Row index","row-dict（JSON，最多前5行）"];
const rawRows=[];
for(const c of cases){
  for(const result of c.raw_results||[]){
    const rows=result.rows||[];
    if(!rows.length){ rawRows.push([c.case_id,c.question,result.flow_step,result.result_name,result.goal,result.api,result.result_ref,result.row_count,yn(result.rows_complete),"", "{}"]) }
    else rows.slice(0,5).forEach((row,index)=>rawRows.push([c.case_id,c.question,result.flow_step,result.result_name,result.goal,result.api,result.result_ref,result.row_count,yn(result.rows_complete),index+1,JSON.stringify(row)]));
  }
}
rawData.getRangeByIndexes(0,0,rawRows.length+1,rawHeaders.length).values=[rawHeaders,...rawRows]; header(rawData.getRange("A1:K1")); body(rawData.getRangeByIndexes(1,0,rawRows.length,rawHeaders.length)); rawData.tables.add(`A1:K${rawRows.length+1}`,true,"RawRowDicts"); rawData.freezePanes.freezeRows(1); rawData.freezePanes.freezeColumns(2); rawData.showGridLines=false; rawData.getRange(`B2:G${rawRows.length+1}`).format.wrapText=true; rawData.getRange(`K2:K${rawRows.length+1}`).format.wrapText=true;

title(method,"A1:F1","评测口径与首跑异常"); method.getRange("A3:F3").values=[["项目","值","口径","范围","说明","注意"]]; header(method.getRange("A3:F3"));
const methodRows=[
  ["题集",analysis.source_path,isMergedFull ? "大陆上市公司研报完整无News评测集" : "大陆上市公司研报题集","逐题",isMergedFull ? "67题历史批次 + 117题增量；case_id无重叠，保留原题集2组重复题文" : "按原题集口径保留",excludedNews ? `母集${motherSet}题；${excludedNews}道纯stock.news任务已排除` : "纯stock.news任务已排除"],
  ["运行时","DSH Opt / deepseek-v4-flash","阶段策略编排","全量","reasoning按阶段配置","并发3；单题超时600秒"],
  ["准确率-首入口","首个观察到的subject.dataview属于acceptable_first_entries","协议入口","逐题","衡量初始路由","不等于最终答案逐值准确率"],
  ["准确率-最终可接受","完整执行链至少包含一个acceptable入口","协议入口","逐题","衡量纠偏后有效执行","不要求API序列完全一致"],
  ["Required覆盖","执行链覆盖全部required_entries","协议入口","逐题","严格采用原题集","news停用会让3题天然不满足"],
  ["Token","total=prompt+completion；cache read另列","模型服务上报","逐请求累加","累计上下文是每轮context相加","不能与最终上下文长度等同"],
  ["首跑异常",failureSummary,"稳定性",`${analysis.summary.first_pass_error}题`,"异常题各单独重跑一次；逐题结果保留错误详情","工作簿有效指标使用重跑结果；首跑率单列"],
]; method.getRangeByIndexes(3,0,methodRows.length,6).values=methodRows; body(method.getRange(`A4:F${3+methodRows.length}`)); method.getRange(`A4:F${3+methodRows.length}`).format.wrapText=true; method.showGridLines=false; method.freezePanes.freezeRows(3);
method.getRange(`4:${3+methodRows.length}`).format.rowHeight=34;

summary.getRange("A:A").format.columnWidth=26; summary.getRange("B:B").format.columnWidth=18; summary.getRange("C:C").format.columnWidth=18; summary.getRange("D:D").format.columnWidth=42; summary.getRange("F:F").format.columnWidth=22; summary.getRange("G:G").format.columnWidth=16; summary.getRange("H:H").format.columnWidth=52;
detail.getRange("A:A").format.columnWidth=10; detail.getRange("B:B").format.columnWidth=48; detail.getRange("C:C").format.columnWidth=24; detail.getRange("D:Y").format.columnWidth=16; detail.getRange("Y:Y").format.columnWidth=36;
api.getRange("A:A").format.columnWidth=10; api.getRange("B:B").format.columnWidth=48; api.getRange("C:G").format.columnWidth=25; api.getRange("H:H").format.columnWidth=80; api.getRange("I:K").format.columnWidth=16;
llm.getRange("A:M").format.columnWidth=16; llm.getRange("D:D").format.columnWidth=28; llm.getRange("M:M").format.columnWidth=25;
answers.getRange("A:A").format.columnWidth=10; answers.getRange("B:B").format.columnWidth=48; answers.getRange("C:C").format.columnWidth=90;
rawData.getRange("A:A").format.columnWidth=10; rawData.getRange("B:B").format.columnWidth=42; rawData.getRange("C:D").format.columnWidth=12; rawData.getRange("E:E").format.columnWidth=42; rawData.getRange("F:F").format.columnWidth=24; rawData.getRange("G:G").format.columnWidth=42; rawData.getRange("H:J").format.columnWidth=14; rawData.getRange("K:K").format.columnWidth=100;
datasets.getRange("A:A").format.columnWidth=10; datasets.getRange("B:B").format.columnWidth=42; datasets.getRange("C:D").format.columnWidth=12; datasets.getRange("E:E").format.columnWidth=42; datasets.getRange("F:F").format.columnWidth=24; datasets.getRange("G:G").format.columnWidth=42; datasets.getRange("H:H").format.columnWidth=70; datasets.getRange("I:K").format.columnWidth=16;
method.getRange("A:A").format.columnWidth=22; method.getRange("B:B").format.columnWidth=52; method.getRange("C:F").format.columnWidth=28;

await fs.mkdir(new URL(".", `file://${outputPath}`).pathname, { recursive: true }).catch(()=>{});
const out = await SpreadsheetFile.exportXlsx(wb); await out.save(outputPath);
const inspect = await wb.inspect({kind:"table",range:"总览!A1:H19",include:"values,formulas",tableMaxRows:25,tableMaxCols:10}); await fs.writeFile(`${outputPath}.inspection.ndjson`,inspect.ndjson);
const errors = await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:100},summary:"formula error scan"}); await fs.writeFile(`${outputPath}.errors.ndjson`,errors.ndjson);
const previewRanges={"总览":"A1:H19","逐题结果":"A1:Y25","API执行明细":"A1:K25","LLM阶段消耗":"A1:M30","回答明细":"A1:C18","数据集概览":"A1:K25","原始数据样例":"A1:K25","口径与稳定性":"A1:F11"};
for(const [sheetName,range] of Object.entries(previewRanges)){ const preview=await wb.render({sheetName,range,scale:0.8,format:"png"}); await fs.writeFile(`${outputPath}.${sheetName}.png`,new Uint8Array(await preview.arrayBuffer())); }
