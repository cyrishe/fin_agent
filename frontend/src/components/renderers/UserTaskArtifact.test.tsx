import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadUserTask, loadUserTaskRun, loadUserTaskRuns } from "../../api";
import type { UserTask, UserTaskRun } from "../../types";
import BlockRenderer from "../BlockRenderer";
import { observeUserTask } from "./UserTaskArtifact";

vi.mock("../../api", async importOriginal => ({
  ...await importOriginal<typeof import("../../api")>(),
  loadUserTask: vi.fn(), loadUserTaskRun: vi.fn(), loadUserTaskRuns: vi.fn(),
}));

const render = (payload: Record<string, unknown>) => renderToStaticMarkup(<BlockRenderer block={{ block_id: "task-1", block_type: "artifact", kind: "artifact", title: "任务", payload: { artifact_type: "user_task", ...payload } }} />);

describe("chat task receipt artifact", () => {
  it("uses the receipt before the authenticated refresh and links to its exact run", () => {
    const html = render({ operation: "task_submit", recorded_at: "2026-10-03T09:00:00Z", task: { task_id: "task_1", requirement_brief: "后台研究股票上涨概率", trigger: {} }, run_id: "run_1", task_url: "javascript:alert(1)" });
    expect(html).not.toContain("非实时状态");
    expect(html).toContain("状态待查看");
    expect(html).toContain("任务已提交");
    expect(html).toContain('href="/?view=tasks&amp;task=task_1&amp;run=run_1"');
    expect(html).not.toContain('target="_blank"');
    expect(html).not.toContain("surface-title");
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("待确认");
  });

  it("shows the observed failed run without inventing a schedule when a run-only query lacks it", () => {
    const html = render({ operation: "task_get", task: { task_id: "task_1", requirement_brief: "后台研究" }, run: { run_id: "run_1", task_id: "task_1", status: "failed", error_text: "数据查询未完成" } });
    expect(html).toContain("运行失败");
    expect(html).toContain("数据查询未完成");
    expect(html).toContain("执行安排见任务详情");
    expect(html).not.toContain("一次性任务");
  });
});

const task: UserTask = { task_id: "task_1", requirement_brief: "后台研究", trigger: {}, execution_plan: { steps: [] }, enabled: true, revision_no: 1 };
const active: UserTaskRun = { task_id: "task_1", run_id: "run_1", status: "running", created_at: "2026-10-03T10:00:00Z" };

describe("chat task authoritative refresh", () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks();
    vi.mocked(loadUserTask).mockResolvedValue(task);
  });
  afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });

  it("refreshes the referenced active run every five seconds and stops on completion", async () => {
    vi.mocked(loadUserTaskRun).mockResolvedValueOnce(active).mockResolvedValue({ ...active, status: "completed", summary: "研究已完成" });
    const updated = vi.fn(), failed = vi.fn();
    const stop = observeUserTask("task_1", "run_1", updated, failed);
    await vi.advanceTimersByTimeAsync(0);
    expect(updated.mock.calls[0][0].run.status).toBe("running");
    await vi.advanceTimersByTimeAsync(5000);
    expect(updated.mock.calls[1][0].run.status).toBe("completed");
    await vi.advanceTimersByTimeAsync(20000);
    expect(loadUserTaskRun).toHaveBeenCalledTimes(2);
    expect(loadUserTaskRuns).not.toHaveBeenCalled();
    expect(failed).not.toHaveBeenCalled();
    stop();
  });

  it("finds the newest run when the receipt has no run ID and then follows that run", async () => {
    vi.mocked(loadUserTaskRuns).mockResolvedValue([{ ...active, run_id: "older", created_at: "2026-09-03" }, active]);
    vi.mocked(loadUserTaskRun).mockResolvedValue({ ...active, status: "completed" });
    const updated = vi.fn();
    const stop = observeUserTask("task_1", "", updated, vi.fn());
    await vi.advanceTimersByTimeAsync(0);
    expect(updated.mock.calls[0][0].run.run_id).toBe("run_1");
    await vi.advanceTimersByTimeAsync(5000);
    expect(loadUserTaskRun).toHaveBeenCalledWith("run_1", expect.any(AbortSignal));
    expect(loadUserTaskRuns).toHaveBeenCalledTimes(1);
    stop();
  });

  it("retains the last useful observation through a transient failure and retries an active run", async () => {
    vi.mocked(loadUserTaskRun).mockResolvedValueOnce(active).mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue({ ...active, status: "failed" });
    const updated = vi.fn(), failed = vi.fn();
    const stop = observeUserTask("task_1", "run_1", updated, failed);
    await vi.advanceTimersByTimeAsync(5000);
    expect(updated).toHaveBeenCalledTimes(1);
    expect(failed).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5000);
    expect(updated.mock.calls[1][0].run.status).toBe("failed");
    stop();
  });

  it("never overlaps a slow refresh and ignores its result after unmount cleanup", async () => {
    let complete!: (run: UserTaskRun) => void;
    vi.mocked(loadUserTaskRun).mockReturnValue(new Promise(resolve => { complete = resolve; }));
    const updated = vi.fn(), failed = vi.fn();
    const stop = observeUserTask("task_1", "run_1", updated, failed);
    await vi.advanceTimersByTimeAsync(20000);
    expect(loadUserTaskRun).toHaveBeenCalledTimes(1);
    const signal = vi.mocked(loadUserTaskRun).mock.calls[0][1]!;
    stop();
    expect(signal.aborted).toBe(true);
    complete(active);
    await vi.advanceTimersByTimeAsync(10000);
    expect(updated).not.toHaveBeenCalled();
    expect(failed).not.toHaveBeenCalled();
    expect(loadUserTaskRun).toHaveBeenCalledTimes(1);
  });

  it("does not present a different task's run under this task card", async () => {
    vi.mocked(loadUserTaskRun).mockResolvedValue({ ...active, task_id: "other_task" });
    const updated = vi.fn(), failed = vi.fn();
    const stop = observeUserTask("task_1", "run_1", updated, failed);
    await vi.advanceTimersByTimeAsync(0);
    expect(updated).not.toHaveBeenCalled();
    expect(failed).toHaveBeenCalledTimes(1);
    stop();
  });
});
