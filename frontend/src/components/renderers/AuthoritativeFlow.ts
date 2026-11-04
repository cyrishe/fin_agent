import { normalizeRenderObject } from "../../rendering/normalize";
import type { RenderObject } from "../../rendering/model";
import type { UnknownRecord } from "../../types";

const record = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value)
  ? value as UnknownRecord
  : {};
const records = (value: unknown): UnknownRecord[] => Array.isArray(value)
  ? value.filter((item): item is UnknownRecord => Boolean(item && typeof item === "object" && !Array.isArray(item)))
  : [];

/**
 * Adapts the persisted Design flow into the shared graph renderer. The helper
 * deliberately does not infer a new flow from implementation modules: Design
 * remains the single authoritative source throughout Design and Coding.
 */
export function authoritativeFlowObject({
  id,
  mermaid,
  flow,
}: {
  id: string;
  mermaid?: unknown;
  flow?: unknown;
}): RenderObject | null {
  const flowRecord = record(flow);
  const nestedFlow = record(flowRecord.flow);
  const source = String(
    mermaid
    || flowRecord.mermaid
    || flowRecord.source
    || flowRecord.code
    || nestedFlow.mermaid
    || nestedFlow.source
    || "",
  ).trim();
  const steps = records(
    flowRecord.steps
    || flowRecord.nodes
    || nestedFlow.steps
    || nestedFlow.nodes,
  );
  const links = records(
    flowRecord.links
    || flowRecord.edges
    || nestedFlow.links
    || nestedFlow.edges,
  );

  if (!source && !steps.length) return null;

  return normalizeRenderObject({
    block_id: id,
    block_type: "data",
    kind: "data",
    semantic: "finance.tool_design_workflow",
    payload: {
      shape: "graph",
      content_type: "application/vnd.fin-agent.graph+json",
      data: {
        source,
        nodes: steps.map((step, index) => ({
          id: String(step.id || step.step_id || `step_${index + 1}`),
          label: String(step.name || step.title || step.label || `步骤 ${index + 1}`),
          detail: String(step.description || step.detail || ""),
          status: String(step.type || step.status || "process"),
        })),
        edges: links.map((link) => ({
          from: String(link.from || link.source || ""),
          to: String(link.to || link.target || ""),
          label: String(link.label || link.condition || ""),
        })),
      },
    },
  });
}
