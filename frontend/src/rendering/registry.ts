import type { RenderObject, RendererCapabilities, RendererId } from "./model";

export const defaultCapabilities: RendererCapabilities = {
  renderers: new Set<RendererId>([
    "text.markdown", "data.metrics", "data.table", "data.chart",
    "finance.kline", "finance.intraday", "diagram.flow", "diagram.hierarchy",
    "workflow.steps", "artifact.spec", "artifact.code", "assessment.review",
    "resource.list", "interaction.form", "fallback.structured",
  ]),
  maxInlineRows: 500,
  supportsMermaid: true,
  supportsHighcharts: true,
};

const preferredAliases: Record<string, RendererId> = {
  "text.markdown": "text.markdown",
  "data.table": "data.table",
  "data.line": "data.chart",
  "data.bar": "data.chart",
  "data.pie": "data.chart",
  "data.kline": "finance.kline",
  "finance.kline": "finance.kline",
  "finance.intraday": "finance.intraday",
  "workflow.steps": "workflow.steps",
  "diagram.flow": "diagram.flow",
  "artifact.code": "artifact.code",
  "artifact.spec_editor": "artifact.spec",
  "assessment.review": "assessment.review",
  "interaction.form": "interaction.form",
};

export function chooseRenderer(object: RenderObject, capabilities = defaultCapabilities): RendererId {
  const preferred = object.presentation.preferredRenderer
    ? preferredAliases[object.presentation.preferredRenderer]
    : undefined;
  if (preferred && capabilities.renderers.has(preferred)) return preferred;

  let candidate: RendererId = "fallback.structured";
  if (object.kind === "narrative") candidate = "text.markdown";
  else if (object.kind === "workflow") candidate = "workflow.steps";
  else if (object.kind === "assessment") candidate = "assessment.review";
  else if (object.kind === "resource") candidate = "resource.list";
  else if (object.kind === "interaction") candidate = "interaction.form";
  else if (object.kind === "artifact") {
    const signature = `${object.semantic || ""} ${object.contentType || ""}`.toLowerCase();
    candidate = /code|python|javascript|typescript|source/.test(signature) || object.legacyType === "code"
      ? "artifact.code"
      : "artifact.spec";
  } else if (object.kind === "data") {
    const signature = `${object.semantic || ""} ${object.contentType || ""} ${object.legacyType || ""}`.toLowerCase();
    if (/ohlcv|candlestick|\bkline\b/.test(signature)) candidate = "finance.kline";
    else if (/intraday|minute|timeshare|分时/.test(signature)) candidate = "finance.intraday";
    else if (object.shape === "graph") candidate = "diagram.flow";
    else if (object.shape === "hierarchy") candidate = "diagram.hierarchy";
    else if (object.shape === "records") candidate = "data.table";
    else if (object.shape === "series" || object.shape === "timeseries") candidate = "data.chart";
    else candidate = "data.metrics";
  }

  return capabilities.renderers.has(candidate) ? candidate : "fallback.structured";
}

export interface RendererDefinition {
  id: RendererId;
  label: string;
  accepts: string;
  fallback: RendererId | null;
}

export const rendererRegistry: RendererDefinition[] = [
  { id: "text.markdown", label: "叙述", accepts: "narrative", fallback: "fallback.structured" },
  { id: "data.metrics", label: "指标", accepts: "data/scalar|record", fallback: "fallback.structured" },
  { id: "data.table", label: "表格", accepts: "data/records", fallback: "fallback.structured" },
  { id: "data.chart", label: "通用图表", accepts: "data/series|timeseries", fallback: "data.table" },
  { id: "finance.kline", label: "K 线", accepts: "data/timeseries/finance.ohlcv", fallback: "data.table" },
  { id: "finance.intraday", label: "分时", accepts: "data/timeseries/finance.intraday", fallback: "data.table" },
  { id: "diagram.flow", label: "流程图", accepts: "data/graph", fallback: "fallback.structured" },
  { id: "diagram.hierarchy", label: "层级图", accepts: "data/hierarchy", fallback: "fallback.structured" },
  { id: "workflow.steps", label: "运行步骤", accepts: "workflow", fallback: "fallback.structured" },
  { id: "artifact.spec", label: "业务产物", accepts: "artifact/*", fallback: "fallback.structured" },
  { id: "artifact.code", label: "代码与运行", accepts: "artifact/*+code", fallback: "fallback.structured" },
  { id: "assessment.review", label: "验证评估", accepts: "assessment", fallback: "fallback.structured" },
  { id: "resource.list", label: "资源", accepts: "resource", fallback: "fallback.structured" },
  { id: "interaction.form", label: "用户交互", accepts: "interaction", fallback: "text.markdown" },
  { id: "fallback.structured", label: "结构化降级", accepts: "*", fallback: null },
];
