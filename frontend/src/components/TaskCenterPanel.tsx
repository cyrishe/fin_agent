import { CalendarClock, Check, CirclePause, CirclePlay, ClipboardCopy, LoaderCircle, Play, RefreshCw, Search, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  cancelUserTaskRun, createUserTask, loadUserTaskRun, loadUserTaskRuns, loadUserTasks, loadRecentUserTaskRuns,
  previewUserTask, runUserTask, updateUserTask,
} from "../api";
import type { UserTask, UserTaskDraft, UserTaskRun } from "../types";
import { activeTaskRun, formatTaskTime, latestTaskRun, matchesTask, readTaskLocation, taskKind, taskLink, taskRunLabel, taskSchedule } from "../taskCenter";
import TaskRunDetail from "./TaskRunDetail";
import UserTaskCard from "./UserTaskCard";

const requestId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const errorText = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const examples = [
  { label: "股票模型研究", text: "用近三年股票数据研究未来 3 和 7 个交易日的上涨概率，比较不同模型，保留其他公司和后续时段测试，考虑交易成本，最多尝试 12 个方案，最后给我训练、回测和泛化评估报告。" },
  { label: "周期分析", text: "每个工作日上午 9 点查询贵州茅台行情，然后生成一份简短分析。" },
];

export function TaskRequirement({ text, heading = false, step = false }: { text: string; heading?: boolean; step?: boolean }) {
  const expandable = text.length > 120 || text.split("\n").length > 2;
  const Tag = heading ? "h2" : step ? "p" : "strong";
  return <div className="task-requirement">
    <Tag className={`${step ? "task-step-brief" : "task-requirement-overview"}${expandable ? " task-requirement-clamped" : ""}`}>{text}</Tag>
    {expandable && <details className="task-requirement-full"><summary>查看完整任务要求</summary><p>{text}</p></details>}
  </div>;
}

export function TaskPlan({ draft }: { draft: UserTaskDraft }) {
  return <ol className="task-plan-list">{draft.execution_plan.steps.map((step, index) => <li key={step.step_id}>
    <span className="task-step-number">{index + 1}</span>
    <div><strong>{draft.preview?.steps?.find(item => item.step_id === step.step_id)?.display_name?.trim() || step.target_ref.name}</strong><small>{step.depends_on.length ? `在 ${step.depends_on.join("、")} 完成后执行` : "直接执行"}</small>
      {typeof step.inputs.requirement_brief === "string" && step.inputs.requirement_brief.trim() && step.inputs.requirement_brief.trim() !== draft.requirement_brief.trim() && <TaskRequirement text={step.inputs.requirement_brief} step />}
      <details className="task-step-inputs"><summary>查看执行参数</summary><pre>{JSON.stringify(step.inputs, null, 2)}</pre></details>
    </div>
  </li>)}</ol>;
}

export default function TaskCenterPanel() {
  const initialLocation = useMemo(() => readTaskLocation(typeof window === "undefined" ? "" : window.location.search), []);
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<UserTaskDraft | null>(null);
  const [tasks, setTasks] = useState<UserTask[]>([]);
  const [recentRuns, setRecentRuns] = useState<UserTaskRun[]>([]);
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
  const detailRef = useRef<HTMLDivElement>(null);
  const createdTaskRef = useRef("");
  const requestedTaskRef = useRef(initialLocation.taskId);
  const requestedRunRef = useRef(initialLocation.runId);
  const selectedIdRef = useRef(selectedId);
  const selectedRunRef = useRef(selectedRunId);
  selectedIdRef.current = selectedId;
  selectedRunRef.current = selectedRunId;
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
        const [taskResponse, runResponse] = await Promise.allSettled([
          loadUserTasks(controller.signal), loadRecentUserTaskRuns(controller.signal),
        ]);
        if (controller.signal.aborted) return;
        if (taskResponse.status === "rejected") throw taskResponse.reason;
        const next = taskResponse.value;
        setTasks(next);
        if (runResponse.status === "fulfilled") setRecentRuns(runResponse.value);
        setSelectedId(current => next.some(task => task.task_id === current) ? current : "");
        if (requestedTaskRef.current && !next.some(task => task.task_id === requestedTaskRef.current)) {
          setNotice("链接中的任务不存在或不属于当前账户，已显示可访问的任务。");
        }
        requestedTaskRef.current = "";
        setListError(runResponse.status === "rejected" ? "运行状态暂时无法更新，请稍后刷新。" : "");
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
        // A linked historical run may be outside the list endpoint's latest 50.
        setRuns(items => items.some(run => run.run_id === next.run_id) ? items : [...items, next]);
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
    window.history.replaceState(null, "", taskLink(selectedId || undefined, selectedId ? selectedRunId || undefined : undefined));
  }, [selectedId, selectedRunId]);

  useEffect(() => {
    if (!selectedId || createdTaskRef.current !== selectedId || !detailRef.current) return;
    createdTaskRef.current = "";
    detailRef.current.focus({ preventScroll: true });
    detailRef.current.scrollIntoView({ behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }, [selectedId, refresh]);

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
      createdTaskRef.current = task.task_id;
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
      setRecentRuns(items => items.map(item => item.run_id === next.run_id ? next : item));
      setRefresh(value => value + 1);
    } catch (reason) { if (mountedRef.current) setError(errorText(reason)); }
    finally { if (mountedRef.current) setBusyAction(""); }
  };

  const reuseTask = (task: UserTask) => {
    // Keep executable schedule syntax in the new instruction; the card uses a human display label.
    const schedule = task.trigger?.cron ? `执行时间：${task.trigger.cron}（${task.trigger.timezone || "Asia/Shanghai"}）`
      : task.trigger?.at ? `预约执行时间：${task.trigger.at}` : "立即执行一次";
    changeInstruction(`${task.requirement_brief}\n${schedule}`);
    composerRef.current?.focus();
    composerRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return <section className="schedule-workspace task-center task-center-simple" aria-label="任务中心">
    <div className="schedule-create-card">
      <div className="schedule-section-heading"><span className="schedule-heading-icon"><Sparkles size={18} /></span><div><h2>想交给我什么任务？</h2><p>用一句话说清要做什么、什么时候做。也可以直接在聊天中告诉我。</p></div></div>
      <label className="schedule-instruction"><span>任务说明</span><textarea ref={composerRef} value={instruction} onChange={event => changeInstruction(event.target.value)} disabled={busyAction === "create"} placeholder="例如：帮我研究未来 3 / 7 天的股票上涨机会，完成训练和回测后给我报告。或：每周一早上 9 点整理行业新闻。" maxLength={4000} /></label>
      <div className="schedule-create-actions"><div className="task-examples" aria-label="任务示例">{examples.map(example => <button type="button" key={example.label} onClick={() => { changeInstruction(example.text); composerRef.current?.focus(); }} disabled={Boolean(busyAction)}>{example.label}</button>)}</div><button type="button" className="schedule-primary" disabled={!instruction.trim() || Boolean(busyAction)} onClick={() => void handlePreview()}>{busyAction === "preview" ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={16} />}{busyAction === "preview" ? "正在理解…" : "整理成任务"}</button></div>
      {preview && <div className="task-preview-card" aria-label="任务预览">
        <UserTaskCard task={preview} actions={<>
          <button type="button" className="schedule-primary" onClick={() => void handleCreate()} disabled={Boolean(busyAction)}>{busyAction === "create" ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{taskKind(preview) === "immediate" ? "确认并开始" : "确认安排"}</button>
          <button type="button" className="schedule-quiet" onClick={() => { setPreview(null); composerRef.current?.focus(); }} disabled={Boolean(busyAction)}>用文字调整</button>
        </>}>
          <details className="task-evidence"><summary>查看执行细节</summary><TaskPlan draft={preview} />{preview.budget?.max_runtime_seconds && <p className="task-small-note">最长运行 {Math.ceil(preview.budget.max_runtime_seconds / 60)} 分钟</p>}</details>
        </UserTaskCard>
      </div>}
    </div>
    {error && <div className="schedule-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError("")}>关闭</button></div>}
    {notice && <div className="task-notice task-feedback" role="status"><span>{notice}</span><button type="button" aria-label="关闭提示" onClick={() => setNotice("")}>×</button></div>}
    <div className="task-list-heading"><h2>我的任务</h2><button type="button" className="schedule-icon-button" onClick={() => setRefresh(value => value + 1)} disabled={Boolean(busyAction)} aria-label="刷新任务"><RefreshCw size={16} /></button></div>
    {tasks.length > 1 && <details className="task-find"><summary>查找任务</summary><div className="task-list-tools"><label><Search size={14} /><input aria-label="搜索任务" placeholder="搜索任务目的" value={query} onChange={event => setQuery(event.target.value)} /></label><select aria-label="按执行安排筛选" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部任务</option><option value="immediate">一次性</option><option value="once">预约一次</option><option value="recurring">周期任务</option></select></div></details>}
    {listError && <p className="task-fetch-error" role="alert">{listError}</p>}
    {loading ? <div className="schedule-state"><LoaderCircle size={22} className="spin" />正在读取任务…</div> : !filtered.length ? <div className="schedule-state"><CalendarClock size={24} />{tasks.length ? "没有匹配的任务，试试其他描述。" : "还没有任务。直接在上方告诉我需要做什么。"}</div> : <div className="task-card-list">{filtered.map(task => {
      const expanded = task.task_id === selectedId;
      const currentRun = latestTaskRun(task.task_id, expanded
        ? [...runs.map(run => runDetail?.run_id === run.run_id ? runDetail : run), ...recentRuns.filter(run => !runs.some(item => item.run_id === run.run_id))]
        : recentRuns);
      const active = currentRun && activeTaskRun(currentRun);
      return <div key={task.task_id} ref={expanded ? detailRef : undefined} tabIndex={expanded ? -1 : undefined} aria-label={expanded ? "所选任务详情" : undefined}>
        <UserTaskCard task={task} run={currentRun} statusLoading={!currentRun && (!expanded || runsLoading || Boolean(runsError))} actions={<>
          <button type="button" className={expanded ? "schedule-quiet" : "schedule-primary"} aria-expanded={expanded} onClick={() => { setSelectedId(expanded ? "" : task.task_id); setSelectedRunId(""); }}>{expanded ? "收起" : active ? "查看进度" : currentRun?.status === "completed" ? "查看结果" : "查看任务"}</button>
          {active && <button type="button" className="schedule-quiet task-stop" onClick={() => void handleCancel(currentRun)} disabled={Boolean(busyAction) || Boolean(currentRun.cancel_requested_at)}>{currentRun.cancel_requested_at ? "正在停止…" : "停止本次"}</button>}
          {expanded && !active && <button type="button" className="schedule-quiet" onClick={() => void handleRun(task)} disabled={Boolean(busyAction) || runsLoading}>{busyAction === `run:${task.task_id}` ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{currentRun ? "再次运行" : "立即运行"}</button>}
          {taskKind(task) === "recurring" && <button type="button" className="schedule-quiet" onClick={() => void handleToggle(task)} disabled={Boolean(busyAction)}>{task.enabled ? <CirclePause size={15} /> : <CirclePlay size={15} />}{task.enabled ? "暂停后续" : "恢复后续"}</button>}
        </>}>
          {!expanded && active && <p className="task-small-note" aria-live="polite">{currentRun.progress?.stage ? `当前：${String(currentRun.progress.stage)}` : currentRun.status === "pending" ? "等待后台执行，你可以先去做其他事情。" : "正在后台处理，完成后可在这里查看结果。"}</p>}
          {expanded && <>
            {runsError && <p className="task-fetch-error" role="alert">运行记录暂时无法更新：{runsError}</p>}
            {runsLoading && <p className="task-small-note">正在读取进展…</p>}
            {detailError && <p className="task-fetch-error" role="alert">运行详情暂时无法更新：{detailError}</p>}
            {selectedRun && currentRun && selectedRun.run_id !== currentRun.run_id && <p className="task-small-note">正在查看历史运行 · {taskRunLabel(selectedRun)} · {formatTaskTime(selectedRun.created_at)}</p>}
            {selectedRun && <TaskRunDetail embedded run={selectedRun} cancelling={Boolean(busyAction)} onCancel={run => void handleCancel(run)} />}
            <details className="task-evidence task-management-details"><summary>历史与执行详情</summary>
              {runs.length > 0 && <label className="task-history-select">运行记录<select aria-label="选择运行记录" value={selectedRunId} onChange={event => setSelectedRunId(event.target.value)}>{runs.map(run => <option key={run.run_id} value={run.run_id}>{formatTaskTime(run.started_at || run.scheduled_for || run.created_at)} · {taskRunLabel(run)}</option>)}</select></label>}
              {!runsLoading && !runs.length && <p className="task-small-note">尚无运行记录。</p>}
              <TaskPlan draft={task} />
              <p className="task-small-note">任务编号：{task.task_id} · 方案版本 {task.revision_no}</p>
              {task.trigger?.cron && <p className="task-small-note">执行规则：{task.trigger.cron} · {task.trigger.timezone || "Asia/Shanghai"}</p>}
              <button type="button" className="schedule-quiet" disabled={Boolean(busyAction)} onClick={() => reuseTask(task)}><ClipboardCopy size={14} />按此要求新建</button>
            </details>
          </>}
        </UserTaskCard>
      </div>;
    })}</div>}
  </section>;
}
