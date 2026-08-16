import { AlertCircle, Check, CheckCircle2, Copy, FileCode2, GitBranch, Layers3, Play, ShieldCheck, Wrench } from "lucide-react";
import { useState } from "react";
import type { UnknownRecord } from "../../types";
import CodeBlock from "../CodeBlock";
import { authoritativeFlowObject } from "./AuthoritativeFlow";
import CustomToolTestWorkbench from "./CustomToolTestWorkbench";
import FlowRenderer from "./FlowRenderer";

const record = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value)
  ? value as UnknownRecord
  : {};
const list = (value: unknown): UnknownRecord[] => Array.isArray(value)
  ? value.filter((item): item is UnknownRecord => Boolean(item && typeof item === "object" && !Array.isArray(item)))
  : [];

const text = (value: unknown): string => {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  const item = record(value);
  return String(item.summary || item.message || item.reason || item.description || "");
};

const statusTone = (value: unknown): "pass" | "fail" | "pending" | "unknown" => {
  const status = String(value || "").trim().toLowerCase();
  if (["ok", "pass", "passed", "success", "succeeded", "supported", "compatible", "completed"].includes(status)) return "pass";
  if (["fail", "failed", "failure", "error", "blocked", "unsupported", "incompatible", "rejected"].includes(status)) return "fail";
  if (["running", "loading", "pending", "queued", "verifying", "in_progress"].includes(status)) return "pending";
  return "unknown";
};

const financeFamilyLabel = (family: string): string => ({
  information: "信息工具",
  analytics: "分析工具",
  strategy: "策略工具",
  action: "动作工具",
}[family] || "");

const executionShapeLabel = (shape: string): string => ({
  aggregate_context: "整体分析",
  entity_local: "单实体独立",
  cross_sectional: "横截面比较",
  portfolio_stateful: "组合状态",
}[shape] || "");

const outputSemanticLabel = (semantic: string): string => ({
  facts: "客观事实",
  metric: "指标值",
  series: "时间序列",
  assessment: "评估结论",
  ranked_selection: "排名筛选",
  signal: "投资信号",
  portfolio_target: "目标组合",
  action_receipt: "动作回执",
}[semantic] || "");

export interface ToolIdentitySelection {
  kind: "tool";
  name: string;
  displayName: string;
  description: string;
}

function FieldList({ title, items }: { title: string; items: UnknownRecord[] }) {
  if (!items.length) return null;
  return <section>
    <h4>{title}</h4>
    <div className="tool-contract-fields">{items.map((item, index) => <article key={String(item.name || item.module_id || index)}>
      <div><strong>{String(item.name || item.module_id || `${title} ${index + 1}`)}</strong>{item.required ? <em>必填</em> : null}</div>
      <p>{String(item.description || item.responsibility || item.role || item.entrypoint || item.type || "未补充说明")}</p>
    </article>)}</div>
  </section>;
}

function SourceFiles({ files }: { files: UnknownRecord[] }) {
  const available = files.filter((file) => String(file.content || file.source_code || file.code || "").trim());
  if (!available.length) return null;
  return <section className="implementation-source-files">
    <h4>实现代码</h4>
    <div>{available.map((file, index) => {
      const name = String(file.name || file.path || file.module_id || `module_${index + 1}.py`);
      return <details key={name}>
        <summary><FileCode2 size={14} /><span>{name}</span><small>按需查看</small></summary>
        <CodeBlock code={String(file.content || file.source_code || file.code || "")} language={String(file.language || "python")} />
      </details>;
    })}</div>
  </section>;
}

export default function ToolIdentityArtifact({
  data,
  onUseAsset,
}: {
  data: UnknownRecord;
  onUseAsset?: (asset: ToolIdentitySelection) => void;
}) {
  const asset = record(data.asset_ref);
  const details = record(data.details);
  const name = String(asset.name || "");
  const displayName = String(asset.display_name || name || "自定义工具");
  const description = String(asset.description || data.summary || "");
  const invocation = name ? `$${name}` : "";
  const active = String(data.lifecycle || "") === "active";
  const modules = list(details.modules);
  const files = list(details.files || data.files);
  const verification = record(details.verification || data.verification);
  const verificationTone = statusTone(verification.status || verification.overall);
  const financeProfile = record(details.finance_tool_profile || data.finance_tool_profile);
  const financeFamily = String(financeProfile.family || "").trim().toLowerCase();
  const financeFamilyText = financeFamilyLabel(financeFamily);
  const financeProfileSummary = text(financeProfile.summary).trim();
  const financeExecutionShapeText = executionShapeLabel(String(financeProfile.execution_shape || "").trim().toLowerCase());
  const financeOutputSemanticText = outputSemanticLabel(String(financeProfile.output_semantic || "").trim().toLowerCase());
  const financeProfileParts = [financeFamilyText, financeExecutionShapeText, financeOutputSemanticText].filter(Boolean);
  const financeProfileLabel = financeProfileParts.length > 1
    ? financeProfileParts.join(" · ")
    : financeProfileSummary.startsWith(financeFamilyText)
      ? financeProfileSummary
      : [financeFamilyText, financeProfileSummary].filter(Boolean).join(" · ");
  const hasFinanceProfile = Boolean(financeFamilyText || financeProfileSummary);
  const strategyProfile = financeFamily === "strategy";
  const plannedAction = financeFamily === "action"
    && String(financeProfile.execution_policy || "").trim().toLowerCase() === "planned_non_executable";
  const runtimeProfile = record(details.strategy_runtime_profile || data.strategy_runtime_profile);
  const compatibility = record(details.strategy_compatibility || data.strategy_compatibility || details.compatibility);
  const backtestCompatibility = record(
    compatibility.backtest
    || details.backtest_compatibility
    || data.backtest_compatibility,
  );
  const runtimeText = text(details.runtime) || text(runtimeProfile.summary);
  const compatibilityText = text(compatibility) || text(backtestCompatibility);
  const showStrategyCompatibility = strategyProfile || (!hasFinanceProfile && Boolean(compatibilityText));
  const strategyCompatibilityText = compatibilityText
    || (strategyProfile ? "尚未提供 Strategy Wrapper 与组合回测契约证据。" : "");
  const wrapperReady = typeof compatibility.strategy_wrapper_ready === "boolean"
    ? compatibility.strategy_wrapper_ready
    : undefined;
  const portfolioBacktestReady = typeof compatibility.portfolio_backtest_contract_ready === "boolean"
    ? compatibility.portfolio_backtest_contract_ready
    : undefined;
  const compatibilityStatus = backtestCompatibility.status
    || compatibility.status
    || (wrapperReady === true && portfolioBacktestReady === true
      ? "supported"
      : wrapperReady === true && portfolioBacktestReady === false
        ? "pending"
        : wrapperReady === false
          ? "unsupported"
          : "")
    || (typeof backtestCompatibility.supported === "boolean"
      ? (backtestCompatibility.supported ? "supported" : "unsupported")
      : "");
  const compatibilityTone = statusTone(compatibilityStatus);
  const compatibilityLabel = wrapperReady === true && portfolioBacktestReady === true
    ? "运行可用 · 回测契约已声明"
    : wrapperReady === true && portfolioBacktestReady === false
      ? "运行可用 · 组合回测受限"
      : compatibilityTone === "pass"
        ? "可衔接"
        : compatibilityTone === "fail"
          ? "运行契约待完善"
          : compatibilityTone === "pending"
            ? "待验证"
            : text(compatibilityStatus) || (strategyProfile ? "运行契约待完善" : "兼容性说明");
  const designFlow = details.design_flow || details.authoritative_design_flow || data.design_flow;
  const flowObject = authoritativeFlowObject({
    id: `implementation_design_flow_${name || "draft"}`,
    mermaid: details.design_mermaid || details.mermaid,
    flow: designFlow || details.flow,
  });
  const hasOverview = Boolean(
    modules.length
    || verificationTone !== "unknown"
    || runtimeText
    || (showStrategyCompatibility && strategyCompatibilityText),
  );
  const [copied, setCopied] = useState(false);
  const inputSchema = record(details.input_schema);
  const sampleInput = record(details.sample_input);
  const revision = Number(asset.revision || data.version || 0);

  const copyInvocation = async () => {
    const copyValue = plannedAction ? name : invocation;
    if (!copyValue) return;
    try {
      await navigator.clipboard.writeText(copyValue);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return <div className="tool-identity-artifact">
    <div className="tool-identity-hero">
      <div className="tool-identity-icon"><Wrench size={19} /></div>
      <div className="tool-identity-copy">
        <div className="tool-identity-status">
          <span className={plannedAction ? "planned" : active ? "active" : "draft"}>
            {plannedAction ? "仅设计" : active ? "已启用" : "待确认"}
          </span>
          <small>版本 {String(data.version || asset.revision || "—")}</small>
        </div>
        <h3>{displayName}</h3>
        <p>{description || "按照已确认的金融需求执行自定义分析。"}</p>
        {financeProfileLabel ? <div className="finance-tool-profile" aria-label="金融工具画像">
          <span>{financeProfileLabel}</span>
        </div> : null}
        {plannedAction ? <p className="tool-action-boundary" role="status">仅设计，未开放执行</p> : null}
      </div>
    </div>

    <div className="tool-invocation-row">
      <div><span>{plannedAction ? "资产标识" : "调用名"}</span><code>{plannedAction ? name || "尚未生成" : invocation || "尚未生成"}</code></div>
      <div className="tool-identity-actions">
        {(plannedAction ? name : invocation) ? <button type="button" onClick={() => void copyInvocation()}>
          {copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "已复制" : plannedAction ? "复制标识" : "复制"}
        </button> : null}
        {active && !plannedAction && name && onUseAsset ? <button className="primary" type="button" onClick={() => onUseAsset({
          kind: "tool",
          name,
          displayName,
          description,
        })}><Play size={14} />立即使用</button> : null}
      </div>
    </div>

    {hasOverview ? <section className="implementation-overview" aria-label="实现概览">
      <div className="implementation-section-heading"><div><Layers3 size={16} /><h4>实现概览</h4></div><small>先看结果与边界，细节按需展开</small></div>
      <div className="implementation-overview-grid">
        {modules.length ? <article><span><Layers3 size={14} />核心模块</span><strong>{modules.length} 个</strong><p>职责拆分见下方实现细节。</p></article> : null}
        {verificationTone !== "unknown" ? <article className={`verification-${verificationTone}`}>
          <span>{verificationTone === "pass" ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}验证状态</span>
          <strong>{verificationTone === "pass" ? "已通过" : verificationTone === "fail" ? "未通过" : "进行中"}</strong>
          <p>{text(verification) || "详细证据见本轮验证结果。"}</p>
        </article> : null}
        {runtimeText ? <article><span><ShieldCheck size={14} />运行边界</span><strong>由系统托管</strong><p>{runtimeText}</p></article> : null}
        {showStrategyCompatibility && strategyCompatibilityText ? <article className={`compatibility-${compatibilityTone}`}>
          <span><ShieldCheck size={14} />运行与回测</span><strong>{compatibilityLabel}</strong><p>{strategyCompatibilityText}</p>
        </article> : null}
      </div>
    </section> : null}

    {!plannedAction ? <CustomToolTestWorkbench
      toolName={name}
      displayName={displayName}
      revision={revision}
      inputSchema={inputSchema}
      sampleInput={sampleInput}
    /> : null}

    {flowObject ? <section className="implementation-flow-section">
      <div className="implementation-section-heading"><div><GitBranch size={16} /><h4>已确认的设计主流程</h4></div><small>沿用 Design 权威版本，不从代码重新推导</small></div>
      <FlowRenderer object={flowObject} />
    </section> : null}

    <details className="tool-contract-details">
      <summary>查看输入、输出、模块与代码</summary>
      <div>
        <FieldList title="输入" items={list(details.inputs)} />
        <FieldList title="输出" items={list(details.outputs)} />
        <FieldList title="内部模块" items={modules} />
        {runtimeText ? <p className="tool-runtime-note">运行方式：{runtimeText}</p> : null}
        {showStrategyCompatibility && strategyCompatibilityText ? <p className="tool-runtime-note">兼容性：{strategyCompatibilityText}</p> : null}
        <SourceFiles files={files} />
      </div>
    </details>
  </div>;
}
