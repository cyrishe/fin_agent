import type { SurfaceBlock, UnknownRecord } from "../types";
import type {
  CodeFile,
  CodeRuntimeState,
  DataShape,
  DomainContext,
  FinanceCandle,
  FinanceIndicator,
  GraphEdge,
  GraphNode,
  PresentationHint,
  RenderObject,
  SemanticBlockKind,
} from "./model";

export const asRecord = (value: unknown): UnknownRecord =>
  value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};

const asList = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const asNumber = (value: unknown): number | undefined => {
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
};

const LEGACY_KIND: Record<string, SemanticBlockKind> = {
  markdown: "narrative",
  narrative: "narrative",
  status: "workflow",
  workflow: "workflow",
  artifact: "artifact",
  code: "artifact",
  assessment: "assessment",
  resource: "resource",
  interaction: "interaction",
  action: "interaction",
  table: "data",
  metric_strip: "data",
  line: "data",
  line_chart: "data",
  bar: "data",
  bar_chart: "data",
  pie: "data",
  pie_chart: "data",
  kline: "data",
  intraday: "data",
  flow: "data",
  flowchart: "data",
  chatflow: "data",
};

const LEGACY_SHAPE: Record<string, DataShape> = {
  table: "records",
  metric_strip: "record",
  line: "series",
  line_chart: "series",
  bar: "series",
  bar_chart: "series",
  pie: "series",
  pie_chart: "series",
  kline: "timeseries",
  intraday: "timeseries",
  flow: "graph",
  flowchart: "graph",
  chatflow: "graph",
};

const LEGACY_SEMANTIC: Record<string, string> = {
  kline: "finance.ohlcv",
  intraday: "finance.intraday",
  flow: "workflow.graph",
  flowchart: "workflow.graph",
  chatflow: "workflow.graph",
  code: "code.module",
};

function normalizePresentation(value: unknown, legacyType: string): PresentationHint {
  const hint = asRecord(value);
  return {
    preferredRenderer: String(hint.preferred_renderer || hint.renderer || "") || undefined,
    density: String(hint.density || "") || undefined,
    defaultState: String(hint.default_state || "") || undefined,
    chartType: String(hint.chart_type || legacyType || "") || undefined,
  };
}

function normalizeDomain(block: SurfaceBlock, data: UnknownRecord): DomainContext {
  const context = asRecord(block.domain_context || data.domain_context);
  const meta = asRecord(block.meta || data.meta);
  return {
    namespace: String(context.namespace || "") || undefined,
    asOf: String(context.as_of || data.as_of || "") || undefined,
    timezone: String(context.timezone || "") || undefined,
    markets: asList(context.markets).map(String),
    currency: String(context.currency || meta.currency || "") || undefined,
    unit: String(context.unit || meta.unit || "") || undefined,
    frequency: String(context.frequency || data.frequency || "") || undefined,
    adjustment: String(context.adjustment || data.adjustment || "") || undefined,
    dataMode: String(context.data_mode || data.data_mode || "") || undefined,
    source: String(context.source || meta.source || data.source || "") || undefined,
  };
}

export function normalizeRenderObject(block: SurfaceBlock): RenderObject {
  const legacyType = String(block.block_type || block.type || "narrative").trim();
  const protocolKind = String(block.kind || "").trim();
  const kind = (protocolKind || LEGACY_KIND[legacyType] || "narrative") as SemanticBlockKind;
  const protocolPayload = asRecord(block.payload);
  const legacyData = asRecord(block.data);
  const protocolData = protocolPayload.data;
  const dataPayload = kind === "data" && Object.keys(protocolPayload).length
    ? Array.isArray(protocolData)
      ? { rows: protocolData }
      : protocolData && typeof protocolData === "object"
        ? asRecord(protocolData)
        : { value: protocolData }
    : legacyData;
  const effectivePayload = kind === "data"
    ? dataPayload
    : Object.keys(protocolPayload).length
      ? protocolPayload
      : legacyData;
  const shape = (String(protocolPayload.shape || LEGACY_SHAPE[legacyType] || "") || undefined) as DataShape | undefined;
  const contentType = String(protocolPayload.content_type || legacyData.content_type || "") || undefined;
  const semantic = String(block.semantic || LEGACY_SEMANTIC[legacyType] || "") || undefined;
  const narrativeText = kind === "narrative" ? String(protocolPayload.text || block.content || "") : undefined;

  return {
    id: String(block.block_id || block.id || "render-object"),
    kind,
    title: block.title ? String(block.title) : undefined,
    semantic,
    shape,
    contentType,
    contentSchemaVersion: String(protocolPayload.content_schema_version || "") || undefined,
    text: narrativeText,
    payload: effectivePayload,
    presentation: normalizePresentation(block.presentation_hint || legacyData.presentation_hint, legacyType),
    domain: normalizeDomain(block, effectivePayload),
    sourceBlock: block,
    legacyType,
  };
}

export function normalizeCandles(value: unknown): FinanceCandle[] {
  const source = Array.isArray(value) ? value : asList(asRecord(value).candles || asRecord(value).kline);
  return source.map((item): FinanceCandle | null => {
    if (Array.isArray(item)) {
      const [time, open, close, low, high, volume, pct] = item;
      const values = [open, close, low, high].map(asNumber);
      if (values.some((number) => number == null)) return null;
      return {
        time: String(time || ""),
        open: values[0]!,
        close: values[1]!,
        low: values[2]!,
        high: values[3]!,
        volume: asNumber(volume),
        pct: asNumber(pct),
      };
    }
    const row = asRecord(item);
    const open = asNumber(row.open);
    const close = asNumber(row.close);
    const low = asNumber(row.low);
    const high = asNumber(row.high);
    if ([open, close, low, high].some((number) => number == null)) return null;
    return {
      time: String(row.time || row.date || row.datetime || ""),
      open: open!, high: high!, low: low!, close: close!,
      volume: asNumber(row.volume || row.vol),
      amount: asNumber(row.amount),
      pct: asNumber(row.pct || row.change_pct),
    };
  }).filter((item): item is FinanceCandle => item !== null);
}

export function normalizeIndicators(value: unknown): FinanceIndicator[] {
  if (Array.isArray(value)) {
    return value.map((item) => {
      const row = asRecord(item);
      return {
        name: String(row.name || "指标"),
        color: String(row.color || "") || undefined,
        points: asList(row.points || row.data).map((point) => {
          if (Array.isArray(point)) return { time: String(point[0] || ""), value: Number(point[1]) };
          const record = asRecord(point);
          return { time: String(record.time || record.date || ""), value: Number(record.value) };
        }).filter((point) => Number.isFinite(point.value)),
      };
    });
  }
  return Object.entries(asRecord(value)).map(([name, points]) => ({
    name,
    points: asList(points).map((point) => Array.isArray(point)
      ? { time: String(point[0] || ""), value: Number(point[1]) }
      : { time: String(asRecord(point).time || ""), value: Number(asRecord(point).value) },
    ).filter((point) => Number.isFinite(point.value)),
  }));
}

export function normalizeGraph(payload: UnknownRecord): { nodes: GraphNode[]; edges: GraphEdge[]; source?: string } {
  const source = String(payload.code || payload.mermaid || payload.source || payload.chatflow || "") || undefined;
  const nodes = asList(payload.nodes).map((item, index) => {
    const node = asRecord(item);
    return {
      id: String(node.id || `node_${index + 1}`),
      label: String(node.label || node.title || node.name || `节点 ${index + 1}`),
      status: String(node.status || "") || undefined,
      detail: String(node.detail || node.description || "") || undefined,
      group: String(node.group || "") || undefined,
    };
  });
  const edges = asList(payload.edges).map((item) => {
    const edge = asRecord(item);
    return {
      from: String(edge.from || edge.source || ""),
      to: String(edge.to || edge.target || ""),
      label: String(edge.label || "") || undefined,
      status: String(edge.status || "") || undefined,
    };
  }).filter((edge) => edge.from && edge.to);
  return { nodes, edges, source };
}

export function normalizeCodeFiles(object: RenderObject): CodeFile[] {
  const payload = object.payload;
  const content = asRecord(payload.content);
  const rawFiles = asList(payload.files || content.files || payload.modules || content.modules);
  if (rawFiles.length) return rawFiles.map((item, index) => {
    const file = asRecord(item);
    return {
      id: String(file.id || file.path || file.name || index),
      name: String(file.path || file.name || file.module || `module_${index + 1}`),
      language: String(file.language || file.lang || "python"),
      content: String(file.content || file.code || file.source || ""),
      status: String(file.status || "") || undefined,
    };
  });
  const code = String(payload.code || content.code || object.sourceBlock.content || "");
  return code ? [{ id: "main", name: String(payload.name || "main"), language: String(payload.language || "python"), content: code }] : [];
}

export function normalizeRuntime(object: RenderObject): CodeRuntimeState {
  const payload = object.payload;
  const runtime = asRecord(payload.runtime || payload.execution || payload.run);
  return {
    status: String(runtime.status || payload.runtime_status || "idle"),
    durationMs: asNumber(runtime.duration_ms || runtime.elapsed_ms),
    stdout: String(runtime.stdout || "") || undefined,
    stderr: String(runtime.stderr || runtime.error || "") || undefined,
    logs: asList(runtime.logs || payload.logs).map((item) => typeof item === "string"
      ? { message: item }
      : {
        level: String(asRecord(item).level || "") || undefined,
        message: String(asRecord(item).message || ""),
        timestamp: String(asRecord(item).timestamp || "") || undefined,
        data: asRecord(item).data,
      }),
    tests: asList(runtime.tests || payload.tests || asRecord(payload.details).tests).map((item, index) => {
      const test = asRecord(item);
      return {
        name: String(test.name || test.test_id || `测试 ${index + 1}`),
        status: String(test.status || "pending"),
        durationMs: asNumber(test.duration_ms),
        summary: String(test.summary || test.purpose || test.error || "") || undefined,
        expected: test.expected,
        actual: test.actual,
      };
    }),
  };
}
