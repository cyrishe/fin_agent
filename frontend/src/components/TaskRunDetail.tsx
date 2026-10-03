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
  onCancel: (run: UserTaskRun) => void;
}

export default function TaskRunDetail({ run, cancelling, onCancel }: Props) {
  const progress = taskProgress(run);
  const outputs = taskOutputs(run);
  const summary = taskResultSummary(run);
  const steps = Array.isArray(run.result?.steps) ? run.result.steps.map(asTaskRecord) : [];
  const artifacts = (run.artifacts || []).flatMap(artifact => {
    const url = taskArtifactUrl(artifact.url || "");
    return url ? [{ ...artifact, url }] : [];
  });
  return (
    <article className="task-run-detail" aria-label="运行详情">
      <div className="task-run-heading">
        <div><span className={`schedule-run-status ${run.status}`}>{taskRunLabel(run)}</span><small>运行 {run.run_id}</small></div>
        {activeTaskRun(run) && <button type="button" className="schedule-quiet task-stop" disabled={cancelling || Boolean(run.cancel_requested_at)} onClick={() => onCancel(run)}>
          {cancelling || run.cancel_requested_at ? <LoaderCircle size={14} className="spin" /> : <Square size={14} />}
          {run.cancel_requested_at ? "停止请求已提交" : "停止本次运行"}
        </button>}
      </div>
      <dl className="task-run-times">
        <div><dt>安排执行</dt><dd>{formatTaskTime(run.scheduled_for || run.created_at)}</dd></div>
        <div><dt>开始</dt><dd>{formatTaskTime(run.started_at)}</dd></div>
        <div><dt>结束</dt><dd>{formatTaskTime(run.finished_at)}</dd></div>
      </dl>
      {(progress.message || progress.total) && <div className="task-progress" role="status" aria-live="polite">
        <p>{activeTaskRun(run) && <LoaderCircle size={15} className="spin" />}{progress.message}</p>
        {progress.total !== undefined && <><progress max={progress.total} value={progress.completed} aria-label="已完成执行步骤" /><small>已完成 {progress.completed} / {progress.total} 个执行步骤</small></>}
      </div>}
      {run.cancel_requested_at && activeTaskRun(run) && <p className="task-notice">正在等待执行进程停止，已完成的结果会保留。周期任务的后续安排不受此次停止影响。</p>}
      {run.error_text && <div className="task-run-error" role="alert"><strong>本次运行未完成</strong><p>{run.error_text}</p><small>下方保留已完成步骤与可用结果。可在任务顶部重新运行。</small></div>}
      {summary && <div className="task-result-summary"><h3>结果摘要</h3><MarkdownContent content={summary} renderImages={false} /></div>}
      {outputs.map(({ stepId, value }) => {
        const metrics = taskMetrics(value.metrics);
        const report = typeof value.report_markdown === "string" ? value.report_markdown : "";
        const stepSummary = typeof value.summary === "string" ? value.summary : "";
        const domain = asTaskRecord(value.domain_result);
        const design = typeof value.design === "string" ? value.design : typeof domain.design === "string" ? domain.design : "";
        const limitations = value.limitations ?? domain.limitations;
        const notes = Array.isArray(limitations) ? limitations.filter(item => typeof item === "string") : typeof limitations === "string" ? [limitations] : [];
        return <section className="task-step-result" key={stepId} aria-label={`步骤 ${stepId} 的结果`}>
          {outputs.length > 1 && <h3>步骤 {stepId}</h3>}
          {outputs.length > 1 && stepSummary && <MarkdownContent content={stepSummary} renderImages={false} />}
          {design && <details className="task-evidence"><summary>执行设计与分析</summary><MarkdownContent content={design} renderImages={false} /></details>}
          {metrics.length > 0 && <dl className="task-metrics">{metrics.map((metric, index) => <div key={`${metric.label}-${index}`}><dt>{metric.label}</dt><dd>{metric.value}<small>{metric.unit}</small></dd></div>)}</dl>}
          {notes.length > 0 && <details className="task-evidence task-result-notes" open><summary>结果说明与限制</summary><ul>{notes.map((note, index) => <li key={index}>{String(note)}</li>)}</ul></details>}
          {report && <details className="task-evidence"><summary>完整报告{outputs.length > 1 ? ` · ${stepId}` : ""}</summary><MarkdownContent content={report} renderImages={false} /></details>}
          <details className="task-evidence"><summary>结果与验证依据{outputs.length > 1 ? ` · ${stepId}` : ""}</summary><pre>{JSON.stringify(value, (key, entry) => ["report_markdown", "artifacts"].includes(key) ? undefined : entry, 2)}</pre></details>
        </section>;
      })}
      {artifacts.length > 0 && <section className="task-artifacts"><h3>交付文件</h3><ul>{artifacts.map(artifact => <li key={artifact.artifact_id}><a href={artifact.url} download><Download size={16} /><span>{artifact.name}<small>{artifactSize(artifact.size_bytes)}</small></span></a></li>)}</ul></section>}
      {steps.length > 0 && <details className="task-evidence"><summary>步骤记录 · {steps.length} 项</summary><ol className="task-step-history">{steps.map((step, index) => <li key={String(step.step_id || index)}><strong>{String(step.step_id || `步骤 ${index + 1}`)}</strong><span>{taskRunLabel({ status: String(step.status || "completed") })}</span>{typeof step.error === "string" && <p>{step.error}</p>}</li>)}</ol></details>}
      {!summary && !outputs.length && !activeTaskRun(run) && <p className="task-notice">{run.status === "cancelled" ? "本次运行已停止。" : "本次运行没有返回可展示的业务结果。"}</p>}
    </article>
  );
}
