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
  graph.nodes.forEach((node) => {
    const shape = node.status === "running" ? `{${safeLabel(node.label)}}` : `[${safeLabel(node.label)}]`;
    lines.push(`  ${node.id}${shape}`);
  });
  graph.edges.forEach((edge) => lines.push(`  ${edge.from} -->${edge.label ? `|${safeLabel(edge.label)}|` : ""} ${edge.to}`));
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
