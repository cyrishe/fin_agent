import type { RenderObject } from "../../rendering/model";
import { normalizeGraph } from "../../rendering/normalize";
import MermaidDiagram from "../MermaidDiagram";

const safeLabel = (value: string) => value.replace(/[\[\]{}()"#;]/g, " ").replace(/\s+/g, " ").trim();

function graphToMermaid(object: RenderObject): string {
  const graph = normalizeGraph(object.payload);
  if (graph.source) return graph.source;
  const directSource = String(object.sourceBlock.content || "").trim();
  if (/^(flowchart|graph|sequenceDiagram|stateDiagram)/.test(directSource)) return directSource;
  if (!graph.nodes.length) return "";

  const lines = ["flowchart LR"];
  const nodeIds = new Map(graph.nodes.map((node, index) => [node.id, `n${index + 1}`]));
  graph.nodes.forEach((node) => {
    const id = nodeIds.get(node.id)!;
    const label = safeLabel(node.label);
    const shape = node.status === "decision" ? `{${label}}`
      : node.status === "validate" ? `([${label}])`
        : node.status === "output" ? `[/ ${label} /]`
          : `[${label}]`;
    lines.push(`  ${id}${shape}`);
    if (["validate", "decision", "output", "process"].includes(String(node.status))) lines.push(`  class ${id} ${node.status}`);
  });
  graph.edges.forEach((edge) => {
    const from = nodeIds.get(edge.from);
    const to = nodeIds.get(edge.to);
    if (from && to) lines.push(`  ${from} -->${edge.label ? `|${safeLabel(edge.label)}|` : ""} ${to}`);
  });
  lines.push("  classDef validate fill:#f3f7ff,stroke:#7d9fdf,color:#264f9e");
  lines.push("  classDef process fill:#f7f9fc,stroke:#aeb9ca,color:#33445f");
  lines.push("  classDef decision fill:#fff8e9,stroke:#d6a34a,color:#795612");
  lines.push("  classDef output fill:#eefaf6,stroke:#55a88d,color:#165f4b");
  const grouped = new Map<string, string[]>();
  graph.nodes.forEach((node) => {
    if (!node.status) return;
    grouped.set(node.status, [...(grouped.get(node.status) || []), node.id]);
  });
  const colors: Record<string, string> = {
    succeeded: "fill:#eefaf6,stroke:#168766,color:#135d49",
    completed: "fill:#eefaf6,stroke:#168766,color:#135d49",
    running: "fill:#edf3ff,stroke:#2f64d6,color:#214da9",
    failed: "fill:#fff2f3,stroke:#c84955,color:#963642",
    blocked: "fill:#fff8ea,stroke:#d99a2b,color:#855d16",
  };
  grouped.forEach((ids, status) => {
    if (!colors[status]) return;
    lines.push(`  classDef ${status} ${colors[status]}`);
    lines.push(`  class ${ids.join(",")} ${status}`);
  });
  return lines.join("\n");
}

export default function FlowRenderer({ object }: { object: RenderObject }) {
  const source = graphToMermaid(object);
  if (!source) {
    const graph = normalizeGraph(object.payload);
    return <div className="graph-fallback">{graph.nodes.map((node) => <div key={node.id}><strong>{node.label}</strong>{node.detail && <span>{node.detail}</span>}</div>)}</div>;
  }
  return <MermaidDiagram source={source} />;
}
