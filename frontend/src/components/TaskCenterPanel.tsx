import { CalendarClock, Check, CirclePause, CirclePlay, ClipboardCopy, Clock3, LoaderCircle, Play, RefreshCw, Search, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  cancelUserTaskRun, createUserTask, loadUserTaskRun, loadUserTaskRuns, loadUserTasks,
  previewUserTask, runUserTask, updateUserTask,
} from "../api";
import type { UserTask, UserTaskDraft, UserTaskRun } from "../types";
import { activeTaskRun, formatTaskTime, matchesTask, readTaskLocation, taskKind, taskLink, taskRunLabel, taskSchedule } from "../taskCenter";
import TaskRunDetail from "./TaskRunDetail";

const requestId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const errorText = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const examples = [
  { label: "股票模型研究", text: "用近三年股票数据研究未来 3 和 7 个交易日的上涨概率，比较不同模型，保留其他公司和后续时段测试，考虑交易成本，最多尝试 12 个方案，最后给我训练、回测和泛化评估报告。" },
  { label: "周期分析", text: "每个工作日上午 9 点查询贵州茅台行情，然后生成一份简短分析。" },
];

function TaskPlan({ draft }: { draft: UserTaskDraft }) {
  return <ol className="task-plan-list">{draft.execution_plan.steps.map((step, index) => <li key={step.step_id}>
    <span className="task-step-number">{index + 1}</span>
    <div><strong>{step.target_ref.name}</strong><small>{step.depends_on.length ? `在 ${step.depends_on.join("、")} 完成后执行` : "直接执行"}</small>
      <details className="task-step-inputs"><summary>查看执行参数</summary><pre>{JSON.stringify(step.inputs, null, 2)}</pre></details>
    </div>
  </li>)}</ol>;
}

export default function TaskCenterPanel() {
  const initialLocation = useMemo(() => readTaskLocation(typeof window === "undefined" ? "" : window.location.search), []);
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<UserTaskDraft | null>(null);
  const [tasks, setTasks] = useState<UserTask[]>([]);
  const [selectedId, setSelectedId] = useState(initialLocation.taskId);
  const [selectedRunId, setSelectedRunId] = useState(initialLocation.runId);
  const [runs, setRuns] = useState<UserTaskRun[]>([]);
  const [runDetail, setRunDetail] = useState<UserTaskRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [runsLoading, setRunsLoading] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [runsError, setRunsError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [refresh, setRefresh] = useState(0);
  const createKeyRef = useRef("");
  const runKeyRef = useRef<{ taskId: string; key: string } | null>(null);
  const previewRequestRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const requestedTaskRef = useRef(initialLocation.taskId);
  const requestedRunRef = useRef(initialLocation.runId);
  const selectedIdRef = useRef(selectedId);
  const selectedRunRef = useRef(selectedRunId);
  selectedIdRef.current = selectedId;
  selectedRunRef.current = selectedRunId;
  const selected = tasks.find(task => task.task_id === selectedId) || null;
  const selectedRun = runDetail?.run_id === selectedRunId ? runDetail : runs.find(run => run.run_id === selectedRunId) || null;
  const filtered = tasks.filter(task => matchesTask(task, query, filter));

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; previewRequestRef.current?.abort(); };
  }, []);

  // Poll without overlapping requests; keep the last useful view on a transient failure.
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await loadUserTasks(controller.signal);
        if (controller.signal.aborted) return;
        setTasks(next);
        setSelectedId(current => next.some(task => task.task_id === current) ? current : next[0]?.task_id || "");
        if (requestedTaskRef.current && !next.some(task => task.task_id === requestedTaskRef.current)) {
          setNotice("链接中的任务不存在或不属于当前账户，已显示可访问的任务。");
        }
        requestedTaskRef.current = "";
        setListError("");
      } catch (reason) {
        if (!controller.signal.aborted) setListError(errorText(reason));
      } finally {
        if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(() => void poll(), 10000); }
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setRuns([]);
    setRunDetail(null);
    setRunsError("");
    setDetailError("");
    if (!selectedId) { setRunsLoading(false); return () => controller.abort(); }
    setRunsLoading(true);
    const poll = async () => {
      try {
        const next = await loadUserTaskRuns(selectedId, controller.signal);
        if (controller.signal.aborted) return;
        setRuns(current => {
          const previous = current.find(run => run.run_id === selectedRunRef.current);
          return previous && !next.some(run => run.run_id === previous.run_id) ? [...next, previous] : next;
        });
        setSelectedRunId(current => current || next[0]?.run_id || "");
        if (requestedRunRef.current && !next.some(run => run.run_id === requestedRunRef.current)) {
          // The requested run can be older than the latest 50 entries. Its endpoint still authorizes access.
          const requested = await loadUserTaskRun(requestedRunRef.current, controller.signal);
          if (controller.signal.aborted) return;
          if ((requested.task_id || requested.schedule_id) === selectedId) {
            setRuns(items => [requested, ...items.filter(item => item.run_id !== requested.run_id)]);
            setSelectedRunId(requested.run_id);
          } else { setNotice("链接中的运行记录不属于所选任务。"); setSelectedRunId(next[0]?.run_id || ""); }
        }
        requestedRunRef.current = "";
        setRunsError("");
      } catch (reason) {
        if (!controller.signal.aborted) {
          setRunsError(errorText(reason));
          if (requestedRunRef.current) setSelectedRunId("");
          requestedRunRef.current = "";
        }
      } finally {
        if (!controller.signal.aborted) { setRunsLoading(false); timer = setTimeout(() => void poll(), 5000); }
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [selectedId, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setRunDetail(null);
    if (!selectedRunId) return () => controller.abort();
    const poll = async () => {
      try {
        const next = await loadUserTaskRun(selectedRunId, controller.signal);
        if (controller.signal.aborted) return;
        if ((next.task_id || next.schedule_id) !== selectedId) return;
        setRunDetail(next);
        setDetailError("");
        if (activeTaskRun(next)) timer = setTimeout(() => void poll(), 3000);
      } catch (reason) {
        if (!controller.signal.aborted) { setDetailError(errorText(reason)); timer = setTimeout(() => void poll(), 5000); }
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [selectedRunId, selectedId, refresh]);

  useEffect(() => {
    if (selectedId) window.history.replaceState(null, "", taskLink(selectedId, selectedRunId || undefined));
  }, [selectedId, selectedRunId]);

  const changeInstruction = (text: string) => {
    previewRequestRef.current?.abort();
    setInstruction(text);
    setPreview(null);
    createKeyRef.current = "";
    if (busyAction === "preview") setBusyAction("");
  };

  const handlePreview = async () => {
    if (!instruction.trim() || busyAction) return;
    const controller = new AbortController();
    previewRequestRef.current = controller;
    setBusyAction("preview"); setError(""); setNotice("");
    try {
      const draft = await previewUserTask(instruction.trim(), controller.signal);
      if (controller.signal.aborted) return;
      setPreview(draft);
      createKeyRef.current = requestId();
    } catch (reason) {
      if (!controller.signal.aborted) { setPreview(null); setError(errorText(reason)); }
    } finally {
      if (!controller.signal.aborted && mountedRef.current) setBusyAction("");
    }
  };

  const handleCreate = async () => {
    if (!preview || busyAction) return;
    setBusyAction("create"); setError("");
    try {
      const task = await createUserTask({ instruction: instruction.trim(), draft: preview, idempotencyKey: createKeyRef.current || requestId() });
      if (!mountedRef.current) return;
      setTasks(items => [task, ...items.filter(item => item.task_id !== task.task_id)]);
      setSelectedId(task.task_id); setSelectedRunId(task.initial_run_id || "");
      setPreview(null); setInstruction(""); createKeyRef.current = "";
      setQuery(""); setFilter("all"); setRefresh(value => value + 1);
      setNotice(taskKind(task) === "immediate" ? "任务已提交。你可以离开此页面，稍后回到任务中心查看结果。" : "任务已安排，执行结果会保存在这里。");
    } catch (reason) { if (mountedRef.current) setError(errorText(reason)); }
    finally { if (mountedRef.current) setBusyAction(""); }
  };

  const handleToggle = async (task: UserTask) => {
    if (busyAction) return;
    setBusyAction(`toggle:${task.task_id}`); setError("");
    try {
      const next = await updateUserTask(task.task_id, { enabled: !task.enabled });
      if (mountedRef.current) setTasks(items => items.map(item => item.task_id === next.task_id ? next : item));
    } catch (reason) { if (mountedRef.current) setError(errorText(reason)); }
    finally { if (mountedRef.current) setBusyAction(""); }
  };

  const handleRun = async (task: UserTask) => {
    if (busyAction) return;
    if (runKeyRef.current?.taskId !== task.task_id) runKeyRef.current = { taskId: task.task_id, key: requestId() };
    setBusyAction(`run:${task.task_id}`); setError("");
    try {
      const run = await runUserTask(task.task_id, runKeyRef.current.key);
      if (!mountedRef.current) return;
      runKeyRef.current = null;
      if (selectedIdRef.current === task.task_id) setSelectedRunId(run.run_id);
      setRefresh(value => value + 1);
      setNotice("新一次运行已提交；之前的运行结果会保留。");
    } catch (reason) { if (mountedRef.current) setError(errorText(reason)); }
    finally { if (mountedRef.current) setBusyAction(""); }
  };

  const handleCancel = async (run: UserTaskRun) => {
    if (busyAction) return;
    setBusyAction(`cancel:${run.run_id}`); setError("");
    try {
      const next = await cancelUserTaskRun(run.run_id);
      if (!mountedRef.current) return;
      if (selectedRunRef.current === next.run_id) setRunDetail(next);
      setRuns(items => items.map(item => item.run_id === next.run_id ? next : item));
      setRefresh(value => value + 1);
    } catch (reason) { if (mountedRef.current) setError(errorText(reason)); }
    finally { if (mountedRef.current) setBusyAction(""); }
  };

  return <section className="schedule-workspace task-center" aria-label="任务中心">
    <div className="schedule-create-card">
      <div className="schedule-section-heading"><span className="schedule-heading-icon"><Sparkles size={18} /></span><div><h2>把复杂工作交给后台任务</h2><p>描述目标、要求和执行时间。先核对方案，再提交执行；进度与结果会持续保留。</p></div></div>
      <label className="schedule-instruction"><span>任务说明</span><textarea ref={composerRef} value={instruction} onChange={event => changeInstruction(event.target.value)} disabled={busyAction === "create"} placeholder="例如：研究未来 3/7 天的股票上涨概率，比较多种模型，保留其他公司测试，并输出回测报告。也可以提交批量分析、多步骤研究或定时任务。" maxLength={4000} /></label>
      <div className="task-examples" aria-label="任务示例">{examples.map(example => <button type="button" key={example.label} onClick={() => { changeInstruction(example.text); composerRef.current?.focus(); }} disabled={Boolean(busyAction)}>{example.label}</button>)}</div>
      <div className="schedule-create-actions"><small>{instruction.length}/4000 · 未指定时间即执行一次</small><button type="button" className="schedule-primary" disabled={!instruction.trim() || Boolean(busyAction)} onClick={() => void handlePreview()}>{busyAction === "preview" ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={16} />}生成执行方案</button></div>
      {preview && <div className="schedule-preview" aria-label="任务预览">
        <div className="schedule-preview-head"><div><strong>{preview.requirement_brief}</strong><span>{taskSchedule(preview)}</span></div><span className="schedule-next"><Clock3 size={14} />{preview.budget?.max_runtime_seconds ? `最长执行 ${Math.ceil(preview.budget.max_runtime_seconds / 60)} 分钟` : "执行预算见方案参数"}</span></div>
        <TaskPlan draft={preview} />
        <div className="schedule-preview-actions"><button type="button" className="schedule-quiet" onClick={() => { setPreview(null); composerRef.current?.focus(); }} disabled={Boolean(busyAction)}>调整说明</button><button type="button" className="schedule-primary" onClick={() => void handleCreate()} disabled={Boolean(busyAction)}>{busyAction === "create" ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{taskKind(preview) === "immediate" ? "创建并开始" : "确认安排"}</button></div>
      </div>}
    </div>
    {error && <div className="schedule-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError("")}>关闭</button></div>}
    {notice && <div className="task-notice task-feedback" role="status"><span>{notice}</span><button type="button" aria-label="关闭提示" onClick={() => setNotice("")}>×</button></div>}
    <div className="schedule-grid">
      <div className="schedule-list-card">
        <div className="schedule-card-title"><div><h2>我的任务</h2><span>{tasks.length} 项 · 自动更新</span></div><button type="button" className="schedule-icon-button" onClick={() => setRefresh(value => value + 1)} disabled={Boolean(busyAction)} aria-label="刷新任务"><RefreshCw size={16} /></button></div>
        <div className="task-list-tools"><label><Search size={14} /><input aria-label="搜索任务" placeholder="搜索目标或任务编号" value={query} onChange={event => setQuery(event.target.value)} /></label><select aria-label="按执行安排筛选" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部任务</option><option value="immediate">立即执行</option><option value="once">预约一次</option><option value="recurring">周期执行</option></select></div>
        {listError && <p className="task-fetch-error" role="alert">任务列表暂时无法更新：{listError}</p>}
        {loading ? <div className="schedule-state"><LoaderCircle size={22} className="spin" />正在加载任务</div> : !filtered.length ? <div className="schedule-state"><CalendarClock size={24} />{tasks.length ? "没有匹配的任务" : "还没有任务，在上方描述你要完成的工作"}</div> : <div className="schedule-task-list">{filtered.map(task => <button type="button" key={task.task_id} className={`schedule-task-item ${task.task_id === selectedId ? "active" : ""}`} aria-pressed={task.task_id === selectedId} onClick={() => { setSelectedId(task.task_id); setSelectedRunId(""); }}><span className={`schedule-status-dot ${task.enabled ? "enabled" : ""}`} /><span><strong>{task.requirement_brief}</strong><small>{taskSchedule(task)}</small>{taskKind(task) === "recurring" && <small>{task.enabled ? `下次 ${formatTaskTime(task.next_run_at)}` : "后续安排已暂停"}</small>}</span></button>)}</div>}
      </div>
      <div className="schedule-detail-card">
        {!selected ? <div className="schedule-state"><CalendarClock size={24} />选择任务查看方案、运行记录与结果</div> : <>
          <div className="schedule-card-title schedule-detail-title"><div><h2>{selected.requirement_brief}</h2><span>方案修订 #{selected.revision_no}</span></div><div className="schedule-detail-actions">
            {taskKind(selected) === "recurring" && <button type="button" className="schedule-quiet" onClick={() => void handleToggle(selected)} disabled={Boolean(busyAction)} title="仅影响后续周期安排，正在运行的任务可单独停止">{selected.enabled ? <CirclePause size={15} /> : <CirclePlay size={15} />}{selected.enabled ? "暂停后续安排" : "恢复后续安排"}</button>}
            <button type="button" className="schedule-primary" onClick={() => void handleRun(selected)} disabled={Boolean(busyAction)}>{busyAction === `run:${selected.task_id}` ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{runs.length ? "再次运行" : "立即运行"}</button>
          </div></div>
          <dl className="schedule-facts"><div><dt>执行安排</dt><dd title={taskSchedule(selected)}>{taskSchedule(selected)}</dd></div><div><dt>创建时间</dt><dd>{formatTaskTime(selected.created_at)}</dd></div><div><dt>执行预算</dt><dd>{selected.budget?.max_runtime_seconds ? `${Math.ceil(selected.budget.max_runtime_seconds / 60)} 分钟` : "见执行参数"}</dd></div><div><dt>任务编号</dt><dd title={selected.task_id}>{selected.task_id}</dd></div></dl>
          <details className="task-plan-details"><summary>执行方案 · {selected.execution_plan.steps.length} 步</summary><TaskPlan draft={selected} /><button type="button" className="schedule-quiet" disabled={Boolean(busyAction)} onClick={() => { changeInstruction(`${selected.requirement_brief}\n${taskSchedule(selected)}`); composerRef.current?.focus(); composerRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }}><ClipboardCopy size={14} />以此为基础创建新任务</button></details>
          <div className="schedule-runs-head"><h3 className="schedule-subtitle">运行记录</h3><small className="task-refresh-note">时间按设备时区显示 · 最近 50 次</small></div>
          {runsError && <p className="task-fetch-error" role="alert">运行记录暂时无法更新：{runsError}</p>}
          {runsLoading ? <p className="schedule-no-runs">正在加载运行记录…</p> : !runs.length ? <p className="schedule-no-runs">暂无运行记录{taskKind(selected) === "recurring" ? "，将按安排执行，也可立即运行。" : "。"}</p> : <div className="task-run-picker" aria-label="选择运行记录">{runs.map(run => <button type="button" className={run.run_id === selectedRunId ? "selected" : ""} key={run.run_id} onClick={() => setSelectedRunId(run.run_id)} aria-pressed={run.run_id === selectedRunId}><span className={`schedule-run-status ${run.status}`}>{taskRunLabel(run)}</span><span>{formatTaskTime(run.started_at || run.scheduled_for || run.created_at)}</span></button>)}</div>}
          {detailError && <p className="task-fetch-error" role="alert">运行详情暂时无法更新：{detailError}</p>}
          {selectedRun && <TaskRunDetail run={selectedRun} cancelling={Boolean(busyAction)} onCancel={run => void handleCancel(run)} />}
        </>}
      </div>
    </div>
  </section>;
}
