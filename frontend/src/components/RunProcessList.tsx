import { CheckCircle2, Circle, XCircle } from "lucide-react";
import type { SurfaceBlock } from "../types";
import JsonBlock from "./JsonBlock";
import MarkdownContent from "./MarkdownContent";

export function processLabel(block: SurfaceBlock): string {
  return String(block.data?.summary || block.data?.message || block.content || block.title || "Agent 正在处理");
}

function ProcessDetail({ block }: { block: SurfaceBlock }) {
  const format = String(block.data?.format || "");
  const value = block.data?.value;
  if (format === "json" && value != null) {
    return <JsonBlock value={value} title="结构化结果" collapsible compact />;
  }
  if (format === "markdown") {
    return <div className="process-markdown"><MarkdownContent content={String(block.content || block.data?.summary || "")} /></div>;
  }
  return null;
}

function ProcessIcon({ block }: { block: SurfaceBlock }) {
  const status = String(block.data?.status || "completed");
  if (status === "running") return <span className="spinner" />;
  if (status === "error") return <XCircle size={15} />;
  if (["pending", "queued", "loading", "in_progress"].includes(status)) return <Circle size={15} />;
  return <CheckCircle2 size={15} />;
}

export default function RunProcessList({ blocks, emptyText = "正在等待第一个运行事件…" }: { blocks: SurfaceBlock[]; emptyText?: string }) {
  return (
    <div className="process-list">
      {blocks.length ? blocks.map((block, index) => <div className={`process-item ${String(block.data?.status || "")}`} key={block.block_id}>
        <div className="process-icon"><ProcessIcon block={block} /></div>
        <div>
          <strong>{block.title || `步骤 ${index + 1}`}</strong>
          {String(block.data?.format || "") !== "markdown" && <p>{processLabel(block)}</p>}
          <ProcessDetail block={block} />
        </div>
      </div>) : <div className="process-item muted"><Circle size={14} /><p>{emptyText}</p></div>}
    </div>
  );
}
