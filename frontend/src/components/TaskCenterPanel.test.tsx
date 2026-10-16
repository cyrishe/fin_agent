import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { UserTaskDraft } from "../types";
import { TaskPlan, TaskRequirement } from "./TaskCenterPanel";

const draft: UserTaskDraft = {
  requirement_brief: "比较股票模型",
  execution_plan: { steps: [{ step_id: "research", type: "tool", target_ref: { kind: "tool", name: "stock_automl_research" }, inputs: { requirement_brief: "研究未来 3 个交易日的上涨概率，保留新公司测试。", source: "kingdomai" }, depends_on: [] }] },
};

describe("TaskPlan", () => {
  it("uses saved display metadata and shows the full natural language instruction before parameters", () => {
    const html = renderToStaticMarkup(<TaskPlan draft={{ ...draft, preview: { steps: [{ step_id: "unrelated", display_name: "其他任务" }, { step_id: "research", display_name: "股票 AutoML 研究任务" }] } }} />);
    expect(html).toContain("<strong>股票 AutoML 研究任务</strong>");
    expect(html).toContain('<p class="task-step-brief">研究未来 3 个交易日的上涨概率，保留新公司测试。</p>');
    expect(html).not.toContain("其他任务");
    expect(html).toContain('<details class="task-step-inputs"><summary>查看执行参数</summary>');
    expect(html).toContain("kingdomai");
  });

  it("keeps older and non-research task plans readable without preview metadata", () => {
    const html = renderToStaticMarkup(<TaskPlan draft={{ ...draft, execution_plan: { steps: [{ step_id: "fetch", type: "tool", target_ref: { kind: "tool", name: "stock_realtime_quote" }, inputs: { code: "600519" }, depends_on: ["prepare"] }] } }} />);
    expect(html).toContain("<strong>stock_realtime_quote</strong>");
    expect(html).toContain("在 prepare 完成后执行");
    expect(html).not.toContain("task-step-brief");
  });

  it("does not repeat identical task instructions inside the visible step plan", () => {
    const instruction = String(draft.execution_plan.steps[0].inputs.requirement_brief);
    const html = renderToStaticMarkup(<TaskPlan draft={{ ...draft, requirement_brief: ` ${instruction} ` }} />);
    expect(html).not.toContain("task-step-brief");
    expect(html).toContain(instruction);
    expect(html).toContain("查看执行参数");
  });

  it("keeps long requirements complete in an initially collapsed disclosure for preview and detail", () => {
    const instruction = "这是一个带有很多明确条件的研究任务。".repeat(12) + "最后一项约束也必须完整保留。";
    for (const heading of [false, true]) {
      const html = renderToStaticMarkup(<TaskRequirement text={instruction} heading={heading} />);
      expect(html).toContain("task-requirement-clamped");
      expect(html).toContain('<details class="task-requirement-full"><summary>查看完整任务要求</summary>');
      expect(html).toContain(`<p>${instruction}</p>`);
      expect(html).not.toContain(" open=");
    }
    expect(renderToStaticMarkup(<TaskRequirement text="简短任务" />)).not.toContain("查看完整任务要求");
  });
});
