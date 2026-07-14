import { ArrowLeft, Blocks, Sparkles } from "lucide-react";
import BlockRenderer from "./components/BlockRenderer";
import type { SurfaceBlock } from "./types";

const candles = Array.from({ length: 42 }, (_, index) => {
  const base = 98 + index * .16 + Math.sin(index / 3) * 2.1;
  const open = base + Math.sin(index * 1.7) * .8;
  const close = base + Math.cos(index * 1.23) * 1.05;
  return {
    time: `06-${String(index + 1).padStart(2, "0")}`,
    open: Number(open.toFixed(2)),
    high: Number((Math.max(open, close) + .8).toFixed(2)),
    low: Number((Math.min(open, close) - .75).toFixed(2)),
    close: Number(close.toFixed(2)),
    volume: Math.round(120000 + Math.abs(Math.sin(index)) * 260000),
  };
});

const intraday = Array.from({ length: 48 }, (_, index) => ({
  time: `${String(9 + Math.floor((30 + index * 5) / 60)).padStart(2, "0")}:${String((30 + index * 5) % 60).padStart(2, "0")}`,
  price: Number((103.2 + Math.sin(index / 5) * 1.2 + index * .018).toFixed(2)),
  average: Number((103.1 + index * .015).toFixed(2)),
  volume: Math.round(3000 + Math.abs(Math.cos(index / 3)) * 9000),
}));

const blocks: SurfaceBlock[] = [
  {
    block_id: "gallery-narrative", block_type: "narrative", kind: "narrative", title: "Narrative · 金融叙述",
    semantic: "finance.market_summary", payload: { format: "markdown", text: "近 30 个交易日价格整体呈现 **震荡上行**，短期均线保持多头排列。\n\n- 当前结论来自样例数据\n- 关键指标和数据时点应由后端提供\n- Narrative 负责讲清楚结论，不负责指定图表组件" },
  },
  {
    block_id: "gallery-metrics", block_type: "data", kind: "data", title: "Data · 核心指标",
    semantic: "finance.quote.metrics", payload: { shape: "record", content_type: "finance.metrics", data: { items: [{ label: "最新价", value: 106.82, unit: "元", pct: 2.34 }, { label: "成交额", value: 28.6, unit: "亿元" }, { label: "换手率", value: 3.18, unit: "%" }, { label: "振幅", value: 4.21, unit: "%" }] } },
    domain_context: { as_of: "2026-07-13T14:35:00+08:00", data_mode: "demo", currency: "CNY" },
  },
  {
    block_id: "gallery-kline", block_type: "data", kind: "data", title: "Data · 日线 K 线",
    semantic: "finance.ohlcv", payload: { shape: "timeseries", content_type: "finance.ohlcv", data: { candles, indicators: [{ name: "MA5", points: candles.map((item, index) => ({ time: item.time, value: Number((item.close - Math.sin(index) * .45).toFixed(2)) })) }] } },
    domain_context: { as_of: "2026-07-13T15:00:00+08:00", frequency: "1d", adjustment: "forward", source: "demo.daily_kline" },
  },
  {
    block_id: "gallery-intraday", block_type: "data", kind: "data", title: "Data · 分时走势",
    semantic: "finance.intraday", payload: { shape: "timeseries", content_type: "finance.intraday", data: { points: intraday } },
    domain_context: { as_of: "2026-07-13T14:35:00+08:00", frequency: "5m", data_mode: "demo", source: "demo.intraday" },
  },
  {
    block_id: "gallery-flow", block_type: "data", kind: "data", title: "Data · Graph / Chatflow 兼容",
    semantic: "finance.tool_design_workflow", payload: { shape: "graph", content_type: "application/vnd.fin-agent.graph+json", data: { nodes: [{ id: "input", label: "校验输入", status: "completed" }, { id: "daily", label: "读取日线", status: "completed" }, { id: "signal", label: "计算金叉", status: "running" }, { id: "result", label: "输出结论", status: "pending" }], edges: [{ from: "input", to: "daily" }, { from: "daily", to: "signal" }, { from: "signal", to: "result", label: "形成结果" }] } },
  },
  {
    block_id: "gallery-table", block_type: "data", kind: "data", title: "Data · 结构化表格",
    semantic: "finance.signal.records", payload: { shape: "records", content_type: "finance.signal.records", data: [{ date: "2026-07-09", ma5: 104.2, ma10: 103.8, signal: "观察" }, { date: "2026-07-10", ma5: 104.9, ma10: 104.1, signal: "金叉" }, { date: "2026-07-11", ma5: 105.4, ma10: 104.5, signal: "已确认" }] },
  },
  {
    block_id: "gallery-code", block_type: "artifact", kind: "artifact", title: "Artifact · 代码与运行状态",
    semantic: "finance.tool_implementation", payload: { artifact_id: "demo-code", artifact_type: "finance.tool_code", lifecycle: "reviewable", version: "3", content_type: "text/x-python", content: { files: [{ name: "golden_cross", language: "python", status: "created", content: "def detect_golden_cross(close: list[float]) -> dict:\n    ma5 = moving_average(close, 5)\n    ma10 = moving_average(close, 10)\n    crossed = ma5[-2] <= ma10[-2] and ma5[-1] > ma10[-1]\n    return {\"matched\": crossed, \"ma5\": ma5[-1], \"ma10\": ma10[-1]}" }, { name: "validators", language: "python", status: "updated", content: "def validate_window(value: int) -> int:\n    if value not in (30, 60):\n        raise ValueError(\"window must be 30 or 60\")\n    return value" }] }, runtime: { status: "succeeded", duration_ms: 184, logs: [{ level: "info", message: "加载 60 个交易日日线数据" }, { level: "info", message: "完成 MA5 / MA10 计算" }], stdout: "result: matched=true, cross_date=2026-07-10", tests: [{ name: "最近30日出现金叉", status: "passed", duration_ms: 72, summary: "返回 matched=true" }, { name: "数据不足时拒绝计算", status: "passed", duration_ms: 18, summary: "返回明确的数据不足错误" }] } },
  },
];

export default function RendererGallery() {
  return <main className="gallery-page">
    <header className="gallery-header"><div><div className="brand-mark"><Sparkles size={19} /></div><div><span>Fin Agent UI Foundation</span><h1>Renderer Gallery</h1><p>同一套语义对象，根据 shape 和 semantic 选择可信 Renderer。</p></div></div><a href="./"><ArrowLeft size={15} />返回对话</a></header>
    <section className="gallery-principle"><Blocks size={18} /><div><strong>这里不是新的业务流程</strong><p>只用于验证渲染对象、兼容适配和视觉表现。样例全部标记为 demo，不会请求 LLM 或保存数据。</p></div></section>
    <div className="gallery-grid">{blocks.map((block) => <BlockRenderer key={block.block_id} block={block} onInsertText={() => undefined} onInteraction={() => undefined} resolved={false} />)}</div>
  </main>;
}
