import { CheckCircle2, ChevronRight, GitBranch } from "lucide-react";
import { normalizeRenderObject } from "../../rendering/normalize";
import type { UnknownRecord } from "../../types";
import FlowRenderer from "./FlowRenderer";

const record = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const records = (value: unknown): UnknownRecord[] => list(value).map(record);

function textOf(value: unknown): string {
  if (!value || typeof value !== "object") return String(value || "");
  const item = record(value);
  if (item.scenario || item.expected) return [item.scenario, item.expected].filter(Boolean).map(String).join("：");
  if (item.name && item.logic) return `${String(item.name)}：${String(item.logic)}`;
  return String(item.description || item.responsibility || item.summary || item.expression || item.rule || item.name || "");
}

function FieldGrid({ items, fallback }: { items: UnknownRecord[]; fallback: string }) {
  return <div className="design-field-grid">{items.map((item, index) => (
    <article key={String(item.name || item.field || item.id || index)}>
      <div><strong>{String(item.label || item.name || item.field || `${fallback} ${index + 1}`)}</strong><code>{String(item.type || "")}</code></div>
      <p>{String(item.description || item.purpose || "")}</p>
    </article>
  ))}</div>;
}

export default function DesignArtifact({ data, content }: { data: UnknownRecord; content: string }) {
  const details = record(data.details);
  const understanding = record(details.understanding);
  const summary = String(data.summary || content || "");
  const goal = String(understanding.goal || summary);
  const expected = String(understanding.expected_result || "");
  const confirmed = list(understanding.confirmed_requirements);
  const constraints = list(understanding.constraints);
  const assumptions = list(understanding.assumptions);
  const rules = records(details.rules);
  const modules = records(details.modules || data.modules);
  const inputs = records(details.inputs);
  const outputs = records(details.outputs);
  const dataRequirements = records(details.data_requirements);
  const acceptance = list(details.acceptance);
  const flow = record(details.flow);
  const flowSteps = records(flow.steps);
  const flowLinks = records(flow.links);
  const flowObject = flowSteps.length ? normalizeRenderObject({
    block_id: "design_core_flow",
    block_type: "data",
    kind: "data",
    semantic: "finance.tool_design_workflow",
    payload: {
      shape: "graph",
      content_type: "application/vnd.fin-agent.graph+json",
      data: {
        nodes: flowSteps.map((step, index) => ({
          id: String(step.id || step.step_id || `step_${index + 1}`),
          label: String(step.name || step.title || step.label || `步骤 ${index + 1}`),
          detail: String(step.description || step.detail || ""),
          status: String(step.type || "process"),
        })),
        edges: flowLinks.map((link) => ({
          from: String(link.from || link.source || ""),
          to: String(link.to || link.target || ""),
          label: String(link.label || link.condition || ""),
        })),
      },
    },
  }) : null;

  return <div className="design-artifact">
    <section className="design-intent">
      <span>需求理解</span>
      <h3>{goal}</h3>
      {expected && expected !== goal ? <p>{expected}</p> : null}
      {confirmed.length > 0 ? <div className="confirmed-points">{confirmed.map((item, index) => (
        <span key={index}><CheckCircle2 size={14} />{textOf(item)}</span>
      ))}</div> : null}
    </section>

    {rules.length > 0 ? <section className="design-core-section">
      <div className="design-section-heading"><span>核心判断</span><small>请重点确认这些规则是否符合你的想法</small></div>
      <div className="design-rule-list">{rules.slice(0, 4).map((rule, index) => (
        <article key={String(rule.id || rule.name || index)}>
          <span>{index + 1}</span>
          <div><strong>{String(rule.name || `规则 ${index + 1}`)}</strong><p>{String(rule.logic || rule.description || "")}</p></div>
        </article>
      ))}</div>
    </section> : null}

    {flowObject ? <section className="design-core-section design-flow-section">
      <div className="design-section-heading"><span><GitBranch size={15} />处理流程</span><small>从输入到结果的主链路</small></div>
      <FlowRenderer object={flowObject} />
    </section> : null}

    <details className="design-details">
      <summary><span><ChevronRight size={16} />查看完整设计细节</span><small>{inputs.length} 个输入 · {outputs.length} 个输出 · {modules.length} 个模块</small></summary>
      <div className="design-details-body">
        {summary && summary !== goal ? <p className="design-description">{summary}</p> : null}
        {inputs.length > 0 ? <section><h4>输入</h4><FieldGrid items={inputs} fallback="输入" /></section> : null}
        {outputs.length > 0 ? <section><h4>输出</h4><FieldGrid items={outputs} fallback="输出" /></section> : null}
        {modules.length > 0 ? <section><h4>内部模块</h4><div className="design-module-list">{modules.map((module, index) => (
          <article key={String(module.id || module.name || index)}><strong>{String(module.title || module.name || `模块 ${index + 1}`)}</strong><p>{String(module.responsibility || module.description || module.summary || "")}</p></article>
        ))}</div></section> : null}
        {(constraints.length > 0 || assumptions.length > 0) ? <section><h4>口径与假设</h4><ul>{[...constraints, ...assumptions].map((item, index) => <li key={index}>{textOf(item)}</li>)}</ul></section> : null}
        {dataRequirements.length > 0 ? <section><h4>数据依据</h4><ul>{dataRequirements.map((item, index) => <li key={index}><strong>{String(item.name || item.source_ref || `数据 ${index + 1}`)}</strong>：{String(item.purpose || item.description || "")}</li>)}</ul></section> : null}
        {acceptance.length > 0 ? <section><h4>验收场景</h4><ul>{acceptance.map((item, index) => <li key={index}>{textOf(item)}</li>)}</ul></section> : null}
      </div>
    </details>
  </div>;
}
