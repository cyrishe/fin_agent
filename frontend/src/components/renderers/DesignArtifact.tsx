import { CheckCircle2, ChevronRight, GitBranch } from "lucide-react";
import type { UnknownRecord } from "../../types";
import MarkdownContent from "../MarkdownContent";
import { authoritativeFlowObject } from "./AuthoritativeFlow";
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

function leadText(value: unknown): string {
  const lines = String(value || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#") && !line.startsWith("```") && !line.startsWith("|") && !/^[-*]\s/.test(line));
  const lead = String(lines[0] || value || "")
    .replace(/\*\*|__|`/g, "")
    .replace(/\[([^\]]+)\]\([^\)]+\)/g, "$1")
    .trim();
  return lead.length > 220 ? `${lead.slice(0, 217)}…` : lead;
}

function financeProfileLabel(profile: UnknownRecord): string {
  const family = ({
    information: "信息工具",
    analytics: "分析工具",
    strategy: "策略工具",
    action: "动作工具",
  } as Record<string, string>)[String(profile.family || "").trim().toLowerCase()] || "";
  const shape = ({
    aggregate_context: "整体分析",
    entity_local: "单实体独立",
    cross_sectional: "横截面比较",
    portfolio_stateful: "组合状态",
  } as Record<string, string>)[String(profile.execution_shape || "").trim().toLowerCase()] || "";
  const output = ({
    facts: "客观事实",
    metric: "指标值",
    series: "时间序列",
    assessment: "评估结论",
    ranked_selection: "排名筛选",
    signal: "投资信号",
    portfolio_target: "目标组合",
    action_receipt: "动作回执",
  } as Record<string, string>)[String(profile.output_semantic || "").trim().toLowerCase()] || "";
  const parts = [family, shape, output].filter(Boolean);
  if (parts.length > 1) return parts.join(" · ");
  const summary = String(profile.summary || "").trim();
  return summary.startsWith(family) ? summary : [family, summary].filter(Boolean).join(" · ");
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
  const financeProfile = record(details.finance_tool_profile || data.finance_tool_profile);
  const financeProfileText = financeProfileLabel(financeProfile);
  const plannedAction = String(financeProfile.family || "").trim().toLowerCase() === "action"
    && String(financeProfile.execution_policy || "").trim().toLowerCase() === "planned_non_executable";
  const understanding = record(details.understanding);
  const summary = String(data.summary || content || "");
  const goal = leadText(understanding.requirement_brief || understanding.goal || summary || details.document);
  const expected = leadText(understanding.expected_result || "");
  const confirmed = list(understanding.confirmed_requirements);
  const constraints = list(understanding.constraints);
  const assumptions = list(understanding.assumptions);
  const rules = records(details.rules);
  const document = String(details.document || "");
  const plan = String(details.plan || "");
  const modules = records(details.modules || data.modules);
  const inputs = records(details.inputs);
  const outputs = records(details.outputs);
  const dataRequirements = records(details.data_requirements);
  const acceptance = list(details.acceptance);
  const flow = record(details.flow);
  const mermaid = String(details.mermaid || "");
  const hasStructuredDetails = Boolean(
    inputs.length || outputs.length || modules.length || constraints.length ||
    assumptions.length || dataRequirements.length || acceptance.length || plan || document,
  );
  const flowObject = authoritativeFlowObject({ id: "design_core_flow", mermaid, flow });

  return <div className="design-artifact">
    <section className="design-intent">
        <span>需求与目标</span>
        <h3>{goal || "工具设计方案"}</h3>
        {expected && expected !== goal ? <p>{expected}</p> : null}
        {financeProfileText ? <div className="finance-tool-profile" aria-label="金融工具画像"><span>{financeProfileText}</span></div> : null}
        {plannedAction ? <p className="design-action-boundary" role="status">仅设计，未开放实现</p> : null}
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
      {rules.length > 4 ? <p className="design-more-note">其余 {rules.length - 4} 条规则可在完整设计中查看。</p> : null}
    </section> : null}

    <section className={`design-core-section design-flow-section${flowObject ? "" : " missing"}`}>
      <div className="design-section-heading"><span><GitBranch size={15} />核心流程</span><small>展示关键判断、分支条件与阈值</small></div>
      {flowObject
        ? <FlowRenderer object={flowObject} />
        : <div className="design-flow-missing" role="status"><GitBranch size={18} /><div><strong>流程图尚未生成</strong><p>当前设计缺少必须的主流程，不应进入确认或编码。</p></div></div>}
    </section>

    {hasStructuredDetails ? <details className="design-details">
      <summary><span><ChevronRight size={16} />查看完整设计细节</span><small>{inputs.length || outputs.length || modules.length ? `${inputs.length} 个输入 · ${outputs.length} 个输出 · ${modules.length} 个模块` : "完整规格、规则与数据口径"}</small></summary>
      <div className="design-details-body">
        {document ? <section className="design-document"><MarkdownContent content={document} /></section> : null}
        {summary && summary !== goal ? <p className="design-description">{summary}</p> : null}
        {plan ? <section><h4>实现方案</h4><p className="design-description" style={{ whiteSpace: "pre-wrap" }}>{plan}</p></section> : null}
        {inputs.length > 0 ? <section><h4>输入</h4><FieldGrid items={inputs} fallback="输入" /></section> : null}
        {outputs.length > 0 ? <section><h4>输出</h4><FieldGrid items={outputs} fallback="输出" /></section> : null}
        {modules.length > 0 ? <section><h4>内部模块</h4><div className="design-module-list">{modules.map((module, index) => (
          <article key={String(module.id || module.name || index)}><strong>{String(module.title || module.name || `模块 ${index + 1}`)}</strong><p>{String(module.responsibility || module.description || module.summary || "")}</p></article>
        ))}</div></section> : null}
        {(constraints.length > 0 || assumptions.length > 0) ? <section><h4>口径与假设</h4><ul>{[...constraints, ...assumptions].map((item, index) => <li key={index}>{textOf(item)}</li>)}</ul></section> : null}
        {dataRequirements.length > 0 ? <section><h4>数据依据</h4><ul>{dataRequirements.map((item, index) => <li key={index}><strong>{String(item.name || item.source_ref || `数据 ${index + 1}`)}</strong>：{String(item.purpose || item.description || "")}</li>)}</ul></section> : null}
        {acceptance.length > 0 ? <section><h4>验收场景</h4><ul>{acceptance.map((item, index) => <li key={index}>{textOf(item)}</li>)}</ul></section> : null}
      </div>
    </details> : null}
  </div>;
}
