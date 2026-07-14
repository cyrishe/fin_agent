import { asRecord } from "../../rendering/normalize";
import type { RenderObject } from "../../rendering/model";
import type { UnknownRecord } from "../../types";

function format(value: unknown, unit?: string): string {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return `${value.toLocaleString("zh-CN", { maximumFractionDigits: 4 })}${unit || ""}`;
  return `${String(value)}${unit || ""}`;
}

export default function MetricStrip({ object }: { object: RenderObject }) {
  const payload = object.payload;
  const configured = Array.isArray(payload.items) ? payload.items.map(asRecord) : [];
  const items: UnknownRecord[] = configured.length
    ? configured
    : Object.entries(payload)
      .filter(([, value]) => ["string", "number", "boolean"].includes(typeof value))
      .map(([label, value]) => ({ label, value }));
  if (!items.length) return <div className="empty-block">暂无指标数据</div>;
  return <div className="metric-grid">{items.map((item, index) => {
    const value = item.value;
    const trend = Number(item.change ?? item.pct ?? 0);
    return <div className="metric-item" key={String(item.id || item.label || index)}>
      <span>{String(item.label || item.name || `指标 ${index + 1}`)}</span>
      <strong>{format(value, String(item.unit || object.domain.unit || ""))}</strong>
      {Number.isFinite(trend) && trend !== 0 ? <small className={trend > 0 ? "up" : "down"}>{trend > 0 ? "+" : ""}{trend.toFixed(2)}%</small> : item.description ? <small>{String(item.description)}</small> : null}
    </div>;
  })}</div>;
}
