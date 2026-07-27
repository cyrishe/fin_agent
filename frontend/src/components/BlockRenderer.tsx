import { AlertCircle, CheckCircle2, Circle, Clock3, PlayCircle } from "lucide-react";
import { lazy, Suspense } from "react";
import { chooseRenderer } from "../rendering/registry";
import { normalizeRenderObject } from "../rendering/normalize";
import type { InteractionDraft, InteractionFeedbackRequest, InteractionResponse, SurfaceBlock, UnknownRecord } from "../types";
import DataTable from "./DataTable";
import MarkdownContent from "./MarkdownContent";
import CodeArtifact from "./renderers/CodeArtifact";
import DesignArtifact from "./renderers/DesignArtifact";
import FlowRenderer from "./renderers/FlowRenderer";
import MetricStrip from "./renderers/MetricStrip";
import InteractionRenderer from "./renderers/InteractionRenderer";
import JsonBlock from "./JsonBlock";

const ChartBlock = lazy(() => import("./ChartBlock"));
const FinanceChart = lazy(() => import("./renderers/FinanceChart"));

interface Props {
  block: SurfaceBlock;
  interactionDrafts?: Record<string, InteractionDraft>;
  selectedInteractions?: Record<string, string>;
  submittedInteractions?: Set<string>;
  disabled?: boolean;
  onDraftChange?: (draft: InteractionDraft) => void;
  onRequestCustomAnswer?: (question: string) => void;
  onClearCustomAnswer?: (question: string) => void;
  onSubmitDraft?: (draft: InteractionDraft) => void;
  onInteraction?: (response: InteractionResponse, label: string, key: string) => void;
  onRequestFeedback?: (request: InteractionFeedbackRequest) => void;
  onSubmitFeedback?: () => void;
}

const record = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];

function Workflow({ data }: { data: UnknownRecord }) {
  const items = list(data.items).map(record);
  if (String(data.role || "") === "conversation_progress") {
    const status = String(data.status || "running");
    return (
      <div className={`coding-progress ${status}`}>
        <div className="coding-progress-head">
          <span className={status === "running" ? "spinner" : ""}>
            {status === "completed" ? <CheckCircle2 size={16} /> : status === "error" ? <AlertCircle size={16} /> : null}
          </span>
          <strong>{status === "completed" ? "实现与验证已完成" : status === "error" ? "实现遇到问题" : "正在实现工具"}</strong>
        </div>
        <div className="coding-progress-list">
          {items.map((item, index) => {
            const itemStatus = String(item.status || (index === items.length - 1 ? status : "completed"));
            return <div className={`coding-progress-item ${itemStatus}`} key={String(item.id || index)}>
              <span>{itemStatus === "completed" ? <CheckCircle2 size={14} /> : itemStatus === "error" ? <AlertCircle size={14} /> : <span className="pulse-dot" />}</span>
              <MarkdownContent content={String(item.summary || item.title || "")} />
            </div>;
          })}
        </div>
      </div>
    );
  }
  const currentIndex = items.findIndex((item) => item.status === "running");
  return (
    <div className="workflow-block">
      {Boolean(data.summary) && <p className="semantic-summary">{String(data.summary)}</p>}
      <div className="workflow-track">
        {items.map((item, index) => {
          const status = String(item.status || (index < currentIndex ? "completed" : "pending"));
          const Icon = status === "completed" ? CheckCircle2 : status === "running" ? PlayCircle : status === "error" ? AlertCircle : Circle;
          return (
            <div className={`workflow-step ${status}`} key={String(item.id || item.title || index)}>
              <Icon size={18} />
              <div><strong>{String(item.title || item.label || `步骤 ${index + 1}`)}</strong>{Boolean(item.message) && <span>{String(item.message)}</span>}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Artifact({ data, content }: { data: UnknownRecord; content: string }) {
  const summary = String(data.summary || content || "");
  const facts = list(data.items).map(record);
  const details = record(data.details);
  const understanding = record(details.understanding);
  const logic = list(details.logic || data.logic);
  const rules = list(details.rules).map(record);
  const modules = list(details.modules || data.modules || data.components || data.internal_modules).map(record);
  const inputs = list(details.inputs).map(record);
  const outputs = list(details.outputs).map(record);
  const confirmed = list(understanding.confirmed_requirements);
  const acceptance = list(details.acceptance);
  const flow = record(details.flow);
  const flowSteps = list(flow.steps).map(record);
  const flowLinks = list(flow.links).map(record);
  const flowObject = flowSteps.length ? normalizeRenderObject({
    block_id: "artifact_flow",
    block_type: "flowchart",
    data: {
      nodes: flowSteps.map((step, index) => ({
        id: String(step.id || step.step_id || `step_${index + 1}`),
        label: String(step.name || step.title || step.label || `步骤 ${index + 1}`),
        detail: String(step.description || step.detail || ""),
      })),
      edges: flowLinks.map((link) => ({
        from: String(link.from || link.source || ""),
        to: String(link.to || link.target || ""),
        label: String(link.label || link.condition || ""),
      })),
    },
  }) : null;
  const entries = Object.entries(data).filter(([key, value]) =>
    !["summary", "logic", "modules", "components", "internal_modules", "artifact_type", "details", "items"].includes(key) &&
    ["string", "number", "boolean"].includes(typeof value),
  );
  const textOf = (value: unknown): string => {
    if (value && typeof value === "object") {
      const item = record(value);
      if (item.scenario || item.expected) return [item.scenario, item.expected].filter(Boolean).map(String).join("：");
      if (item.case || item.behavior) return [item.case, item.behavior].filter(Boolean).map(String).join("：");
      if (item.name && item.logic) return `${String(item.name)}：${String(item.logic)}`;
      return String(item.description || item.summary || item.expression || item.rule || item.name || JSON.stringify(item));
    }
    return String(value || "");
  };
  const fieldCards = (items: UnknownRecord[], fallback: string) => items.map((item, index) => <article key={String(item.name || item.field || item.id || index)}><strong>{String(item.name || item.field || item.id || `${fallback} ${index + 1}`)}</strong><p>{String(item.description || item.purpose || item.type || "")}</p></article>);
  return (
    <div className="artifact-block">
      {summary && <MarkdownContent content={summary} />}
      {facts.length > 0 && <div className="fact-grid">{facts.map((item, index) => <div key={String(item.label || index)}><span>{String(item.label || `项目 ${index + 1}`)}</span><strong>{String(item.value ?? "—")}</strong></div>)}</div>}
      {confirmed.length > 0 && <section className="artifact-section"><h4>已确认要求</h4><ul>{confirmed.map((item, index) => <li key={index}>{textOf(item)}</li>)}</ul></section>}
      {inputs.length > 0 && <section className="artifact-section"><h4>输入</h4><div className="module-grid">{fieldCards(inputs, "输入")}</div></section>}
      {outputs.length > 0 && <section className="artifact-section"><h4>输出</h4><div className="module-grid">{fieldCards(outputs, "输出")}</div></section>}
      {logic.length > 0 && <section className="artifact-section"><h4>核心逻辑</h4><ol>{logic.map((item, index) => <li key={index}>{textOf(item)}</li>)}</ol></section>}
      {rules.length > 0 && <section className="artifact-section"><h4>判断规则</h4><ol>{rules.map((item, index) => <li key={String(item.id || index)}>{textOf(item)}</li>)}</ol></section>}
      {modules.length > 0 && <section className="artifact-section"><h4>模块设计</h4><div className="module-grid">{modules.map((module, index) => (
        <article key={String(module.id || module.module_id || module.name || index)}><strong>{String(module.title || module.name || module.module_id || `模块 ${index + 1}`)}</strong><p>{String(module.description || module.summary || module.responsibility || module.role || "")}</p></article>
      ))}</div></section>}
      {flowObject && <section className="artifact-section"><h4>处理流程</h4><FlowRenderer object={flowObject} /></section>}
      {acceptance.length > 0 && <section className="artifact-section"><h4>验收标准</h4><ul>{acceptance.map((item, index) => <li key={index}>{textOf(item)}</li>)}</ul></section>}
      {entries.length > 0 && <div className="fact-grid">{entries.map(([key, value]) => <div key={key}><span>{key.replaceAll("_", " ")}</span><strong>{String(value)}</strong></div>)}</div>}
    </div>
  );
}

function Assessment({ data, content }: { data: UnknownRecord; content: string }) {
  const status = String(data.overall || "unknown");
  const issues = list(data.issues).map((item) => typeof item === "object" ? record(item) : { summary: item });
  const tests = list(record(data.details).tests).map(record);
  return (
    <div className={`assessment-block ${status}`}>
      <div className="assessment-head">{status === "pass" ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}<strong>{status === "pass" ? "验证通过" : status === "fail" ? "未通过" : status === "warn" ? "需要关注" : "待确认"}</strong></div>
      <p>{String(data.summary || content || "")}</p>
      {issues.length > 0 && <ul>{issues.map((item, index) => <li key={index}>{String(item.summary || item.message || "")}</li>)}</ul>}
      {tests.length > 0 && <div className="assessment-tests">{tests.map((test, index) => {
        const testStatus = String(test.status || "unknown");
        const logs = list(test.logs).map(record);
        const actual = record(test.actual);
        const keyProcessInfo = record(test.key_process_info || actual.key_process_info);
        const businessResult = Object.fromEntries(Object.entries(actual).filter(([key]) => key !== "key_process_info"));
        return <article className={testStatus} key={String(test.name || index)}>
          <div><strong>{String(test.name || `样例 ${index + 1}`)}</strong><span>{testStatus}</span></div>
          {test.summary ? <p>{String(test.summary)}</p> : null}
          {Object.keys(keyProcessInfo).length > 0 ? <div className="key-process-info">
            <b>核心过程信息</b>
            <div className="key-process-grid">{Object.entries(keyProcessInfo).map(([key, value]) => <div key={key}><span>{key.replaceAll("_", " ")}</span>{value && typeof value === "object" ? <JsonBlock value={value} title="" compact /> : <strong>{String(value ?? "—")}</strong>}</div>)}</div>
          </div> : null}
          {(test.input || Object.keys(businessResult).length || logs.length) ? <details><summary>查看测试输入、完整结果与日志</summary>{test.input ? <JsonBlock value={test.input} title="输入" compact /> : null}{Object.keys(businessResult).length ? <JsonBlock value={businessResult} title="业务结果" compact /> : null}{logs.length ? <div><b>关键日志</b><ul>{logs.map((log, logIndex) => <li key={logIndex}>{log.message ? String(log.message) : <JsonBlock value={log} title={`日志 ${logIndex + 1}`} compact />}</li>)}</ul></div> : null}</details> : null}
        </article>;
      })}</div>}
    </div>
  );
}

export default function BlockRenderer(props: Props) {
  const { block } = props;
  const object = normalizeRenderObject(block);
  const renderer = chooseRenderer(object);
  const data = object.payload;
  let content = <MarkdownContent content={object.text || block.content || String(data.summary || "")} />;

  if (renderer === "artifact.code") content = <CodeArtifact object={object} />;
  else if (renderer === "data.table") content = <DataTable data={data} />;
  else if (renderer === "data.metrics") content = <MetricStrip object={object} />;
  else if (renderer === "data.chart") content = <Suspense fallback={<div className="block-skeleton">正在加载图表…</div>}><ChartBlock type={object.presentation.chartType || "line_chart"} data={data} /></Suspense>;
  else if (renderer === "finance.kline") content = <Suspense fallback={<div className="block-skeleton">正在加载 K 线…</div>}><FinanceChart object={object} mode="kline" /></Suspense>;
  else if (renderer === "finance.intraday") content = <Suspense fallback={<div className="block-skeleton">正在加载分时图…</div>}><FinanceChart object={object} mode="intraday" /></Suspense>;
  else if (renderer === "diagram.flow" || renderer === "diagram.hierarchy") content = <FlowRenderer object={object} />;
  else if (renderer === "workflow.steps") content = block.block_type === "status" ? <div className="status-line"><Clock3 size={15} />{block.content || String(data.summary || "处理中")}</div> : <Workflow data={data} />;
  else if (renderer === "artifact.spec") content = String(data.artifact_type || "") === "finance.tool_spec"
    ? <DesignArtifact data={record(data.content || data)} content={block.content || String(data.change_summary || "")} />
    : <Artifact data={record(data.content || data)} content={block.content || String(data.change_summary || "")} />;
  else if (renderer === "assessment.review") content = <Assessment data={data} content={block.content || ""} />;
  else if (renderer === "interaction.form") {
    const key = `${String(data.interaction_id || "interaction")}:${Number(data.subject_revision ?? data.expected_revision ?? 0)}`;
    content = <InteractionRenderer
      data={data}
      content={block.content || ""}
      draft={props.interactionDrafts?.[key]}
      selectedActionId={props.selectedInteractions?.[key]}
      disabled={props.disabled || props.submittedInteractions?.has(key)}
      onDraftChange={props.onDraftChange || (() => undefined)}
      onRequestCustomAnswer={props.onRequestCustomAnswer || (() => undefined)}
      onClearCustomAnswer={props.onClearCustomAnswer || (() => undefined)}
      onSubmitDraft={props.onSubmitDraft || (() => undefined)}
      onAction={props.onInteraction || (() => undefined)}
      onRequestFeedback={props.onRequestFeedback || (() => undefined)}
      onSubmitFeedback={props.onSubmitFeedback || (() => undefined)}
    />;
  }
  else if (renderer === "resource.list") {
    const resources = Array.isArray(data.resources) ? data.resources.map(record) : [];
    content = <div className="resource-list">{resources.length ? resources.map((resource, index) => <div key={String(resource.resource_id || index)}><div><strong>{String(resource.title || `资源 ${index + 1}`)}</strong><span>{String(resource.relation || resource.mime_type || "")}</span></div>{resource.uri ? <code>{String(resource.uri)}</code> : null}</div>) : <div className="empty-block">暂无可展示资源</div>}</div>;
  } else if (renderer === "fallback.structured") {
    content = <JsonBlock value={data} title="结构化数据" />;
  }

  const stage = String(block.stage || record(block.data).stage || record(block.meta).stage || "");
  return (
    <section className={`surface-block block-${object.kind} renderer-${renderer.replaceAll(".", "-")}${stage ? ` stage-${stage}` : ""}`} data-block-id={block.block_id} data-renderer={renderer}>
      {block.title && <div className="surface-title">{block.title}</div>}
      {content}
    </section>
  );
}
