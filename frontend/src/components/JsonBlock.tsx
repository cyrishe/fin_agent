import { Braces } from "lucide-react";

interface Props {
  value: unknown;
  title?: string;
  collapsible?: boolean;
  defaultOpen?: boolean;
  compact?: boolean;
}

export default function JsonBlock({
  value,
  title = "JSON",
  collapsible = false,
  defaultOpen = false,
  compact = false,
}: Props) {
  const formatted = JSON.stringify(value, null, 2) ?? String(value ?? "");
  const body = <pre><code>{formatted}</code></pre>;

  if (collapsible) {
    return (
      <details className={`json-block${compact ? " compact" : ""}`} open={defaultOpen}>
        <summary><Braces size={13} /><span>{title}</span></summary>
        {body}
      </details>
    );
  }

  return (
    <div className={`json-block${compact ? " compact" : ""}`}>
      {title && <div className="json-block-head"><Braces size={13} /><span>{title}</span></div>}
      {body}
    </div>
  );
}
