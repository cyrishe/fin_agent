import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { UserTaskRun } from "../types";
import TaskRunDetail from "./TaskRunDetail";

const base: UserTaskRun = { run_id: "run_1", task_id: "task_1", status: "running" };
const render = (run: UserTaskRun) => renderToStaticMarkup(<TaskRunDetail run={run} onCancel={() => {}} />);

describe("TaskRunDetail", () => {
  it("avoids repeating the parent card status and actions when embedded", () => {
    const html = renderToStaticMarkup(<TaskRunDetail embedded run={{ ...base, progress: { stage: "收集资料", message: "正在读取新闻" } }} onCancel={() => {}} />);
    expect(html).not.toContain("task-run-heading");
    expect(html).not.toContain("停止本次运行");
    expect(html).toContain("收集资料");
    expect(html).toContain("正在读取新闻");
    expect(html).toContain("详情与记录");
  });

  it("shows one short progress line and keeps cancellation available", () => {
    const html = render({ ...base, progress: { message: "收集第 2 份资料", completed_steps: 1, total_steps: 4 } });
    expect(html).toContain("停止本次运行");
    expect(html).toContain("收集第 2 份资料");
    expect(html).toContain("已完成 1 / 4 个步骤");
    expect(html).not.toContain("<progress");
    expect(html).not.toContain("暂停后续安排");
    expect(html.indexOf("运行编号：run_1")).toBeGreaterThan(html.indexOf("详情与记录"));
    expect(html.indexOf("安排执行")).toBeGreaterThan(html.indexOf("详情与记录"));
    expect(html).not.toContain(" open=");
  });

  it("shows only the most relevant work count without two levels of progress", () => {
    const html = render({ ...base, progress: { stage: "训练与验证", message: "拟合决策树", completed_steps: 0, total_steps: 1, completed: 3, total: 8 } });
    expect(html).toContain("训练与验证");
    expect(html).toContain("拟合决策树");
    expect(html).toContain("已完成 3 / 8 项工作");
    expect(html).not.toContain("0 / 1");
    expect(html).not.toContain("37.5%");
  });

  it("keeps a completed result visible while reports, files, metrics and records start collapsed", () => {
    const result = { summary: "研究完成；没有模型满足泛化要求。", report_markdown: "## 留出公司评估\n\n样本 178 条，没有选中信号。", metrics: [{ label: "训练样本", value: 2096 }, { label: "留出公司信号", value: 0 }], domain_result: { development_constraints_met: false, design: "冻结公司与时间留出集。", limitations: ["合成数据只验证流程。"] } };
    const html = render({ ...base, status: "completed", summary: result.summary, progress: { stage: "训练与验证", message: "报告已保存", completed: 12, total: 12 }, result: { steps: [{ step_id: "research", status: "completed", result }] }, artifacts: [{ artifact_id: "report", name: "研究报告.md", url: "/api/task-runs/run_1/artifacts/report" }] });
    expect(html).toContain("已完成");
    expect(html).toContain("没有模型满足泛化要求");
    expect(html.indexOf("没有模型满足泛化要求")).toBeLessThan(html.indexOf("<details"));
    expect(html).toContain('<details class="task-evidence task-report"><summary>查看完整报告');
    expect(html).toContain("留出公司评估");
    expect(html).toContain("2,096");
    expect(html).toContain("原始结果与验证依据");
    expect(html).toContain("冻结公司与时间留出集");
    expect(html).toContain("合成数据只验证流程");
    expect(html).toContain('<details class="task-evidence task-artifacts"><summary>交付文件 · 1 份');
    expect(html).toContain('href="/api/task-runs/run_1/artifacts/report"');
    expect(html).not.toContain("停止本次运行");
    expect(html).not.toContain("task-progress-stage");
    expect(html).not.toContain("12 / 12");
    expect(html).not.toContain(" open=");
  });

  it("keeps partial outputs visible on failure and puts error diagnostics in records", () => {
    const html = render({ ...base, status: "failed", error_text: "报告步骤超时", result: { outputs: { fetch: { result: { summary: "已收集 20 份资料" } } } }, artifacts: [{ artifact_id: "unsafe", name: "不可访问附件", url: "file:///secret/report" }] });
    expect(html).toContain('role="alert"');
    expect(html).toContain("本次运行未完成");
    expect(html).toContain("已收集 20 份资料");
    expect(html.indexOf("已收集 20 份资料")).toBeLessThan(html.indexOf("详情与记录"));
    expect(html.indexOf("报告步骤超时")).toBeGreaterThan(html.indexOf("详情与记录"));
    expect(html).not.toContain("file:///secret/report");
    expect(html).not.toContain("没有返回结果摘要");
  });

  it("keeps a pending task understandable without requiring step data", () => {
    const html = render({ ...base, status: "pending" });
    expect(html).toContain("等待执行");
    expect(html).toContain("已进入执行队列，等待后台执行");
    expect(html).toContain("停止本次运行");
    expect(html).not.toContain("已有结果");
  });

  it("does not call a cancellation request a finished cancellation or show stale progress", () => {
    const html = render({ ...base, progress: { stage: "训练与验证", message: "拟合决策树" }, cancel_requested_at: "2026-10-03T10:00:00Z" });
    expect(html).toContain("正在停止");
    expect(html).toContain("停止请求已提交");
    expect(html).toContain("disabled");
    expect(html).not.toContain("拟合决策树");
    expect(html).not.toContain("task-progress-stage");
  });

  it("explains a cancelled or empty completed result without a stale running status", () => {
    const cancelled = render({ ...base, status: "cancelled" });
    expect(cancelled).toContain("本次运行已停止");
    expect(cancelled).not.toContain("停止本次运行");
    const completed = render({ ...base, status: "completed" });
    expect(completed).toContain("本次运行没有返回结果摘要");
    expect(completed).toContain("详情与记录");
    expect(completed).not.toContain("执行中");
  });
});
