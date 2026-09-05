import { CheckCircle2, Circle, Clock3, FileCode2, TerminalSquare, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import type { RenderObject } from "../../rendering/model";
import { normalizeCodeFiles, normalizeRuntime } from "../../rendering/normalize";
import CodeBlock from "../CodeBlock";

const statusLabel: Record<string, string> = {
  idle: "尚未执行", queued: "等待执行", running: "执行中", succeeded: "执行成功", failed: "执行失败", cancelled: "已取消",
};

export default function CodeArtifact({ object }: { object: RenderObject }) {
  const files = useMemo(() => normalizeCodeFiles(object), [object]);
  const runtime = useMemo(() => normalizeRuntime(object), [object]);
  const [activeFile, setActiveFile] = useState(files[0]?.id || "");
  const [panel, setPanel] = useState<"output" | "tests">("output");
  const selected = files.find((file) => file.id === activeFile) || files[0];
  const StatusIcon = runtime.status === "succeeded" ? CheckCircle2 : runtime.status === "failed" ? XCircle : runtime.status === "running" ? Clock3 : Circle;
  return <div className="code-artifact">
    <div className="code-file-tabs" role="tablist" aria-label="代码模块">
      {files.map((file) => <button role="tab" aria-selected={selected?.id === file.id} className={selected?.id === file.id ? "active" : ""} onClick={() => setActiveFile(file.id)} key={file.id}><FileCode2 size={13} />{file.name}{file.status && <small>{file.status}</small>}</button>)}
    </div>
    {selected ? <CodeBlock code={selected.content} language={selected.language} /> : <div className="empty-block">暂无内联代码，可通过关联资源查看。</div>}
    {(runtime.status !== "idle" || runtime.logs.length > 0 || runtime.stdout || runtime.stderr || runtime.tests.length > 0) && <div className="runtime-box">
      <div className="runtime-head"><div className={`runtime-status ${runtime.status}`}><StatusIcon size={15} /><strong>{statusLabel[runtime.status] || runtime.status}</strong>{runtime.durationMs != null && <span>{runtime.durationMs} ms</span>}</div><div className="runtime-tabs"><button className={panel === "output" ? "active" : ""} onClick={() => setPanel("output")}><TerminalSquare size={13} />输出</button><button className={panel === "tests" ? "active" : ""} onClick={() => setPanel("tests")}>测试 <span>{runtime.tests.length}</span></button></div></div>
      {panel === "output" ? <div className="runtime-output">
        {runtime.logs.map((log, index) => <div className="runtime-log" key={index}><span className={String(log.level || "info")}>{log.level || "info"}</span><code>{log.message}</code></div>)}
        {runtime.stdout && <pre className="stdout">{runtime.stdout}</pre>}
        {runtime.stderr && <pre className="stderr">{runtime.stderr}</pre>}
        {!runtime.logs.length && !runtime.stdout && !runtime.stderr && <div className="runtime-empty">暂无运行输出</div>}
      </div> : <div className="runtime-tests">{runtime.tests.length ? runtime.tests.map((test) => <div className={`runtime-test ${test.status}`} key={test.name}>{test.status === "passed" ? <CheckCircle2 size={15} /> : test.status === "failed" ? <XCircle size={15} /> : <Clock3 size={15} />}<div><strong>{test.name}</strong>{test.summary && <p>{test.summary}</p>}</div>{test.durationMs != null && <span>{test.durationMs} ms</span>}</div>) : <div className="runtime-empty">暂无测试结果</div>}</div>}
    </div>}
  </div>;
}
