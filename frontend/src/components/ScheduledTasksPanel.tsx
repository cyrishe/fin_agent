import {
  CalendarClock,
  Check,
  CirclePause,
  CirclePlay,
  Clock3,
  LoaderCircle,
  Play,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createScheduledTask,
  loadScheduledTaskRuns,
  loadScheduledTasks,
  previewScheduledTask,
  runScheduledTask,
  updateScheduledTask,
} from "../api";
import type { ScheduledTask, ScheduledTaskDraft, ScheduledTaskRun } from "../types";

const example = "每个工作日上午 9 点查询贵州茅台行情，然后生成一份简短分析";
const requestId = () => globalThis.crypto?.randomUUID?.()
  || `${Date.now()}-${Math.random().toString(36).slice(2)}`;

function formatTime(value?: string | null): string {
  if (!value) return "尚未安排";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("zh-CN", { hour12: false });
}

function targetName(step: ScheduledTaskDraft["execution_plan"]["steps"][number]): string {
  return step.target_ref?.name || "未知资产";
}

function runLabel(status: string): string {
  return {
    pending: "等待执行",
    running: "执行中",
    completed: "成功",
    failed: "失败",
  }[status] || status;
}

export default function ScheduledTasksPanel() {
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<ScheduledTaskDraft | null>(null);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [runs, setRuns] = useState<ScheduledTaskRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const createKeyRef = useRef("");

  const selected = tasks.find((task) => task.schedule_id === selectedId) || null;

  const refreshTasks = useCallback(async (preferredId = "") => {
    setLoading(true);
    try {
      const next = await loadScheduledTasks();
      setTasks(next);
      setSelectedId((current) => {
        const candidate = preferredId || current;
        return next.some((item) => item.schedule_id === candidate)
          ? candidate
          : next[0]?.schedule_id || "";
      });
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshRuns = useCallback(async (scheduleId: string) => {
    if (!scheduleId) {
      setRuns([]);
      return;
    }
    try {
      setRuns(await loadScheduledTaskRuns(scheduleId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => { void refreshTasks(); }, [refreshTasks]);
  useEffect(() => { void refreshRuns(selectedId); }, [refreshRuns, selectedId]);

  const handlePreview = async () => {
    const text = instruction.trim();
    if (!text || busyAction) return;
    setBusyAction("preview");
    setError("");
    try {
      setPreview(await previewScheduledTask(text));
      createKeyRef.current = requestId();
    } catch (reason) {
      setPreview(null);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyAction("");
    }
  };

  const handleCreate = async () => {
    if (!preview || busyAction) return;
    setBusyAction("create");
    setError("");
    try {
      const created = await createScheduledTask({
        instruction: instruction.trim(),
        draft: preview,
        idempotencyKey: createKeyRef.current || requestId(),
      });
      setPreview(null);
      createKeyRef.current = "";
      setInstruction("");
      await refreshTasks(created.schedule_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyAction("");
    }
  };

  const handleToggle = async (task: ScheduledTask) => {
    if (busyAction) return;
    setBusyAction(`toggle:${task.schedule_id}`);
    setError("");
    try {
      const updated = await updateScheduledTask(task.schedule_id, { enabled: !task.enabled });
      setTasks((current) => current.map((item) => item.schedule_id === updated.schedule_id ? updated : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyAction("");
    }
  };

  const handleRun = async (task: ScheduledTask) => {
    if (busyAction) return;
    setBusyAction(`run:${task.schedule_id}`);
    setError("");
    try {
      await runScheduledTask(task.schedule_id);
      await refreshRuns(task.schedule_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyAction("");
    }
  };

  return (
    <section className="schedule-workspace" aria-label="定时任务">
      <div className="schedule-create-card">
        <div className="schedule-section-heading">
          <span className="schedule-heading-icon"><Sparkles size={18} /></span>
          <div>
            <h2>用自然语言创建任务</h2>
            <p>Fin Agent 会先生成可核对的时间和执行步骤，确认后才保存。</p>
          </div>
        </div>
        <label className="schedule-instruction">
          <span>任务说明</span>
          <textarea
            value={instruction}
            onChange={(event) => {
              setInstruction(event.target.value);
              if (preview) {
                setPreview(null);
                createKeyRef.current = "";
              }
            }}
            placeholder={example}
            maxLength={4000}
          />
        </label>
        <div className="schedule-create-actions">
          <small>{instruction.length}/4000</small>
          <button type="button" className="schedule-primary" disabled={!instruction.trim() || Boolean(busyAction)} onClick={() => void handlePreview()}>
            {busyAction === "preview" ? <LoaderCircle className="spin" size={16} /> : <CalendarClock size={16} />}
            生成预览
          </button>
        </div>
        {preview && (
          <div className="schedule-preview" aria-label="任务预览">
            <div className="schedule-preview-head">
              <div><strong>{preview.requirement_brief}</strong><span>{preview.trigger.timezone} · {preview.trigger.cron}</span></div>
              <span className="schedule-next"><Clock3 size={14} />下次 {formatTime(preview.next_run_at)}</span>
            </div>
            <ol className="schedule-step-list">
              {preview.execution_plan.steps.map((step) => (
                <li key={step.step_id}>
                  <span>{step.type === "tool" ? "Tool" : "Skill"}</span>
                  <strong>{targetName(step)}</strong>
                  {step.depends_on.length > 0 && <small>依赖 {step.depends_on.join("、")}</small>}
                </li>
              ))}
            </ol>
            <div className="schedule-preview-actions">
              <button type="button" className="schedule-quiet" onClick={() => { setPreview(null); createKeyRef.current = ""; }} disabled={Boolean(busyAction)}>重新描述</button>
              <button type="button" className="schedule-primary" onClick={() => void handleCreate()} disabled={Boolean(busyAction)}>
                {busyAction === "create" ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}
                确认创建
              </button>
            </div>
          </div>
        )}
      </div>

      {error && <div className="schedule-error" role="alert">{error}<button type="button" onClick={() => setError("")}>关闭</button></div>}

      <div className="schedule-grid">
        <div className="schedule-list-card">
          <div className="schedule-card-title">
            <div><h2>我的定时任务</h2><span>{tasks.length} 个</span></div>
            <button type="button" className="schedule-icon-button" onClick={() => void refreshTasks()} disabled={loading || Boolean(busyAction)} aria-label="刷新任务"><RefreshCw size={16} /></button>
          </div>
          {loading ? (
            <div className="schedule-state"><LoaderCircle className="spin" size={22} />正在加载</div>
          ) : tasks.length === 0 ? (
            <div className="schedule-state"><CalendarClock size={24} />还没有定时任务</div>
          ) : (
            <div className="schedule-task-list">
              {tasks.map((task) => (
                <button type="button" key={task.schedule_id} className={`schedule-task-item ${task.schedule_id === selectedId ? "active" : ""}`} onClick={() => setSelectedId(task.schedule_id)}>
                  <span className={`schedule-status-dot ${task.enabled ? "enabled" : ""}`} />
                  <span><strong>{task.requirement_brief}</strong><small>{task.trigger.timezone} · {task.trigger.cron}</small></span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="schedule-detail-card">
          {!selected ? (
            <div className="schedule-state"><CalendarClock size={24} />选择任务查看详情</div>
          ) : (
            <>
              <div className="schedule-card-title schedule-detail-title">
                <div><h2>{selected.requirement_brief}</h2><span>修订 #{selected.revision_no}</span></div>
                <div className="schedule-detail-actions">
                  <button type="button" className="schedule-quiet" onClick={() => void handleToggle(selected)} disabled={Boolean(busyAction)}>
                    {selected.enabled ? <CirclePause size={16} /> : <CirclePlay size={16} />}
                    {selected.enabled ? "暂停" : "启用"}
                  </button>
                  <button type="button" className="schedule-primary" onClick={() => void handleRun(selected)} disabled={Boolean(busyAction)}>
                    {busyAction === `run:${selected.schedule_id}` ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
                    立即运行
                  </button>
                </div>
              </div>
              <dl className="schedule-facts">
                <div><dt>计划</dt><dd>{selected.trigger.cron}</dd></div>
                <div><dt>时区</dt><dd>{selected.trigger.timezone}</dd></div>
                <div><dt>下次执行</dt><dd>{formatTime(selected.next_run_at)}</dd></div>
                <div><dt>状态</dt><dd>{selected.enabled ? "已启用" : "已暂停"}</dd></div>
              </dl>
              <h3 className="schedule-subtitle">执行步骤</h3>
              <ol className="schedule-step-list detail">
                {selected.execution_plan.steps.map((step) => (
                  <li key={step.step_id}><span>{step.type === "tool" ? "Tool" : "Skill"}</span><strong>{targetName(step)}</strong><small>{step.step_id}</small></li>
                ))}
              </ol>
              <div className="schedule-runs-head">
                <h3 className="schedule-subtitle">最近运行</h3>
                <button type="button" className="schedule-icon-button" onClick={() => void refreshRuns(selected.schedule_id)} aria-label="刷新运行记录"><RefreshCw size={15} /></button>
              </div>
              {runs.length === 0 ? <p className="schedule-no-runs">暂无运行记录</p> : (
                <div className="schedule-run-list">
                  {runs.map((run) => (
                    <div className="schedule-run-row" key={run.run_id}>
                      <span className={`schedule-run-status ${run.status}`}>{runLabel(run.status)}</span>
                      <span>{formatTime(run.started_at || run.scheduled_for || run.created_at)}</span>
                      {run.error_text && <small title={run.error_text}>查看错误</small>}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
