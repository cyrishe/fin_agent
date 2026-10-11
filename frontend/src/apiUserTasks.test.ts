import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelUserTaskRun, createUserTask, loadUserTaskRun, loadUserTaskRuns, loadUserTasks, loadUserTask, loadRecentUserTaskRuns, previewUserTask, runUserTask, updateUserTask } from "./api";
import type { UserTaskDraft } from "./types";

const draft: UserTaskDraft = { requirement_brief: "研究未来三天上涨概率", trigger: {}, budget: { max_runtime_seconds: 3600 }, execution_plan: { steps: [] } };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

describe("user task API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("previews without creating and forwards cancellation of stale requests", async () => {
    const mock = vi.fn().mockResolvedValue(response({ ok: true, preview: draft }));
    vi.stubGlobal("fetch", mock);
    const controller = new AbortController();
    await expect(previewUserTask("研究未来三天上涨概率", controller.signal)).resolves.toEqual(draft);
    expect(mock).toHaveBeenCalledWith("/api/task-definitions/preview", expect.objectContaining({ method: "POST", signal: controller.signal, body: JSON.stringify({ instruction: "研究未来三天上涨概率" }) }));
  });

  it("uses idempotency keys for create and manual rerun without sending owner IDs", async () => {
    const mock = vi.fn().mockResolvedValueOnce(response({ ok: true, task: { ...draft, task_id: "task_1" } })).mockResolvedValueOnce(response({ ok: true, run: { run_id: "run_1" } }));
    vi.stubGlobal("fetch", mock);
    await createUserTask({ instruction: "开始研究", draft, idempotencyKey: "create-key" });
    await runUserTask("task_1", "run-key");
    expect(mock.mock.calls[0][0]).toBe("/api/task-definitions");
    const createOptions = mock.mock.calls[0][1] as RequestInit;
    expect(createOptions.credentials).toBe("include");
    expect(createOptions.headers).toEqual(expect.objectContaining({ "Idempotency-Key": "create-key" }));
    expect(JSON.parse(String(createOptions.body))).toEqual({ instruction: "开始研究", draft });
    expect(mock.mock.calls[1][1].headers["Idempotency-Key"]).toBe("run-key");
  });

  it("keeps cancelling a run separate from disabling a definition", async () => {
    const mock = vi.fn().mockResolvedValueOnce(response({ ok: true, run: { run_id: "run/1", status: "cancelled" } })).mockResolvedValueOnce(response({ ok: true, task: { task_id: "task/1", enabled: false } }));
    vi.stubGlobal("fetch", mock);
    await cancelUserTaskRun("run/1");
    await updateUserTask("task/1", { enabled: false });
    expect(mock.mock.calls[0][0]).toBe("/api/task-runs/run%2F1/cancel");
    expect(mock.mock.calls[1][0]).toBe("/api/task-definitions/task%2F1");
    expect(mock.mock.calls[1][1].method).toBe("PATCH");
  });

  it("loads histories and details through task-scoped endpoints", async () => {
    const mock = vi.fn().mockResolvedValueOnce(response({ ok: true, tasks: [] })).mockResolvedValueOnce(response({ ok: true, runs: [{ run_id: "run_1" }] })).mockResolvedValueOnce(response({ ok: true, run: { run_id: "run_1", status: "running" } }));
    vi.stubGlobal("fetch", mock);
    await expect(loadUserTasks()).resolves.toEqual([]);
    await expect(loadUserTaskRuns("task_1")).resolves.toEqual([{ run_id: "run_1" }]);
    await expect(loadUserTaskRun("run_1")).resolves.toEqual({ run_id: "run_1", status: "running" });
    expect(mock.mock.calls.map(call => call[0])).toEqual(["/api/task-definitions", "/api/task-definitions/task_1/runs?limit=50", "/api/task-runs/run_1"]);
  });

  it("reads current card facts through existing authenticated endpoints", async () => {
    const mock = vi.fn().mockResolvedValueOnce(response({ ok: true, task: { ...draft, task_id: "task/1" } })).mockResolvedValueOnce(response({ ok: true, runs: [] }));
    vi.stubGlobal("fetch", mock);
    const controller = new AbortController();
    await loadUserTask("task/1", controller.signal);
    await loadRecentUserTaskRuns(controller.signal);
    expect(mock.mock.calls[0][0]).toBe("/api/task-definitions/task%2F1");
    expect(mock.mock.calls[1][0]).toBe("/api/task-runs?limit=50");
    for (const [, options] of mock.mock.calls) expect(options).toEqual(expect.objectContaining({ credentials: "include", signal: controller.signal }));
  });

  it("surfaces owner/access and compilation errors instead of an empty result", async () => {
    const mock = vi.fn().mockResolvedValueOnce(response({ ok: false, error: "任务不存在或不可访问" }, 404)).mockResolvedValueOnce(response({ ok: false, error: { message: "请说明要研究的市场" } }, 422));
    vi.stubGlobal("fetch", mock);
    await expect(loadUserTaskRun("someone_else")).rejects.toThrow("任务不存在或不可访问");
    await expect(previewUserTask("训练一个模型")).rejects.toThrow("请说明要研究的市场");
  });
});
