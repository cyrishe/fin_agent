import type { AgentRun } from "../types";

function formatTime(timestamp: number): string {
  if (!timestamp || !Number.isFinite(timestamp)) return "";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameDay = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  return new Intl.DateTimeFormat("zh-CN", sameDay
    ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }
    : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
  ).format(date);
}

function formatDuration(durationMs?: number): string {
  if (durationMs == null || !Number.isFinite(durationMs)) return "";
  if (durationMs < 1_000) return `${Math.max(1, Math.round(durationMs))}ms`;
  if (durationMs < 60_000) return `${(durationMs / 1_000).toFixed(durationMs < 10_000 ? 1 : 0)}s`;
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.round((durationMs % 60_000) / 1_000);
  return `${minutes}m ${seconds}s`;
}

function fullTime(timestamp: number): string {
  return timestamp ? new Date(timestamp).toLocaleString("zh-CN", { hour12: false }) : "";
}

export default function TurnMeta({ createdAt, run }: { createdAt: number; run?: AgentRun }) {
  const time = formatTime(createdAt);
  const duration = formatDuration(run?.durationMs);
  const tokens = run?.tokenUsage?.totalTokens || 0;
  if (!time && !duration && !tokens) return null;
  return (
    <span className="turn-meta" title={fullTime(createdAt)}>
      {time ? <span>{time}</span> : null}
      {duration ? <><i /><span>用时 {duration}</span></> : null}
      {tokens ? <><i /><span>{tokens.toLocaleString("zh-CN")} tokens</span></> : null}
    </span>
  );
}
