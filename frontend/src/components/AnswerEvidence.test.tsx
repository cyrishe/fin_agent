import { renderToStaticMarkup } from "react-dom/server";
import { describe, it, expect } from "vitest";
import AnswerEvidence, { splitAnswerEvidence } from "./AnswerEvidence";
import SkillActivity, { loadedSkills } from "./SkillActivity";
import BlockRenderer from "./BlockRenderer";
import { blocksFromPayload, initialRun, applyStreamEvent } from "../surface";
import type { SurfaceBlock } from "../types";

const answer: SurfaceBlock = { block_id: "answer", block_type: "narrative", semantic: "finance.answer", content: "> 增长需要结合现金流判断。\n\n## 收入与利润\n\n| 指标 | 变化 |\n|---|---|\n| 收入 | 8% |" };
const table: SurfaceBlock = { block_id: "rows", block_type: "data", kind: "data", semantic: "finance.financial.records", title: "年度财务数据", payload: { shape: "records", data: { columns: ["收入"], rows: [{ 收入: 100 }], row_count: 12 } }, presentation_hint: { preferred_renderer: "data.table" } };

describe("analysis / reference separation", () => {
  it("folds data evidence only when an analysis answer exists", () => {
    expect(splitAnswerEvidence([answer, table])).toEqual({ primary: [answer], references: [table] });
    expect(splitAnswerEvidence([table]).primary).toEqual([table]);
    expect(splitAnswerEvidence([{ ...answer, content: "" }, table]).references).toEqual([]);
    const chart = { ...table, block_id: "chart", block_type: "bar_chart", payload: { shape: "series", data: { series: [] } }, presentation_hint: { preferred_renderer: "data.bar" } };
    expect(splitAnswerEvidence([answer, chart]).references).toContain(chart);
    const unrelated = { ...table, semantic: "custom.records" };
    expect(splitAnswerEvidence([answer, unrelated]).primary).toContain(unrelated);
  });
  it("starts collapsed and does not mount data tables or load pages before opening", () => {
    let rendered = 0;
    const html = renderToStaticMarkup(<AnswerEvidence blocks={[table]} renderBlock={() => { rendered++; return <p>原始值</p>; }} />);
    expect(html).toContain("参考数据");
    expect(html).toContain("12 条记录");
    expect(html).not.toContain(" open=");
    expect(rendered).toBe(0);
  });
  it("preserves rich Markdown without inserting new financial values", () => {
    const html = renderToStaticMarkup(<BlockRenderer block={answer} />);
    expect(html).toContain("finance-answer");
    expect(html).toContain("<blockquote>");
    expect(html).toContain("<table>");
    expect(html).toContain("8%");
    expect(html).toContain('aria-label="分析对比表"');
  });
});

describe("observable Skill use", () => {
  const skill = { block_id: "runtime_skill_example", block_type: "status", title: "加载方法", data: { skill_id: "example", display_name: "示例专业方法", status: "completed", role: "process" } };
  it("requires an actual completed method load and preserves it on streaming updates", () => {
    const run = initialRun();
    expect(renderToStaticMarkup(<SkillActivity run={run} />)).toBe("");
    expect(loadedSkills({ ...run, process: [{ ...skill, data: { ...skill.data, status: "running" } }] })).toEqual([]);
    const updated = applyStreamEvent(run, { event: "block", ...skill });
    expect(renderToStaticMarkup(<SkillActivity run={updated} />)).toContain("示例专业方法");
    expect(loadedSkills(updated, { financial_qa: { skill_entries: [{ skill_id: "example" }] } })).toHaveLength(1);
  });
  it("replays method evidence alongside ordinary historical process steps", () => {
    const blocks = blocksFromPayload({ surface_blocks: [answer, table, skill], task_state: { steps: [{ title: "取数完成" }] } });
    expect(blocks.some(block => block.block_id === "runtime_task_progress")).toBe(true);
    expect(blocks.some(block => block.data?.skill_id === "example")).toBe(true);
  });
});
