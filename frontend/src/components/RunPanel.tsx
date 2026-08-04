import { Activity, CheckCircle2, ChevronRight, Clock3, PanelRightClose, Terminal, XCircle } from "lucide-react";
import { settleProcessBlocks } from "../surface";
import type { AgentRun, SurfaceBlock } from "../types";
import RunProcessList from "./RunProcessList";

function hasAction(block: SurfaceBlock): boolean {
  if (block.block_type !== "interaction") return false;
  const actions = Array.isArray(block.data?.actions) ? block.data.actions : [];
  return actions.some((action) => action && typeof action === "object" && !(action as { disabled?: boolean }).disabled);
}

interface Props {
  run?: AgentRun;
  onClose?: () => void;
  onArtifactSelect?: (blockId: string) => void;
}

export default function RunPanel({ run, onClose, onArtifactSelect }: Props) {
  const process = run ? settleProcessBlocks(run.process, run.status) : [];
  const awaiting = Boolean(run?.status === "done" && run.artifacts.some(hasAction));
  const statusLabel = awaiting ? "等待你的确认" : run?.status === "done" ? "本轮完成" : run?.status === "error" ? "运行失败" : "正在运行";
  return (
    <aside className="run-panel" aria-label="当前对话的运行过程与结果导航">
      <div className="run-panel-head"><div><Activity size={17} /><strong>运行过程</strong></div>{onClose && <button className="icon-button mobile-only" onClick={onClose} aria-label="关闭运行面板"><PanelRightClose size={18} /></button>}</div>
      {!run ? <div className="run-empty"><div><Terminal size={21} /></div><strong>等待任务开始</strong><p>Agent 的阶段状态和精简过程会显示在这里，正式结果保留在主对话中。</p></div> : <div className="run-detail">
        <div className={`run-status-card ${run.status}${awaiting ? " awaiting" : ""}`} role="status" aria-live="polite">
          <div>{run.status === "done" && !awaiting ? <CheckCircle2 size={19} /> : run.status === "error" ? <XCircle size={19} /> : <Clock3 size={19} />}<span>{statusLabel}</span></div>
          <p>{awaiting ? "结果已经生成，请在主对话中确认下一步。" : run.summary}</p>
        </div>
        <div className="run-section-label">{run.status === "running" ? "实时步骤" : "执行轨迹"}<span>{process.length}</span></div>
        <RunProcessList blocks={process} />
        <div className="run-section-label">{awaiting ? "结果与下一步" : "本轮结果"}<span>{run.artifacts.length}</span></div>
        <div className="artifact-index">{run.artifacts.length ? run.artifacts.map((block) => {
          const actionable = hasAction(block);
          return <button type="button" key={block.block_id} onClick={() => onArtifactSelect?.(block.block_id)} disabled={!onArtifactSelect} aria-label={`定位到${block.title || block.block_type}`}>
            <span><small className={actionable ? "pending" : "result"}>{actionable ? "待确认" : "结果"}</small>{block.title || block.block_type}</span>
            <ChevronRight size={14} />
          </button>;
        }) : <p>结果生成后会显示在主对话中</p>}</div>
      </div>}
    </aside>
  );
}
