import type { SurfaceBlock, UnknownRecord } from "../types";

export type SemanticBlockKind =
  | "narrative"
  | "data"
  | "workflow"
  | "artifact"
  | "assessment"
  | "resource"
  | "interaction";

export type DataShape =
  | "scalar"
  | "record"
  | "records"
  | "series"
  | "timeseries"
  | "graph"
  | "hierarchy";

export type RendererId =
  | "text.markdown"
  | "data.metrics"
  | "data.table"
  | "data.chart"
  | "finance.kline"
  | "finance.intraday"
  | "diagram.flow"
  | "diagram.hierarchy"
  | "workflow.steps"
  | "artifact.spec"
  | "artifact.code"
  | "assessment.review"
  | "resource.list"
  | "interaction.form"
  | "fallback.structured";

export interface PresentationHint {
  preferredRenderer?: string;
  density?: "compact" | "comfortable" | "detailed" | string;
  defaultState?: "expanded" | "collapsed" | string;
  chartType?: string;
}

export interface DomainContext {
  namespace?: string;
  asOf?: string;
  timezone?: string;
  markets?: string[];
  currency?: string;
  unit?: string;
  frequency?: string;
  adjustment?: string;
  dataMode?: string;
  source?: string;
}

export interface RenderObject {
  id: string;
  kind: SemanticBlockKind;
  title?: string;
  semantic?: string;
  shape?: DataShape;
  contentType?: string;
  contentSchemaVersion?: string;
  text?: string;
  payload: UnknownRecord;
  presentation: PresentationHint;
  domain: DomainContext;
  sourceBlock: SurfaceBlock;
  legacyType?: string;
}

export interface FinanceCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  amount?: number;
  pct?: number;
}

export interface FinanceIndicatorPoint {
  time: string;
  value: number;
}

export interface FinanceIndicator {
  name: string;
  points: FinanceIndicatorPoint[];
  color?: string;
}

export interface GraphNode {
  id: string;
  label: string;
  status?: string;
  detail?: string;
  group?: string;
}

export interface GraphEdge {
  from: string;
  to: string;
  label?: string;
  status?: string;
}

export interface CodeFile {
  id: string;
  name: string;
  language: string;
  content: string;
  status?: "created" | "updated" | "unchanged" | string;
}

export interface RuntimeLog {
  level?: string;
  message: string;
  timestamp?: string;
  data?: unknown;
}

export interface RuntimeTest {
  name: string;
  status: "passed" | "failed" | "running" | "pending" | string;
  durationMs?: number;
  summary?: string;
  expected?: unknown;
  actual?: unknown;
}

export interface CodeRuntimeState {
  status: "idle" | "queued" | "running" | "succeeded" | "failed" | "cancelled" | string;
  durationMs?: number;
  stdout?: string;
  stderr?: string;
  logs: RuntimeLog[];
  tests: RuntimeTest[];
}

export interface RendererCapabilities {
  renderers: Set<RendererId>;
  maxInlineRows: number;
  supportsMermaid: boolean;
  supportsHighcharts: boolean;
}
