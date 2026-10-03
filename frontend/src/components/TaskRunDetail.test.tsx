import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { UserTaskRun } from "../types";
import TaskRunDetail from "./TaskRunDetail";

const base: UserTaskRun = { run_id: "run_1", task_id: "task_1", status: "running" };

describe("TaskRunDetail", () => {
  it("shows active progress and cancellation as a run action", () => {
    const html = renderToStaticMarkup(<TaskRunDetail run={{ ...base, progress: { message: "训练第 2 个实验", completed_steps: 1, total_steps: 4 } }} onCancel={() => {}} />);
    expect(html).toContain("停止本次运行");
    expect(html).toContain("训练第 2 个实验");
    expect(html).toContain('<progress max="4" value="1"');
    expect(html).not.toContain("暂停后续安排");
  });

  it("shows a tool stage and internal workload without calling them total task completion", () => {
    const html = renderToStaticMarkup(<TaskRunDetail run={{ ...base, progress: { stage: "训练与验证", message: "拟合决策树", completed_steps: 0, total_steps: 1, completed: 3, total: 8 } }} onCancel={() => {}} />);
    expect(html).toContain("当前阶段 · 训练与验证");
    expect(html).toContain("步骤内工作量 3 / 8");
    expect(html).toContain("不代表整体耗时进度");
    expect(html).toContain("已完成 0 / 1 个执行步骤");
    expect(html).toContain('<progress max="8" value="3" aria-label="步骤内已完成工作量"');
    expect(html).not.toContain("37.5%");
  });

  it("renders an ML finding as completed work with readable report and evidence", () => {
    const result = { summary: "研究完成；没有模型满足泛化要求。", report_markdown: "## 留出公司评估\n\n样本 178 条，没有选中信号。", metrics: [{ label: "训练样本", value: 2096 }, { label: "留出公司信号", value: 0 }], domain_result: { development_constraints_met: false, design: "冻结公司与时间留出集。", limitations: ["合成数据只验证流程。"] } };
    const html = renderToStaticMarkup(<TaskRunDetail run={{ ...base, status: "completed", summary: result.summary, result: { steps: [{ step_id: "research", status: "completed", result }] }, artifacts: [{ artifact_id: "report", name: "研究报告.md", url: "/api/task-runs/run_1/artifacts/report" }] }} onCancel={() => {}} />);
    expect(html).toContain("已完成");
    expect(html).toContain("没有模型满足泛化要求");
    expect(html).toContain("2,096");
    expect(html).toContain("留出公司评估");
    expect(html).toContain("结果与验证依据");
    expect(html).toContain("执行设计与分析");
    expect(html).toContain("冻结公司与时间留出集");
    expect(html).toContain("合成数据只验证流程");
    expect(html).toContain('href="/api/task-runs/run_1/artifacts/report"');
    expect(html).not.toContain("停止本次运行");
    expect(html.indexOf("查看完整报告")).toBeLessThan(html.indexOf("结果说明与限制"));
    expect(html).toContain('<details class="task-evidence task-result-notes"><summary>结果说明与限制 · 1 项');
  });

  it("keeps partial outputs readable when a non-ML task fails", () => {
    const html = renderToStaticMarkup(<TaskRunDetail run={{ ...base, status: "failed", error_text: "报告步骤超时", result: { outputs: { fetch: { result: { summary: "已收集 20 份资料" } } } }, artifacts: [{ artifact_id: "unsafe", name: "不可访问附件", url: "file:///secret/report" }] }} onCancel={() => {}} />);
    expect(html).toContain("报告步骤超时");
    expect(html).toContain("已收集 20 份资料");
    expect(html).not.toContain("file:///secret/report");
  });

  it("does not call a cancellation request a finished cancellation", () => {
    const html = renderToStaticMarkup(<TaskRunDetail run={{ ...base, cancel_requested_at: "2026-10-03T10:00:00Z" }} onCancel={() => {}} />);
    expect(html).toContain("正在停止");
    expect(html).toContain("停止请求已提交");
    expect(html).toContain("disabled");
  });
});
