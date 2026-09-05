import { ChevronRight, Clock3, Route } from "lucide-react";
import { settleProcessBlocks } from "../surface";
import type { AgentRun } from "../types";
import RunProcessList from "./RunProcessList";

function formatDuration(durationMs?: number): string {
  if (durationMs == null || !Number.isFinite(durationMs)) return "";
  if (durationMs < 1_000) return `${Math.max(1, Math.round(durationMs))} 毫秒`;
  if (durationMs < 60_000) return `${(durationMs / 1_000).toFixed(durationMs < 10_000 ? 1 : 0)} 秒`;
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.round((durationMs % 60_000) / 1_000);
  return `${minutes} 分 ${seconds} 秒`;
}

export default function TurnProcess({ run }: { run: AgentRun }) {
  const process = settleProcessBlocks(run.process, run.status);
  if (!process.length && run.status !== "running") return null;
  const duration = formatDuration(run.durationMs);
  return (
    <details className={`turn-process ${run.status}`} open={run.status === "running"}>
      <summary>
        <span className="turn-process-heading"><Route size={15} /><strong>本轮过程</strong></span>
        <span className="turn-process-summary">
          {process.length ? `${process.length} 个节点` : "等待执行线索"}
          {duration ? <><i /><Clock3 size={12} />{duration}</> : null}
          <ChevronRight size={15} className="turn-process-chevron" />
        </span>
      </summary>
      <div className="turn-process-body"><RunProcessList blocks={process} /></div>
    </details>
  );
}
