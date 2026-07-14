import { CalendarClock, Database, Gauge, RefreshCw } from "lucide-react";
import type { RenderObject } from "../../rendering/model";

export default function RenderMeta({ object }: { object: RenderObject }) {
  const items = [
    object.domain.asOf ? { icon: CalendarClock, label: object.domain.asOf } : null,
    object.domain.frequency ? { icon: Gauge, label: object.domain.frequency } : null,
    object.domain.adjustment ? { icon: RefreshCw, label: object.domain.adjustment } : null,
    object.domain.source ? { icon: Database, label: object.domain.source } : null,
  ].filter(Boolean) as Array<{ icon: typeof CalendarClock; label: string }>;
  if (!items.length) return null;
  return <div className="render-meta">{items.map(({ icon: Icon, label }, index) => <span key={`${label}-${index}`}><Icon size={12} />{label}</span>)}</div>;
}
