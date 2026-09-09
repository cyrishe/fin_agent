import { ChevronRight, Database, Table2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { normalizeRenderObject } from "../rendering/normalize";
import { chooseRenderer } from "../rendering/registry";
import type { SurfaceBlock } from "../types";

export function splitAnswerEvidence(blocks: SurfaceBlock[]) {
  const hasAnswer = blocks.some(block => block.semantic === "finance.answer" &&
    Boolean(normalizeRenderObject(block).text?.trim() || block.content?.trim()));
  const reference = (block: SurfaceBlock) => hasAnswer &&
    String(block.semantic || "").startsWith("finance.") &&
    chooseRenderer(normalizeRenderObject(block)) === "data.table";
  return { primary: blocks.filter(block => !reference(block)), references: blocks.filter(reference) };
}

function ReferenceItem({ block, renderBlock }: { block: SurfaceBlock; renderBlock: (block: SurfaceBlock) => ReactNode }) {
  const [mounted, setMounted] = useState(false);
  const object = normalizeRenderObject(block);
  const data = object.payload;
  const count = data.returned_row_count ?? data.row_count;
  return <details className="reference-item" data-block-id={block.block_id} tabIndex={-1}
    onToggle={event => { if (event.currentTarget.open) setMounted(true); }}>
    <summary><Table2 size={16} aria-hidden="true" /><span className="reference-item-label">
      <strong>{block.title || "数据明细"}</strong>
      <small>{object.domain.source || "查询结果"}{typeof count === "number" ? ` · ${count} 条记录` : ""}</small>
    </span><ChevronRight className="reference-chevron" size={16} aria-hidden="true" /></summary>
    {mounted ? <div className="reference-item-body">{renderBlock(block)}</div> : null}
  </details>;
}

export default function AnswerEvidence({ blocks, renderBlock }: { blocks: SurfaceBlock[]; renderBlock: (block: SurfaceBlock) => ReactNode }) {
  if (!blocks.length) return null;
  return <details className="answer-evidence">
    <summary><span><Database size={17} aria-hidden="true" /><strong>参考数据</strong><small>{blocks.length} 组</small></span>
      <span className="reference-action">来源与明细<ChevronRight className="reference-chevron" size={16} aria-hidden="true" /></span></summary>
    <div className="answer-evidence-body">{blocks.map(block => <ReferenceItem key={block.block_id} block={block} renderBlock={renderBlock} />)}</div>
  </details>;
}
