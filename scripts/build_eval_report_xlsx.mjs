import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "/Users/chenghe/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const input = process.argv[2];
const output = process.argv[3];
if (!input || !output) throw new Error("usage: build_eval_report_xlsx.mjs input.json output.xlsx");
const report = JSON.parse(await fs.readFile(input, "utf8"));
const wb = Workbook.create();
const detail = wb.worksheets.add("逐轮明细");
const summary = wb.worksheets.add("案例汇总");
const notes = wb.worksheets.add("测试说明");

const header = ["dialogue_id", "case_id", "turn_id", "turn", "stage", "user_question", "agent_answer", "context", "elapsed_ms", "judgment", "evidence_or_issue", "route", "response_keys"];
const rows = [header];
const summaryRows = [["case_id", "initial_request", "turns", "total_elapsed_ms", "design_reached", "result", "judgment", "root_cause_or_note"]];

function text(v, max = 30000) {
  const s = v == null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2);
  return s.length > max ? `${s.slice(0, max)}\n…[truncated]` : s;
}
function stageFor(turn) {
  if (turn.stage) return text(turn.stage, 100);
  const answer = text(turn.agent_answer);
  const question = text(turn.user_question);
  const asksForDesign = /(形成完整的工具设计|形成完整设计|模块与流程设计|进入设计方案|继续.*设计方案)/.test(question);
  if (asksForDesign && /评测范围.*限定|暂时不进入模块与流程设计/.test(answer)) return "design_blocked";
  if (asksForDesign && /(设计方案已保存|以下是完整的工具设计|模块划分|流程设计方案|整体架构)/.test(answer)) return "design";
  return "requirement";
}
function judgmentFor(turn, stage) {
  if (turn.judgment) return text(turn.judgment, 1000);
  const answer = text(turn.agent_answer);
  if (stage === "design") {
    if (/设计方案已保存|完整工具设计|模块与流程设计/.test(answer)) return "基本可行：已从需求进入 Design，并给出结构化的自然语言方案。";
    return "方向基本正确，但 Design 产出不完整。";
  }
  if (stage === "design_blocked") return "方向不对：用户已经明确要求进入 Design，但回答仍把流程限制在 Requirement。";
  if (/评测范围限定|暂时不进入模块与流程设计/.test(answer)) return "方向不对：系统被留在 Requirement，且回答暴露了不应由业务对话承担的评测范围限制。";
  if (/需要您确认|等待你确认|待您反馈|只需要告诉我/.test(answer)) return "基本可行：需求摘要清晰，提问集中在会影响实现的口径。";
  return "基本可行：已给出需求理解，但仍需观察是否能自然收敛到 Design。";
}
for (const c of report.cases || []) {
  let reached = false;
  for (const [turnIndex, t] of (c.turns || []).entries()) {
    const stage = stageFor(t);
    reached ||= stage === "design";
    const evidence = t.evidence_or_issue || (stage === "design"
      ? "response_keys 包含 design；回答已出现设计方案/模块流程。"
      : /评测范围.*限定|暂时不进入/.test(text(t.agent_answer))
        ? "模型回答自行声明评测范围，阻断了从 Requirement 到 Design。"
        : "需求摘要与 questions/pending_questions 由现场响应产生。");
    rows.push([
      t.dialogue_id, c.case_id, t.turn_id, turnIndex + 1, stage,
      text(t.user_question, 12000), text(t.agent_answer), text(t.context, 12000),
      Number(t.elapsed_ms || 0), judgmentFor(t, stage), evidence,
      text(t.route), text(t.response_keys),
    ]);
  }
  const terminal = text(c.terminal);
  const result = terminal === "design_reached" ? "到达 Design" : terminal === "api_error" ? "API 异常" : "未到达 Design";
  const judgment = reached ? "基本可行" : terminal === "api_error" ? "外部/API异常" : "需要继续验证";
  const note = terminal === "design_reached"
    ? "真实响应已形成 Design，评测在 Design 处停止。"
    : text(c.error || "本次现场没有稳定到达 Design。", 1000);
  summaryRows.push([c.case_id, text((c.turns?.[0] || {}).user_question, 8000), (c.turns || []).length, Number(c.total_elapsed_ms || 0), reached ? "是" : "否", result, judgment, note]);
}

function styleSheet(sheet, range, widths) {
  sheet.showGridLines = false;
  const r = sheet.getRange(range);
  r.format.wrapText = true;
  r.format.font = { name: "Aptos", size: 10, color: "#1F2937" };
  r.format.borders = { preset: "inside", style: "thin", color: "#E5E7EB" };
  const h = sheet.getRange(range.split(":")[0] + ":" + range.split(":")[1].replace(/\d+$/, "1"));
  h.format.fill = "#1D4ED8";
  h.format.font = { name: "Aptos Display", size: 10, bold: true, color: "#FFFFFF" };
  h.format.wrapText = true;
  for (const [col, width] of Object.entries(widths)) sheet.getRange(`${col}:${col}`).format.columnWidth = width;
}

detail.getRangeByIndexes(0, 0, rows.length, header.length).values = rows;
styleSheet(detail, `A1:M${rows.length}`, { A: 12, B: 28, C: 10, D: 8, E: 12, F: 42, G: 90, H: 70, I: 14, J: 42, K: 52, L: 20, M: 42 });
detail.freezePanes.freezeRows(1);
detail.getRange(`I2:I${rows.length}`).format.numberFormat = "#,##0.00";

summary.getRangeByIndexes(0, 0, summaryRows.length, summaryRows[0].length).values = summaryRows;
styleSheet(summary, `A1:H${summaryRows.length}`, { A: 28, B: 70, C: 10, D: 18, E: 14, F: 18, G: 48, H: 70 });
summary.freezePanes.freezeRows(1);
summary.getRange(`D2:D${summaryRows.length}`).format.numberFormat = "#,##0.00";

const noteRows = [
  ["项目", "说明"],
  ["测试范围", "真实调用 /api/chat/dispatch，后续用户回答根据上一轮返回的 questions/candidates 现场生成；每个 case 只观察 Requirement → Design。"],
  ["判定口径", "基本可行表示当前轮方向与主线一致；方向不对表示出现阻断主线的明显行为；不评价业务策略本身是否正确。"],
  ["耗时", "elapsed_ms 为该轮 HTTP/模型调用的端到端耗时；total_elapsed_ms 为案例累计耗时。"],
  ["注意", "本报告把已返回设计但被旧评测器漏判的轮次单独标为 Design；这避免把测试器缺陷误报成业务流程失败。"],
];
notes.getRangeByIndexes(0, 0, noteRows.length, 2).values = noteRows;
styleSheet(notes, `A1:B${noteRows.length}`, { A: 20, B: 120 });
notes.freezePanes.freezeRows(1);

await fs.mkdir(output.substring(0, output.lastIndexOf("/")), { recursive: true });
const blob = await SpreadsheetFile.exportXlsx(wb);
await blob.save(output);
const preview = await wb.render({ sheetName: "案例汇总", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(output.replace(/\.xlsx$/, "_summary_preview.png"), new Uint8Array(await preview.arrayBuffer()));
const detailPreview = await wb.render({ sheetName: "逐轮明细", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(output.replace(/\.xlsx$/, "_detail_preview.png"), new Uint8Array(await detailPreview.arrayBuffer()));
const notesPreview = await wb.render({ sheetName: "测试说明", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(output.replace(/\.xlsx$/, "_notes_preview.png"), new Uint8Array(await notesPreview.arrayBuffer()));
console.log(JSON.stringify({ output, preview: output.replace(/\.xlsx$/, "_summary_preview.png"), rows: rows.length - 1, cases: summaryRows.length - 1 }));
