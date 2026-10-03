import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { UserTask, UserTaskDraft } from "../types";
import UserTaskCard from "./UserTaskCard";

const draft: UserTaskDraft = { requirement_brief: "研究未来七个交易日的上涨概率", trigger: {}, execution_plan: { steps: [] } };
const saved: UserTask = { ...draft, task_id: "private-task-id", enabled: true, revision_no: 1 };

describe("shared task card", () => {
  it("shows purpose, arrangement and real status without technical task metadata", () => {
    const html = renderToStaticMarkup(<UserTaskCard task={{ ...saved, budget: { max_runtime_seconds: 600 } }} run={{ run_id: "private-run-id", task_id: saved.task_id, status: "running" }} actions={<button>停止本次</button>}><p>正在比较第三个模型。</p></UserTaskCard>);
    expect(html).toContain("一次性任务");
    expect(html).toContain("执行中");
    expect(html).toContain(draft.requirement_brief);
    expect(html).toContain("停止本次");
    expect(html).toContain("正在比较第三个模型");
    expect(html).not.toContain("private-task-id");
    expect(html).not.toContain("private-run-id");
    expect(html).not.toContain("600");
  });

  it("distinguishes an unconfirmed draft, schedule pause and unavailable runtime evidence", () => {
    expect(renderToStaticMarkup(<UserTaskCard task={draft} />)).toContain("待确认");
    const paused = { ...saved, trigger: { cron: "0 9 * * 1-5" }, enabled: false };
    expect(renderToStaticMarkup(<UserTaskCard task={paused} />)).toContain("后续已暂停");
    const unknown = renderToStaticMarkup(<UserTaskCard task={saved} statusLoading />);
    expect(unknown).toContain("状态待查看");
    expect(unknown).not.toContain("等待执行");
  });

  it("keeps long requirements complete behind an initially closed disclosure", () => {
    const purpose = "遵循用户约束，使用历史训练和独立公司验证。".repeat(12) + "保留最后一个约束。";
    const html = renderToStaticMarkup(<UserTaskCard task={{ ...draft, requirement_brief: purpose }} />);
    expect(html).toContain("task-requirement-clamped");
    expect(html).toContain('<details class="task-requirement-full"><summary>查看完整任务要求</summary>');
    expect(html).toContain(`<p>${purpose}</p>`);
    expect(html).not.toContain(" open=");
  });

  it("keeps future schedule state visible when the latest run has completed", () => {
    const recurring = { ...saved, trigger: { cron: "0 9 * * 1-5" }, next_run_at: "2026-10-05T01:00:00Z" };
    const run = { run_id: "run_1", task_id: saved.task_id, status: "completed" };
    const active = renderToStaticMarkup(<UserTaskCard task={recurring} run={run} />);
    expect(active).toContain("已完成");
    expect(active).toContain("下次 ");
    const paused = { ...recurring, enabled: false };
    const html = renderToStaticMarkup(<UserTaskCard task={paused} run={run} />);
    expect(html).toContain("已完成");
    expect(html).toContain("后续已暂停");
    expect(html).not.toContain("下次 ");
  });
});
