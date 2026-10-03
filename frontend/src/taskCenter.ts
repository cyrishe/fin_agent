import { appPath } from "./appPath";
import type { UnknownRecord, UserTask, UserTaskDraft, UserTaskRun } from "./types";

export const asTaskRecord = (value: unknown): UnknownRecord => (
  value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {}
);

export function taskLink(taskId?: string, runId?: string): string {
  const params = new URLSearchParams({ view: "tasks" });
  if (taskId) params.set("task", taskId);
  if (runId) params.set("run", runId);
  return `${appPath("/")}?${params}`;
}

export function readTaskLocation(search: string): { open: boolean; taskId: string; runId: string } {
  const params = new URLSearchParams(search);
  return { open: params.get("view") === "tasks", taskId: params.get("task") || "", runId: params.get("run") || "" };
}

export function formatTaskTime(value?: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

export function taskKind(task: UserTaskDraft): "immediate" | "once" | "recurring" {
  return task.trigger?.cron ? "recurring" : task.trigger?.at ? "once" : "immediate";
}

export function taskSchedule(task: UserTaskDraft): string {
  if (task.trigger?.cron) return `周期执行 · ${task.trigger.cron} · ${task.trigger.timezone || "Asia/Shanghai"}`;
  if (task.trigger?.at) return `预约一次 · ${formatTaskTime(task.trigger.at)}`;
  return "立即执行一次";
}

export function matchesTask(task: UserTask, query: string, filter: string): boolean {
  if (filter !== "all" && taskKind(task) !== filter) return false;
  return !query.trim() || `${task.requirement_brief} ${task.task_id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
}

export const activeTaskRun = (run: UserTaskRun): boolean => ["pending", "running"].includes(run.status);

export function taskRunLabel(run: Pick<UserTaskRun, "status" | "cancel_requested_at">): string {
  if (run.cancel_requested_at && ["pending", "running"].includes(run.status)) return "正在停止";
  return ({ pending: "等待执行", running: "执行中", completed: "已完成", failed: "运行失败", cancelled: "已取消" } as Record<string, string>)[run.status] || run.status;
}

export function taskProgress(run: UserTaskRun): { message: string; completed?: number; total?: number } {
  const progress = asTaskRecord(run.progress);
  const completed = Number(progress.completed_steps);
  const total = Number(progress.total_steps);
  const counts = Number.isFinite(completed) && Number.isFinite(total) && total > 0
    ? { completed: Math.max(0, Math.min(completed, total)), total }
    : {};
  return {
    message: String(progress.message || (run.status === "pending"
      ? run.scheduled_for && new Date(run.scheduled_for).getTime() > Date.now()
        ? `将在 ${formatTaskTime(run.scheduled_for)} 执行。`
        : "已进入执行队列，等待后台执行。"
      : run.status === "running" ? "任务执行中，完成阶段后会更新进度。" : "")),
    ...counts,
  };
}

export function taskOutputs(run: UserTaskRun): { stepId: string; value: UnknownRecord }[] {
  const result = asTaskRecord(run.result);
  const steps = Array.isArray(result.steps) ? result.steps.map(asTaskRecord) : [];
  const outputs = asTaskRecord(result.outputs);
  const ids = [...new Set([...steps.map(step => String(step.step_id || "")).filter(Boolean), ...Object.keys(outputs)])];
  return ids.map(stepId => {
    const step = steps.find(item => item.step_id === stepId);
    const output = asTaskRecord(outputs[stepId]);
    const raw = step?.result ?? output.result ?? outputs[stepId];
    return { stepId, value: typeof raw === "string" ? { summary: raw } : asTaskRecord(raw) };
  }).filter(item => Object.keys(item.value).length > 0);
}

export function taskResultSummary(run: UserTaskRun): string {
  if (run.summary) return run.summary;
  const summaries = taskOutputs(run).map(item => item.value.summary).filter(value => typeof value === "string" && value.trim());
  return summaries.map(String).join("\n\n");
}

export function taskMetrics(value: unknown): { label: string; value: string; unit: string }[] {
  const source: UnknownRecord[] = Array.isArray(value) ? value.map(asTaskRecord)
    : Object.entries(asTaskRecord(value)).map(([label, metric]) => ({ label, value: metric }));
  return source.filter(metric => typeof metric.label === "string" && ["string", "number", "boolean"].includes(typeof metric.value))
    .map(metric => ({ label: String(metric.label), value: typeof metric.value === "number" ? metric.value.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : String(metric.value), unit: String(metric.unit || "") }));
}

/** Task downloads must pass through the authenticated run-artifact endpoint. */
export function taskArtifactUrl(value: string): string | undefined {
  if (!value.startsWith("/") || value.startsWith("//") || /[\\\r\n]/.test(value)) return undefined;
  const prefix = appPath("/api/task-runs/");
  const normalized = appPath(value);
  return normalized.startsWith(prefix) && /\/artifacts\//.test(normalized) ? normalized : undefined;
}

export function artifactSize(value?: number): string {
  if (value === undefined || !Number.isFinite(value)) return "";
  return value >= 1_048_576 ? `${(value / 1_048_576).toFixed(1)} MB` : `${Math.ceil(value / 1024)} KB`;
}
