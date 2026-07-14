import { Activity, CheckCircle2, ChevronRight, Circle, Clock3, PanelRightClose, Terminal, XCircle } from "lucide-react";
import type { AgentRun, SurfaceBlock } from "../types";

function processLabel(block: SurfaceBlock): string {
  return block.title || block.content || String(block.data?.summary || block.data?.message || "Agent 正在处理");
}

export default function RunPanel({ run, onClose }: { run?: AgentRun; onClose?: () => void }) {
  return (
    <aside className="run-panel">
      <div className="run-panel-head"><div><Activity size={17} /><strong>运行过程</strong></div>{onClose && <button className="icon-button" onClick={onClose} aria-label="关闭运行面板"><PanelRightClose size={18} /></button>}</div>
      {!run ? <div className="run-empty"><div><Terminal size={21} /></div><strong>等待任务开始</strong><p>Agent 的阶段状态和精简过程会显示在这里，正式结果保留在主对话中。</p></div> : <div className="run-detail">
        <div className={`run-status-card ${run.status}`}>
          <div>{run.status === "done" ? <CheckCircle2 size={19} /> : run.status === "error" ? <XCircle size={19} /> : <Clock3 size={19} />}<span>{run.status === "done" ? "运行完成" : run.status === "error" ? "运行失败" : "正在运行"}</span></div>
          <p>{run.summary}</p>
        </div>
        <div className="run-section-label">实时步骤</div>
        <div className="process-list">
          {run.process.length ? run.process.map((block, index) => <div className="process-item" key={block.block_id}><div className="process-icon">{index === run.process.length - 1 && run.status === "running" ? <span className="spinner" /> : <CheckCircle2 size={15} />}</div><div><strong>{block.title || `步骤 ${index + 1}`}</strong><p>{processLabel(block)}</p></div></div>) : <div className="process-item muted"><Circle size={14} /><p>正在等待第一个运行事件…</p></div>}
        </div>
        <div className="run-section-label">本轮产物</div>
        <div className="artifact-index">{run.artifacts.length ? run.artifacts.map((block) => <div key={block.block_id}><span>{block.title || block.block_type}</span><ChevronRight size={14} /></div>) : <p>产物生成后会显示在主对话中</p>}</div>
      </div>}
    </aside>
  );
}
