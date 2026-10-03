import { Download, LoaderCircle, Square } from "lucide-react";
import type { UserTaskRun } from "../types";
import {
  activeTaskRun, artifactSize, asTaskRecord, formatTaskTime, taskArtifactUrl,
  taskMetrics, taskOutputs, taskProgress, taskResultSummary, taskRunLabel,
} from "../taskCenter";
import MarkdownContent from "./MarkdownContent";

interface Props {
  run: UserTaskRun;
  cancelling?: boolean;
  embedded?: boolean;
  onCancel: (run: UserTaskRun) => void;
}

export default function TaskRunDetail({ run, cancelling, embedded, onCancel }: Props) {
  const active = activeTaskRun(run);
  const progress = taskProgress(run);
  const summary = taskResultSummary(run);
  const steps = Array.isArray(run.result?.steps) ? run.result.steps.map(asTaskRecord) : [];
  const outputs = taskOutputs(run).map(output => {
    const { value } = output;
    const domain = asTaskRecord(value.domain_result);
    const limitations = value.limitations ?? domain.limitations;
    return {
      ...output,
      metrics: taskMetrics(value.metrics),
      report: typeof value.report_markdown === "string" ? value.report_markdown : "",
      design: typeof value.design === "string" ? value.design : typeof domain.design === "string" ? domain.design : "",
      notes: Array.isArray(limitations) ? limitations.filter((item): item is string => typeof item === "string") : typeof limitations === "string" ? [limitations] : [],
    };
  });
  const hasReport = outputs.some(output => output.report || output.design || output.metrics.length || output.notes.length);
  const artifacts = (run.artifacts || []).flatMap(artifact => {
    const url = taskArtifactUrl(artifact.url || "");
    return url ? [{ ...artifact, url }] : [];
  });
  return (
    <article className="task-run-detail" aria-label="运行详情">
      {!embedded && <div className="task-run-heading">
        <div><span className={`schedule-run-status ${run.status}`}>{taskRunLabel(run)}</span></div>
        {active && <button type="button" className="schedule-quiet task-stop" disabled={cancelling || Boolean(run.cancel_requested_at)} onClick={() => onCancel(run)}>
          {cancelling || run.cancel_requested_at ? <LoaderCircle size={14} className="spin" /> : <Square size={14} />}
          {run.cancel_requested_at ? "停止请求已提交" : "停止本次运行"}
        </button>}
      </div>}
      {active && !run.cancel_requested_at && <div className="task-progress" role="status" aria-live="polite">
        {progress.stage && <strong className="task-progress-stage">{progress.stage}</strong>}
        <p>{run.status === "running" && <LoaderCircle size={15} className="spin" />}{progress.message}</p>
        {progress.work
          ? <small>已完成 {progress.work.completed} / {progress.work.total} 项工作</small>
          : progress.total !== undefined && <small>已完成 {progress.completed} / {progress.total} 个步骤</small>}
      </div>}
      {run.cancel_requested_at && active && <p className="task-notice" role="status">正在停止，已完成的结果会保留。</p>}
      {run.status === "failed" && <div className="task-run-error" role="alert"><strong>本次运行未完成</strong><p>已完成的结果会保留。可以再次运行，或展开下方记录查看原因。</p></div>}
      {summary && <div className="task-result-summary"><h3>{active ? "已有结果" : "结果摘要"}</h3><MarkdownContent content={summary} renderImages={false} /></div>}
      {hasReport && <details className="task-evidence task-report"><summary>查看完整报告</summary>
        {outputs.map(({ stepId, report, design, metrics, notes }) => <section className="task-step-result" key={stepId} aria-label={`步骤 ${stepId} 的结果`}>
          {outputs.length > 1 && <h3>步骤 {stepId}</h3>}
          {report && <MarkdownContent content={report} renderImages={false} />}
          {design && <details className="task-evidence"><summary>执行设计与分析</summary><MarkdownContent content={design} renderImages={false} /></details>}
          {metrics.length > 0 && <details className="task-evidence"><summary>相关指标</summary><dl className="task-metrics">{metrics.map((metric, index) => <div key={`${metric.label}-${index}`}><dt>{metric.label}</dt><dd>{metric.value}<small>{metric.unit}</small></dd></div>)}</dl></details>}
          {notes.length > 0 && <details className="task-evidence task-result-notes"><summary>结果说明与限制</summary><ul>{notes.map((note, index) => <li key={index}>{note}</li>)}</ul></details>}
        </section>)}
      </details>}
      {artifacts.length > 0 && <details className="task-evidence task-artifacts"><summary>交付文件 · {artifacts.length} 份</summary><ul>{artifacts.map(artifact => <li key={artifact.artifact_id}><a href={artifact.url} download><Download size={16} /><span>{artifact.name}<small>{artifactSize(artifact.size_bytes)}</small></span></a></li>)}</ul></details>}
      {!summary && !active && run.status !== "failed" && <p className="task-notice">{run.status === "cancelled" ? "本次运行已停止。" : "本次运行没有返回结果摘要，可在下方查看记录。"}</p>}
      <details className="task-evidence task-run-records"><summary>详情与记录</summary>
        <dl className="task-run-times">
          <div><dt>安排执行</dt><dd>{formatTaskTime(run.scheduled_for || run.created_at)}</dd></div>
          <div><dt>开始</dt><dd>{formatTaskTime(run.started_at)}</dd></div>
          <div><dt>结束</dt><dd>{formatTaskTime(run.finished_at)}</dd></div>
        </dl>
        <p className="task-notice">运行编号：{run.run_id}</p>
        {run.error_text && <div className="task-run-error"><strong>未完成原因</strong><p>{run.error_text}</p></div>}
        {!active && progress.message && <p className="task-notice">最后进展：{progress.message}</p>}
        {steps.length > 0 && <ol className="task-step-history">{steps.map((step, index) => <li key={String(step.step_id || index)}><strong>{String(step.step_id || `步骤 ${index + 1}`)}</strong><span>{taskRunLabel({ status: String(step.status || "completed") })}</span>{typeof step.error === "string" && <p>{step.error}</p>}</li>)}</ol>}
        {outputs.length > 0 && <details className="task-evidence"><summary>原始结果与验证依据</summary>{outputs.map(({ stepId, value }) => <pre key={stepId}>{JSON.stringify(value, (key, entry) => ["report_markdown", "artifacts"].includes(key) ? undefined : entry, 2)}</pre>)}</details>}
      </details>
    </article>
  );
}
