import { Activity, Braces, CheckCircle2, Clock3, Play, RotateCcw, Save, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { runCustomToolInteractiveTest, type CustomToolInteractiveTest } from "../../api";
import type { UnknownRecord } from "../../types";
import JsonBlock from "../JsonBlock";

const record = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value)
  ? value as UnknownRecord
  : {};

const schemaType = (schema: UnknownRecord): string => {
  const value = schema.type;
  if (Array.isArray(value)) return String(value.find((item) => item !== "null") || "string");
  return String(value || (schema.properties ? "object" : "string"));
};

const draftValue = (value: unknown, type: string): string => {
  if (value === undefined || value === null) return "";
  if (type === "array" && Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return value.join("\n");
  }
  if (type === "object" || type === "array") return JSON.stringify(value, null, 2);
  return String(value);
};

const draftsFrom = (schema: UnknownRecord, source: UnknownRecord): Record<string, string> => {
  const properties = record(schema.properties);
  return Object.fromEntries(Object.entries(properties).map(([name, rawField]) => {
    const field = record(rawField);
    const value = Object.prototype.hasOwnProperty.call(source, name)
      ? source[name]
      : field.default;
    return [name, draftValue(value, schemaType(field))];
  }));
};

const parseArrayStrings = (value: string): string[] => Array.from(new Set(
  value.split(/[\n,，]+/).map((item) => item.trim()).filter(Boolean),
));

function parseArguments(
  schema: UnknownRecord,
  drafts: Record<string, string>,
): { value?: UnknownRecord; error?: string } {
  const properties = record(schema.properties);
  const required = new Set(Array.isArray(schema.required) ? schema.required.map(String) : []);
  const result: UnknownRecord = {};
  for (const [name, rawField] of Object.entries(properties)) {
    const field = record(rawField);
    const type = schemaType(field);
    const raw = String(drafts[name] || "").trim();
    if (!raw) {
      if (required.has(name)) return { error: `${String(field.title || name)} 为必填项。` };
      continue;
    }
    try {
      if (type === "integer") {
        const value = Number(raw);
        if (!Number.isInteger(value)) return { error: `${String(field.title || name)} 必须是整数。` };
        result[name] = value;
      } else if (type === "number") {
        const value = Number(raw);
        if (!Number.isFinite(value)) return { error: `${String(field.title || name)} 必须是数字。` };
        result[name] = value;
      } else if (type === "boolean") {
        result[name] = raw === "true";
      } else if (type === "array") {
        const items = record(field.items);
        result[name] = schemaType(items) === "string" ? parseArrayStrings(raw) : JSON.parse(raw);
      } else if (type === "object") {
        result[name] = JSON.parse(raw);
      } else {
        result[name] = raw;
      }
    } catch {
      return { error: `${String(field.title || name)} 不是合法的 ${type === "object" ? "JSON 对象" : "JSON 数组"}。` };
    }
  }
  return { value: result };
}

function ContractState({ label, value }: { label: string; value: unknown }) {
  const pass = value === true;
  const skipped = value === null || value === undefined;
  return <span className={skipped ? "skipped" : pass ? "pass" : "fail"}>
    {skipped ? <Clock3 size={12} /> : pass ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
    {label}{skipped ? "未配置" : pass ? "通过" : "失败"}
  </span>;
}

export default function CustomToolTestWorkbench({
  toolName,
  displayName,
  revision,
  inputSchema,
  sampleInput,
}: {
  toolName: string;
  displayName: string;
  revision: number;
  inputSchema: UnknownRecord;
  sampleInput: UnknownRecord;
}) {
  const properties = useMemo(() => record(inputSchema.properties), [inputSchema]);
  const hasFields = Object.keys(properties).length > 0;
  const storageKey = `fin-agent:custom-tool-test:${toolName}:${revision}`;
  const initialSource = useMemo(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = JSON.parse(window.localStorage.getItem(storageKey) || "null");
        if (saved && typeof saved === "object" && !Array.isArray(saved)) return saved as UnknownRecord;
      } catch {
        // Ignore a broken local draft and fall back to the generated sample.
      }
    }
    return sampleInput;
  }, [sampleInput, storageKey]);
  const [drafts, setDrafts] = useState<Record<string, string>>(() => draftsFrom(inputSchema, initialSource));
  const [rawMode, setRawMode] = useState(!hasFields);
  const [rawText, setRawText] = useState(() => JSON.stringify(initialSource, null, 2));
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [test, setTest] = useState<CustomToolInteractiveTest | null>(null);

  if (!toolName || revision < 1 || !Object.keys(inputSchema).length) return null;

  const loadSource = (source: UnknownRecord) => {
    setDrafts(draftsFrom(inputSchema, source));
    setRawText(JSON.stringify(source, null, 2));
    setError("");
  };

  const currentArguments = (): { value?: UnknownRecord; error?: string } => {
    if (!rawMode) return parseArguments(inputSchema, drafts);
    try {
      const parsed = JSON.parse(rawText || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return { error: "测试输入顶层必须是 JSON 对象。" };
      }
      return { value: parsed as UnknownRecord };
    } catch {
      return { error: "测试输入不是合法 JSON。" };
    }
  };

  const saveDraft = () => {
    const parsed = currentArguments();
    if (!parsed.value) {
      setError(parsed.error || "输入无效。");
      return;
    }
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(parsed.value));
      setError("");
    } catch {
      setError("当前浏览器无法保存本地输入，但仍可直接运行测试。");
    }
  };

  const run = async () => {
    const parsed = currentArguments();
    if (!parsed.value) {
      setError(parsed.error || "输入无效。");
      return;
    }
    setRunning(true);
    setError("");
    setTest(null);
    try {
      const next = await runCustomToolInteractiveTest({
        toolName,
        revision,
        arguments: parsed.value,
      });
      setTest(next);
      try {
        window.localStorage.setItem(storageKey, JSON.stringify(parsed.value));
      } catch {
        // Local drafts are optional and must never change a successful test result.
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRunning(false);
    }
  };

  const diagnostics = record(test?.diagnostics);
  const contract = record(test?.contract);
  const process = Array.isArray(test?.process) ? test.process.map(record) : [];

  return <section className="custom-tool-test-workbench" aria-label={`${displayName} 交互测试台`}>
    <div className="implementation-section-heading">
      <div><Activity size={16} /><h4>交互测试台</h4></div>
      <small>固定测试候选版本 {revision}，不会启用或修改工具</small>
    </div>

    <div className="custom-tool-test-toolbar">
      <button type="button" onClick={() => loadSource(sampleInput)} disabled={running}><RotateCcw size={13} />载入生成样例</button>
      <button type="button" onClick={saveDraft} disabled={running}><Save size={13} />保存本地输入</button>
      {hasFields ? <button type="button" onClick={() => setRawMode((value) => !value)} disabled={running}><Braces size={13} />{rawMode ? "标准表单" : "JSON 模式"}</button> : null}
    </div>

    {rawMode ? <label className="custom-tool-test-raw">
      <span>测试输入 JSON</span>
      <textarea value={rawText} onChange={(event) => setRawText(event.target.value)} spellCheck={false} />
    </label> : <div className="custom-tool-test-fields">{Object.entries(properties).map(([name, rawField]) => {
      const field = record(rawField);
      const type = schemaType(field);
      const required = Array.isArray(inputSchema.required) && inputSchema.required.map(String).includes(name);
      const enums = Array.isArray(field.enum) ? field.enum : [];
      const label = String(field.title || name);
      const value = drafts[name] || "";
      return <label key={name} className={type === "object" || type === "array" ? "wide" : ""}>
        <span>{label}{required ? <em>必填</em> : <small>可选</small>}</span>
        {field.description ? <p>{String(field.description)}</p> : null}
        {enums.length ? <select value={value} onChange={(event) => setDrafts((current) => ({ ...current, [name]: event.target.value }))}>
          {!required ? <option value="">不填写</option> : null}
          {enums.map((item) => <option key={String(item)} value={String(item)}>{String(item)}</option>)}
        </select> : type === "boolean" ? <select value={value || "false"} onChange={(event) => setDrafts((current) => ({ ...current, [name]: event.target.value }))}>
          <option value="true">是</option><option value="false">否</option>
        </select> : type === "object" || type === "array" ? <textarea
          value={value}
          onChange={(event) => setDrafts((current) => ({ ...current, [name]: event.target.value }))}
          placeholder={type === "array" && schemaType(record(field.items)) === "string" ? "每行输入一个值，也支持逗号分隔" : "输入合法 JSON"}
          spellCheck={false}
        /> : <input
          type={type === "number" || type === "integer" ? "number" : String(field.format || "") === "date" ? "date" : "text"}
          step={type === "integer" ? "1" : type === "number" ? "any" : undefined}
          value={value}
          onChange={(event) => setDrafts((current) => ({ ...current, [name]: event.target.value }))}
          placeholder={String(field.example || (Array.isArray(field.examples) ? field.examples[0] : "") || "")}
        />}
      </label>;
    })}</div>}

    {error ? <div className="custom-tool-test-error" role="alert"><XCircle size={15} />{error}</div> : null}
    <button className="custom-tool-test-run" type="button" disabled={running} onClick={() => void run()}>
      <Play size={14} />{running ? "正在执行，请稍候…" : "运行这个测试用例"}
    </button>

    {test ? <div className={`custom-tool-test-result ${test.status}`} aria-live="polite">
      <div className="custom-tool-test-result-head">
        <div>{test.status === "passed" ? <CheckCircle2 size={18} /> : <XCircle size={18} />}<strong>{test.status === "passed" ? "测试通过" : "测试未通过"}</strong></div>
        <span>{test.elapsed_ms} ms</span>
      </div>
      <div className="custom-tool-test-contract">
        <ContractState label="输入" value={contract.input_valid} />
        <ContractState label="运行" value={contract.runtime_ok} />
        <ContractState label="业务" value={contract.business_ok} />
        <ContractState label="输出" value={contract.output_valid} />
      </div>
      <div className="custom-tool-test-metrics">
        <div><span>金融查询</span><strong>{String(diagnostics.finance_query_count ?? "—")}</strong></div>
        <div><span>桥接轮次</span><strong>{String(diagnostics.finance_bridge_rounds ?? "—")}</strong></div>
        <div><span>执行后端</span><strong>{String(diagnostics.backend || "—")}</strong></div>
        <div><span>运行 ID</span><strong>{test.run_id}</strong></div>
      </div>
      {test.error ? <div className="custom-tool-test-error"><XCircle size={15} />{test.error}</div> : null}
      <section className="custom-tool-test-process">
        <h5>中间过程</h5>
        <ol>{process.map((event, index) => <li key={String(event.sequence || index)} className={String(event.level || "info")}>
          <span>{String(event.sequence || index + 1)}</span>
          <div><strong>{String(event.message || event.content || event.stage || "执行事件")}</strong>{event.data ? <JsonBlock value={event.data} title="" compact /> : null}</div>
        </li>)}</ol>
      </section>
      <JsonBlock value={test.result} title="业务结果" />
      <details className="custom-tool-test-diagnostics"><summary>查看运行诊断</summary><JsonBlock value={diagnostics} title="" compact /></details>
    </div> : null}
  </section>;
}
