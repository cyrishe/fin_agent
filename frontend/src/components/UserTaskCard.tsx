import type { ReactNode } from "react";
import type { UserTask, UserTaskDraft, UserTaskRun } from "../types";
import { formatTaskTime, taskKind, taskRunLabel, taskSchedule } from "../taskCenter";

interface Props {
  task: UserTaskDraft;
  run?: UserTaskRun | null;
  statusLoading?: boolean;
  actions?: ReactNode;
  children?: ReactNode;
}

/** A shared view of saved task facts; loading and actions belong to its caller. */
export default function UserTaskCard({ task, run, statusLoading, actions, children }: Props) {
  const saved = task as Partial<UserTask>;
  const kind = taskKind(task);
  const hasSchedule = task.trigger !== undefined;
  const purpose = task.requirement_brief;
  const expandable = purpose.length > 60 || purpose.split("\n").length > 2;
  const status = statusLoading ? "状态待查看" : run ? taskRunLabel(run)
    : saved.enabled === false ? "后续已暂停"
      : saved.task_id ? kind === "immediate" ? "暂无运行记录" : "已安排" : "待确认";
  const nextSchedule = kind === "recurring"
    ? saved.enabled === false ? "后续已暂停" : task.next_run_at ? `下次 ${formatTaskTime(task.next_run_at)}` : ""
    : "";
  return <article className="user-task-card" aria-label="任务卡片">
    <div className="user-task-card-header">
      <span className="user-task-kind">{!hasSchedule ? "任务" : kind === "recurring" ? "周期任务" : kind === "once" ? "定时任务 · 一次" : "一次性任务"}</span>
      {status && <span className={`schedule-run-status ${run?.status || ""}`} role="status">{status}</span>}
    </div>
    <div className="user-task-purpose task-requirement">
      <h3 className={`task-requirement-overview${expandable ? " task-requirement-clamped" : ""}`}>{purpose}</h3>
      {expandable && <details className="task-requirement-full"><summary>查看完整任务要求</summary><p>{purpose}</p></details>}
    </div>
    <p className="user-task-schedule">{hasSchedule ? taskSchedule(task) : "执行安排见任务详情"}{nextSchedule && ` · ${nextSchedule}`}</p>
    {actions && <div className="user-task-actions">{actions}</div>}
    {children && <div className="user-task-content">{children}</div>}
  </article>;
}
