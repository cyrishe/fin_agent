import { describe, expect, it } from "vitest";
import { matchesTask, readTaskLocation, taskArtifactUrl, taskKind, taskLink, taskMetrics, taskOutputs, taskProgress, taskResultSummary, taskRunLabel, taskSchedule } from "./taskCenter";
import type { UserTask, UserTaskRun } from "./types";

const task: UserTask = { task_id: "task_1", requirement_brief: "银行股研究", execution_plan: { steps: [] }, enabled: true, revision_no: 1 };
const run: UserTaskRun = { run_id: "run_1", task_id: "task_1", status: "completed" };

describe("task center view model", () => {
  it("represents immediate, one-time, and recurring definitions without fake schedules", () => {
    expect(taskKind(task)).toBe("immediate");
    expect(taskSchedule(task)).toBe("立即执行一次");
    expect(taskKind({ ...task, trigger: { at: "2026-10-04T01:00:00Z" } })).toBe("once");
    expect(taskSchedule({ ...task, trigger: { cron: "0 9 * * 1-5", timezone: "Asia/Shanghai" } })).toContain("周期执行 · 0 9 * * 1-5 · Asia/Shanghai");
    expect(matchesTask(task, "银行", "immediate")).toBe(true);
    expect(matchesTask(task, "银行", "recurring")).toBe(false);
  });

  it("encodes task/run links and restores their selection", () => {
    const link = taskLink("task / 1", "run & 2");
    expect(readTaskLocation(link.slice(link.indexOf("?")))).toEqual({ open: true, taskId: "task / 1", runId: "run & 2" });
    expect(readTaskLocation("?view=chat").open).toBe(false);
  });

  it("keeps execution completion separate from business qualification", () => {
    const completed = { ...run, result: { steps: [{ step_id: "research", status: "completed", result: { summary: "研究已完成，没有找到满足约束的模型。", domain_result: { development_constraints_met: false } } }] } };
    expect(taskRunLabel(completed)).toBe("已完成");
    expect(taskResultSummary(completed)).toContain("没有找到满足约束的模型");
    expect(taskRunLabel({ status: "running", cancel_requested_at: "2026-10-03T01:00:00Z" })).toBe("正在停止");
    expect(taskRunLabel({ status: "cancelled", cancel_requested_at: "2026-10-03T01:00:00Z" })).toBe("已取消");
  });

  it("shows recorded progress without inventing a percentage", () => {
    expect(taskProgress({ ...run, status: "running", progress: { message: "拟合随机森林" } })).toEqual({ message: "拟合随机森林" });
    expect(taskProgress({ ...run, progress: { completed_steps: 1, total_steps: 3, message: "已完成数据准备" } })).toEqual({ message: "已完成数据准备", completed: 1, total: 3 });
    expect(taskProgress({ ...run, progress: { completed_steps: 1, total_steps: 0 } }).total).toBeUndefined();
  });

  it("normalizes generic outputs once across persisted step records and bindings", () => {
    const output = { summary: "批量检查完成", metrics: [{ label: "处理文件", value: 12 }] };
    const detail = { ...run, result: { steps: [{ step_id: "check", result: output }], outputs: { check: { result: output }, report: { summary: "报告已保存" } } } };
    expect(taskOutputs(detail)).toEqual([{ stepId: "check", value: output }, { stepId: "report", value: { summary: "报告已保存" } }]);
    expect(taskMetrics(output.metrics)).toEqual([{ label: "处理文件", value: "12", unit: "" }]);
    expect(taskMetrics({ "样本数": 4850, "选中模型": "logistic", "嵌套数据": {} })).toEqual([{ label: "样本数", value: "4,850", unit: "" }, { label: "选中模型", value: "logistic", unit: "" }]);
  });

  it("only exposes authenticated task artifact download URLs", () => {
    expect(taskArtifactUrl("/api/task-runs/run_1/artifacts/art_1")).toBe("/api/task-runs/run_1/artifacts/art_1");
    for (const url of ["https://example.com/report", "//example.com/report", "file:///tmp/model.pkl", "/etc/passwd", "javascript:alert(1)", "/api/task-runs/\\evil/artifacts/id"]) expect(taskArtifactUrl(url)).toBeUndefined();
  });
});
