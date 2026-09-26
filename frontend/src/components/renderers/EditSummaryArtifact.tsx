import { AlertCircle, ArrowRight, CheckCircle2, LoaderCircle, Wrench } from "lucide-react";
import { useId } from "react";
import type { UnknownRecord } from "../../types";

const record = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value)
  ? value as UnknownRecord
  : {};

const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];

const firstDefined = (source: UnknownRecord, keys: string[]): unknown => {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(source, key) && source[key] != null) return source[key];
  }
  return undefined;
};

const displayValue = (value: unknown, fallback = "—"): string => {
  if (value == null || value === "") return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const routeLabel = (value: unknown): string => {
  const route = String(value || "").trim();
  const normalized = route.toLowerCase();
  if (normalized === "local_patch") return "局部修改";
  if (normalized === "full_revision") return "完整设计与实现";
  if (["light", "lightweight", "direct", "quick"].includes(normalized)) return "轻量修改";
  if (["full", "design", "redesign", "rebuild"].includes(normalized)) return "完整设计与实现";
  return route;
};

const affectedAssetLabel = (value: unknown): string => {
  const label = displayValue(value);
  const normalized = label.trim().toLowerCase();
  const labels: Record<string, string> = {
    metadata: "名称与说明",
    design: "设计说明",
    implementation: "工具实现",
    contract: "输入输出契约",
    tests: "验证样例",
    verification: "验证样例",
  };
  return labels[normalized] || label;
};

const operationLabel = (value: unknown): string => {
  const operation = displayValue(value, "");
  const labels: Record<string, string> = {
    add: "新增",
    create: "新增",
    update: "修改",
    replace: "替换",
    remove: "删除",
    delete: "删除",
  };
  return labels[operation.trim().toLowerCase()] || operation;
};

const statusTone = (value: unknown): "pass" | "fail" | "loading" | "unknown" => {
  const status = String(value || "").trim().toLowerCase();
  if (["ok", "pass", "passed", "success", "succeeded", "verified", "completed"].includes(status)) return "pass";
  if (["fail", "failed", "failure", "error", "blocked", "rejected"].includes(status)) return "fail";
  if (["running", "loading", "pending", "queued", "verifying", "in_progress"].includes(status)) return "loading";
  return "unknown";
};

function AffectedAsset({ value, index }: { value: unknown; index: number }) {
  const item = record(value);
  const rawLabel = typeof value === "object"
    ? firstDefined(item, ["display_name", "name", "asset_id", "id", "path"])
    : value;
  const label = rawLabel == null || rawLabel === ""
    ? `关联资产 ${index + 1}`
    : affectedAssetLabel(rawLabel);
  const detail = displayValue(firstDefined(item, ["description", "impact", "kind", "type"]), "");
  return <li><strong>{label}</strong>{detail ? <span>{detail}</span> : null}</li>;
}

function ChangeItem({ value, index }: { value: unknown; index: number }) {
  const item = record(value);
  if (!Object.keys(item).length) return <li className="edit-change-item"><strong>变更 {index + 1}</strong><p>{displayValue(value)}</p></li>;

  const title = displayValue(firstDefined(item, ["title", "field", "path", "name", "change"]), `变更 ${index + 1}`);
  const operation = operationLabel(firstDefined(item, ["operation", "action", "type"]));
  const summary = displayValue(firstDefined(item, ["summary", "description", "reason", "impact"]), "");
  const before = firstDefined(item, ["before", "from", "previous", "old_value"]);
  const after = firstDefined(item, ["after", "to", "candidate", "new_value"]);
  const hasDiff = before !== undefined || after !== undefined;

  return <li className="edit-change-item">
    <div className="edit-change-heading"><strong>{title}</strong>{operation ? <span>{operation}</span> : null}</div>
    {summary ? <p>{summary}</p> : null}
    {hasDiff ? <div className="edit-change-values">
      <div><span>修改前</span><code title={displayValue(before)}>{displayValue(before)}</code></div>
      <ArrowRight aria-hidden="true" size={15} />
      <div><span>候选版本</span><code title={displayValue(after)}>{displayValue(after)}</code></div>
    </div> : null}
  </li>;
}

function EvidenceDetails({ item }: { item: UnknownRecord }) {
  const entries = [
    ["输入", firstDefined(item, ["input", "inputs"])],
    ["预期", firstDefined(item, ["expected", "expectation"])],
    ["实际", firstDefined(item, ["actual", "output", "result"])],
  ].filter((entry) => entry[1] !== undefined) as [string, unknown][];
  if (!entries.length) return null;
  return <details><summary>查看输入与结果</summary><dl>{entries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd><pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre></dd></div>)}</dl></details>;
}

function VerificationCase({ value, index }: { value: unknown; index: number }) {
  const item = typeof value === "object" ? record(value) : { name: value };
  const tone = statusTone(item.status);
  const name = displayValue(firstDefined(item, ["name", "title", "case", "scenario", "test_id"]), `验证项 ${index + 1}`);
  const summary = displayValue(firstDefined(item, ["summary", "purpose", "message", "error"]), "");
  const label = tone === "pass" ? "通过" : tone === "fail" ? "失败" : tone === "loading" ? "进行中" : "待确认";
  return <li className={`edit-evidence-case ${tone}`}>
    <div><strong>{name}</strong><span>{label}</span></div>
    {summary ? <p>{summary}</p> : null}
    <EvidenceDetails item={item} />
  </li>;
}

export default function EditSummaryArtifact({ data }: { data: UnknownRecord }) {
  const instanceId = useId();
  const impactTitleId = `${instanceId}-impact`;
  const changesTitleId = `${instanceId}-changes`;
  const verificationTitleId = `${instanceId}-verification`;
  const verification = record(data.verification);
  const tone = statusTone(verification.status);
  const toolName = String(data.tool_name || "").trim();
  const displayName = String(data.display_name || toolName || "自定义工具").trim();
  const route = routeLabel(data.route);
  const affectedAssets = list(data.affected_assets);
  const changes = list(data.changes);
  const cases = list(verification.cases);
  const visibleChanges = changes.slice(0, 4);
  const remainingChanges = changes.slice(4);
  const visibleCases = cases.slice(0, 3);
  const remainingCases = cases.slice(3);
  const verificationTitle = tone === "pass"
    ? "验证通过"
    : tone === "fail"
      ? "验证未通过"
      : tone === "loading"
        ? "正在验证候选版本"
        : "验证状态待确认";
  const StatusIcon = tone === "pass" ? CheckCircle2 : tone === "loading" ? LoaderCircle : AlertCircle;
  const statusRole = tone === "fail" ? "alert" : "status";

  return <div className="edit-summary-artifact">
    <header className="edit-summary-hero">
      <div className="edit-summary-icon"><Wrench aria-hidden="true" size={19} /></div>
      <div>
        <div className="edit-summary-kicker"><span>候选修改</span>{route ? <small>{route}</small> : null}</div>
        <h3>{displayName}</h3>
        {toolName ? <code>${toolName}</code> : null}
      </div>
      <div className="edit-revision-track" aria-label="版本变化">
        <div><span>当前版本</span><strong>{displayValue(data.base_revision)}</strong></div>
        <ArrowRight aria-hidden="true" size={16} />
        <div><span>候选版本</span><strong>{displayValue(data.candidate_revision)}</strong></div>
      </div>
    </header>

    <section className="edit-impact-summary" aria-labelledby={impactTitleId}>
      <h4 id={impactTitleId}>本次修改</h4>
      <p>{String(data.impact_summary || "已生成候选版本，详细影响请查看下方变更与验证证据。")}</p>
      {affectedAssets.length ? <ul aria-label="影响范围">{affectedAssets.map((item, index) => <AffectedAsset value={item} index={index} key={index} />)}</ul> : <p className="edit-empty-note">未单独列出关联资产；修改范围以当前工具为准。</p>}
    </section>

    <section className="edit-changes" aria-labelledby={changesTitleId}>
      <div className="edit-section-heading"><h4 id={changesTitleId}>变更摘要</h4><span>{changes.length} 项</span></div>
      {changes.length ? <>
        <ol>{visibleChanges.map((item, index) => <ChangeItem value={item} index={index} key={index} />)}</ol>
        {remainingChanges.length ? <details className="edit-more-items"><summary>查看其余 {remainingChanges.length} 项变更</summary><ol>{remainingChanges.map((item, index) => <ChangeItem value={item} index={index + visibleChanges.length} key={index} />)}</ol></details> : null}
      </> : <p className="edit-empty-note">本轮没有可展示的逐项差异，候选版本仍保留完整修订记录。</p>}
    </section>

    <section className={`edit-verification ${tone}`} role={statusRole} aria-live={tone === "loading" ? "polite" : undefined} aria-labelledby={verificationTitleId}>
      <div className="edit-section-heading">
        <h4 id={verificationTitleId}><StatusIcon aria-hidden="true" className={tone === "loading" ? "spin" : ""} size={17} />{verificationTitle}</h4>
        {cases.length ? <span>{cases.length} 项证据</span> : null}
      </div>
      <p>{String(verification.summary || (tone === "loading" ? "测试仍在执行，完成前不会切换当前版本。" : "暂无验证汇总结论。"))}</p>
      {cases.length ? <>
        <ol>{visibleCases.map((item, index) => <VerificationCase value={item} index={index} key={index} />)}</ol>
        {remainingCases.length ? <details className="edit-more-items"><summary>查看其余 {remainingCases.length} 项验证证据</summary><ol>{remainingCases.map((item, index) => <VerificationCase value={item} index={index + visibleCases.length} key={index} />)}</ol></details> : null}
      </> : <p className="edit-empty-note">暂无逐项验证证据；当前只展示验证汇总结论。</p>}
    </section>

    <aside className="edit-candidate-notice" role="note">
      <strong>候选版本尚未生效</strong>
      <span>当前线上仍使用版本 {displayValue(data.base_revision)}；只有确认启用后才会切换到版本 {displayValue(data.candidate_revision)}。</span>
    </aside>
  </div>;
}
