import { useEffect, useRef, useState } from "react";
import type { UnknownRecord, UserTaskDraft, UserTaskRun } from "../../types";
import { cancelUserTaskRun, loadUserTask, loadUserTaskRun, loadUserTaskRuns } from "../../api";
import { activeTaskRun, asTaskRecord, formatTaskTime, latestTaskRun, taskLink, taskProgress } from "../../taskCenter";
import UserTaskCard from "../UserTaskCard";

type TaskObservation = { task: UserTaskDraft; run: UserTaskRun | null };

/** Sequential refreshes stop once this run is terminal; the caller owns cleanup. */
export function observeUserTask(taskId: string, runId: string, onUpdate: (value: TaskObservation) => void, onError: () => void) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let observedRunId = runId;
  let running = false;
  const refresh = async () => {
    try {
      const [task, run] = await Promise.all([
        loadUserTask(taskId, controller.signal),
        observedRunId ? loadUserTaskRun(observedRunId, controller.signal)
          : loadUserTaskRuns(taskId, controller.signal).then(runs => latestTaskRun(taskId, runs) || null),
      ]);
      if (controller.signal.aborted) return;
      if (run && (run.task_id || run.schedule_id) !== taskId) throw new Error("任务运行不匹配");
      observedRunId = run?.run_id || observedRunId;
      running = Boolean(run && activeTaskRun(run));
      onUpdate({ task, run });
    } catch {
      if (controller.signal.aborted) return;
      onError();
    }
    if (!controller.signal.aborted && running) timer = setTimeout(() => void refresh(), 5000);
  };
  void refresh();
  return () => { controller.abort(); clearTimeout(timer); };
}

/** The receipt anchors identity; authenticated task APIs supply the current facts. */
export default function UserTaskArtifact({ data }: { data: UnknownRecord }) {
  const savedTask = asTaskRecord(data.task);
  const savedRun = asTaskRecord(data.run);
  const taskId = String(savedTask.task_id || savedRun.task_id || savedRun.schedule_id || "");
  const runId = String(savedRun.run_id || data.run_id || "");
  const receiptRun = savedRun.status ? savedRun as unknown as UserTaskRun : null;
  const receiptTask: UserTaskDraft & { task_id: string } = { ...savedTask, task_id: taskId, requirement_brief: String(savedTask.requirement_brief || savedRun.requirement_brief || "后台任务"), execution_plan: { steps: [] } };
  const [current, setCurrent] = useState<TaskObservation | null>(null);
  const [updateFailed, setUpdateFailed] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!taskId || stopping) return;
    return observeUserTask(taskId, runId, value => { setCurrent(value); setUpdateFailed(false); }, () => setUpdateFailed(true));
  }, [taskId, runId, stopping]);
  const task = current?.task || receiptTask;
  const run = current ? current.run : receiptRun;
  const operation = String(data.operation || "");
  const progress = run ? taskProgress(run) : null;
  const stop = async () => {
    if (!run || stopping) return;
    setStopping(true); setStopError("");
    try {
      const stopped = await cancelUserTaskRun(run.run_id);
      if (mounted.current) setCurrent({ task, run: stopped });
    } catch {
      if (mounted.current) setStopError("停止请求未提交，请重试或打开任务查看。");
    } finally {
      if (mounted.current) setStopping(false);
    }
  };
  return <div className="task-receipt">
    <UserTaskCard task={task} run={run} statusLoading={!current && !run} actions={taskId ? <>
      <a className="schedule-quiet" href={taskLink(taskId, run?.run_id || runId)}>{run?.status === "completed" ? "查看结果" : "查看任务"}</a>
      {current && run && activeTaskRun(run) && <button type="button" className="schedule-quiet" disabled={stopping || Boolean(run.cancel_requested_at)} onClick={() => void stop()}>{stopping ? "正在提交…" : run.cancel_requested_at ? "正在停止" : "停止本次"}</button>}
    </> : undefined}>
      {updateFailed && <p className="task-receipt-observed" role="status">状态暂未更新，可打开任务查看。{!current && data.recorded_at ? ` 保留 ${formatTaskTime(String(data.recorded_at))} 的记录。` : ""}</p>}
      {stopError && <p className="task-fetch-error" role="alert">{stopError}</p>}
      {!current && operation === "task_submit" && !run && <p>{updateFailed ? "任务已提交，执行记录会保留。" : "任务已提交，正在读取执行进度。"}</p>}
      {run?.summary ? <p>{run.summary}</p> : progress?.message ? <p>{progress.message}</p> : null}
      {run?.error_text && <p className="task-fetch-error">{run.error_text}</p>}
    </UserTaskCard>
  </div>;
}
