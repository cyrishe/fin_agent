import { useMemo, useState } from "react";
import {
  ArrowLeft,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Database,
  FlaskConical,
  GitCompareArrows,
  Info,
  Layers3,
  ListFilter,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  TableProperties,
} from "lucide-react";
import "./backtest-prototype.css";

type TabId = "overview" | "stability" | "evidence" | "spec";
type EvidenceFilter = "all" | "positive" | "negative" | "missing";
type EvidenceStatus = "valid" | "missing";

type MonthlyPoint = {
  label: string;
  value: number;
  benchmark: number;
  count: number;
};

type StrategyResult = {
  id: string;
  name: string;
  shortName: string;
  sourceType: string;
  version: string;
  runId: string;
  color: string;
  description: string;
  period: { start: string; end: string };
  horizonDays: number;
  confidenceLevel: number;
  summary: {
    total: number;
    valid: number;
    missing: number;
    effective: number;
    mean: number;
    median: number;
    positiveRate: number;
    excess: number;
    ci: [number, number];
  };
  monthly: MonthlyPoint[];
  distribution: { label: string; value: number }[];
  groups: { label: string; value: number; count: number }[];
  evidence: {
    date: string;
    code: string;
    name: string;
    output: string;
    realized: number | null;
    benchmark: number | null;
    status: EvidenceStatus;
  }[];
  notes: string[];
};

export const strategyResults: StrategyResult[] = [
  {
    id: "volume-breakout",
    name: "放量突破筛选",
    shortName: "放量突破",
    sourceType: "Custom Tool",
    version: "v1.4",
    runId: "BT-260720-0186",
    color: "#315fca",
    description: "评价历史上被工具筛出的股票，在随后 5 个交易日内的真实价格表现。",
    period: { start: "2026-01-05", end: "2026-06-30" },
    horizonDays: 5,
    confidenceLevel: 95,
    summary: { total: 186, valid: 178, missing: 8, effective: 126, mean: 1.84, median: 0.92, positiveRate: 61.8, excess: 1.23, ci: [0.66, 2.91] },
    monthly: [
      { label: "1月", value: 1.22, benchmark: 0.34, count: 24 },
      { label: "2月", value: 2.36, benchmark: 0.72, count: 27 },
      { label: "3月", value: 1.65, benchmark: 0.41, count: 35 },
      { label: "4月", value: 0.74, benchmark: -0.18, count: 29 },
      { label: "5月", value: 2.81, benchmark: 0.96, count: 31 },
      { label: "6月", value: 1.91, benchmark: 0.53, count: 32 },
    ],
    distribution: [
      { label: "≤ -5%", value: 12 }, { label: "-5%~-2%", value: 24 },
      { label: "-2%~0", value: 32 }, { label: "0~2%", value: 46 },
      { label: "2%~5%", value: 38 }, { label: "> 5%", value: 26 },
    ],
    groups: [
      { label: "低置信组", value: 0.46, count: 58 },
      { label: "中置信组", value: 1.57, count: 60 },
      { label: "高置信组", value: 3.42, count: 60 },
    ],
    evidence: [
      { date: "2026-06-23", code: "600519.SH", name: "贵州茅台", output: "匹配 · 量比 2.14", realized: 2.84, benchmark: 0.63, status: "valid" },
      { date: "2026-06-18", code: "300750.SZ", name: "宁德时代", output: "匹配 · 量比 1.87", realized: -1.26, benchmark: 0.18, status: "valid" },
      { date: "2026-06-11", code: "000725.SZ", name: "京东方A", output: "匹配 · 量比 2.52", realized: 4.91, benchmark: 0.82, status: "valid" },
      { date: "2026-06-04", code: "601318.SH", name: "中国平安", output: "匹配 · 量比 1.76", realized: 0.74, benchmark: -0.21, status: "valid" },
      { date: "2026-05-29", code: "688981.SH", name: "中芯国际", output: "匹配 · 量比 2.08", realized: null, benchmark: null, status: "missing" },
    ],
    notes: ["同一股票的重叠观察窗按相关性折算，有效样本量为 126。", "8 条记录因停牌或观察窗不完整未进入收益统计。"],
  },
  {
    id: "momentum-score",
    name: "短期动量评分",
    shortName: "动量评分",
    sourceType: "ML Model",
    version: "v2.1",
    runId: "BT-260720-0420",
    color: "#8a59c4",
    description: "评价模型历史高分输出与随后 5 个交易日真实表现之间的统计关系。",
    period: { start: "2026-01-05", end: "2026-06-30" },
    horizonDays: 5,
    confidenceLevel: 95,
    summary: { total: 420, valid: 407, missing: 13, effective: 263, mean: 1.26, median: 0.74, positiveRate: 58.7, excess: 0.71, ci: [0.43, 2.02] },
    monthly: [
      { label: "1月", value: 0.91, benchmark: 0.34, count: 61 },
      { label: "2月", value: 1.74, benchmark: 0.72, count: 66 },
      { label: "3月", value: 1.13, benchmark: 0.41, count: 74 },
      { label: "4月", value: 0.35, benchmark: -0.18, count: 69 },
      { label: "5月", value: 1.89, benchmark: 0.96, count: 71 },
      { label: "6月", value: 1.42, benchmark: 0.53, count: 66 },
    ],
    distribution: [
      { label: "≤ -5%", value: 31 }, { label: "-5%~-2%", value: 58 },
      { label: "-2%~0", value: 79 }, { label: "0~2%", value: 113 },
      { label: "2%~5%", value: 82 }, { label: "> 5%", value: 44 },
    ],
    groups: [
      { label: "70~79分", value: 0.38, count: 135 },
      { label: "80~89分", value: 1.09, count: 142 },
      { label: "90~100分", value: 2.36, count: 130 },
    ],
    evidence: [
      { date: "2026-06-24", code: "002594.SZ", name: "比亚迪", output: "评分 92.4", realized: 3.17, benchmark: 0.54, status: "valid" },
      { date: "2026-06-20", code: "600036.SH", name: "招商银行", output: "评分 88.1", realized: 0.46, benchmark: 0.22, status: "valid" },
      { date: "2026-06-13", code: "300059.SZ", name: "东方财富", output: "评分 94.7", realized: -2.18, benchmark: -0.46, status: "valid" },
      { date: "2026-06-06", code: "601899.SH", name: "紫金矿业", output: "评分 86.3", realized: 2.09, benchmark: 0.37, status: "valid" },
      { date: "2026-05-30", code: "000333.SZ", name: "美的集团", output: "评分 81.6", realized: null, benchmark: null, status: "missing" },
    ],
    notes: ["评分仅作为上游历史输出读取，评估模块不重新训练或调整模型。", "高分组样本表现按预先固定的分组边界统计。"],
  },
  {
    id: "golden-cross",
    name: "均线金叉识别",
    shortName: "均线金叉",
    sourceType: "Rule Module",
    version: "v1.0",
    runId: "BT-260720-0074",
    color: "#ce7b35",
    description: "评价历史金叉识别结果在随后 5 个交易日的实际表现，不推导交易动作。",
    period: { start: "2026-01-05", end: "2026-06-30" },
    horizonDays: 5,
    confidenceLevel: 95,
    summary: { total: 74, valid: 71, missing: 3, effective: 55, mean: 0.98, median: 0.41, positiveRate: 54.9, excess: 0.38, ci: [-0.18, 2.12] },
    monthly: [
      { label: "1月", value: 0.58, benchmark: 0.34, count: 9 },
      { label: "2月", value: 1.83, benchmark: 0.72, count: 12 },
      { label: "3月", value: 0.24, benchmark: 0.41, count: 14 },
      { label: "4月", value: -0.61, benchmark: -0.18, count: 11 },
      { label: "5月", value: 2.19, benchmark: 0.96, count: 13 },
      { label: "6月", value: 1.32, benchmark: 0.53, count: 12 },
    ],
    distribution: [
      { label: "≤ -5%", value: 6 }, { label: "-5%~-2%", value: 12 },
      { label: "-2%~0", value: 14 }, { label: "0~2%", value: 18 },
      { label: "2%~5%", value: 13 }, { label: "> 5%", value: 8 },
    ],
    groups: [
      { label: "首次金叉", value: 1.21, count: 31 },
      { label: "30日内重复", value: 0.57, count: 22 },
      { label: "放量伴随", value: 1.64, count: 18 },
    ],
    evidence: [
      { date: "2026-06-25", code: "600276.SH", name: "恒瑞医药", output: "MA5 上穿 MA20", realized: 1.67, benchmark: 0.43, status: "valid" },
      { date: "2026-06-17", code: "000858.SZ", name: "五粮液", output: "MA5 上穿 MA20", realized: -0.82, benchmark: 0.11, status: "valid" },
      { date: "2026-06-10", code: "600887.SH", name: "伊利股份", output: "MA5 上穿 MA20", realized: 0.39, benchmark: -0.15, status: "valid" },
      { date: "2026-05-26", code: "601012.SH", name: "隆基绿能", output: "MA5 上穿 MA20", realized: 4.13, benchmark: 0.71, status: "valid" },
      { date: "2026-05-19", code: "002230.SZ", name: "科大讯飞", output: "MA5 上穿 MA20", realized: null, benchmark: null, status: "missing" },
    ],
    notes: ["95% 置信区间跨过 0，页面只陈述估计不确定性，不给出有效或无效判断。", "重复触发事件已保留在证据中，并在有效样本量估算时处理相关性。"],
  },
];

const tabs: { id: TabId; label: string }[] = [
  { id: "overview", label: "结果概览" },
  { id: "stability", label: "时间与分组" },
  { id: "evidence", label: "样本证据" },
  { id: "spec", label: "运行与口径" },
];

const pct = (value: number, signed = true) => `${signed && value > 0 ? "+" : ""}${value.toFixed(2)}%`;

function LinePlot({ points, color }: { points: MonthlyPoint[]; color: string }) {
  const width = 760;
  const height = 238;
  const pad = { left: 42, right: 20, top: 20, bottom: 38 };
  const values = points.flatMap((point) => [point.value, point.benchmark, 0]);
  const min = Math.floor((Math.min(...values) - .35) * 2) / 2;
  const max = Math.ceil((Math.max(...values) + .35) * 2) / 2;
  const x = (index: number) => pad.left + index * ((width - pad.left - pad.right) / Math.max(points.length - 1, 1));
  const y = (value: number) => pad.top + (max - value) * ((height - pad.top - pad.bottom) / (max - min));
  const line = (key: "value" | "benchmark") => points.map((point, index) => `${index === 0 ? "M" : "L"}${x(index)},${y(point[key])}`).join(" ");
  const ticks = [max, (max + min) / 2, min];

  return <div className="backtest-line-chart">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="stability-chart-title">
      <title id="stability-chart-title">各月平均后续收益率与同期基准对比</title>
      {ticks.map((tick) => <g key={tick}>
        <line x1={pad.left} y1={y(tick)} x2={width - pad.right} y2={y(tick)} className="chart-grid-line" />
        <text x={pad.left - 8} y={y(tick) + 4} textAnchor="end" className="chart-axis-label">{tick.toFixed(1)}%</text>
      </g>)}
      <line x1={pad.left} y1={y(0)} x2={width - pad.right} y2={y(0)} className="chart-zero-line" />
      <path d={line("benchmark")} fill="none" stroke="#a5adba" strokeWidth="2" strokeDasharray="6 5" />
      <path d={line("value")} fill="none" stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      {points.map((point, index) => <g key={point.label}>
        <circle cx={x(index)} cy={y(point.value)} r="4" fill="#fff" stroke={color} strokeWidth="2.5" />
        <text x={x(index)} y={height - 13} textAnchor="middle" className="chart-axis-label">{point.label}</text>
      </g>)}
    </svg>
  </div>;
}

function MetricCard({ label, value, hint, tone = "default" }: { label: string; value: string; hint: string; tone?: "default" | "positive" | "neutral" }) {
  return <article className={`backtest-metric ${tone}`}>
    <span>{label}</span>
    <strong>{value}</strong>
    <small>{hint}</small>
  </article>;
}

function ConfidenceRange({ strategy }: { strategy: StrategyResult }) {
  const min = -1;
  const max = 3.5;
  const left = (value: number) => `${Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100))}%`;
  const crossesZero = strategy.summary.ci[0] <= 0 && strategy.summary.ci[1] >= 0;
  return <article className="backtest-card confidence-card">
    <div className="backtest-card-head">
      <div><span className="eyebrow">估计不确定性</span><h2>{strategy.confidenceLevel}% 置信区间</h2></div>
      <span className={`interval-state ${crossesZero ? "uncertain" : "bounded"}`}>{crossesZero ? "区间跨过 0" : "区间未跨 0"}</span>
    </div>
    <div className="confidence-scale" aria-label={`均值 ${pct(strategy.summary.mean)}，置信区间 ${pct(strategy.summary.ci[0])} 至 ${pct(strategy.summary.ci[1])}`}>
      <div className="confidence-axis"><span style={{ left: left(0) }} className="confidence-zero" /></div>
      <div className="confidence-interval" style={{ left: left(strategy.summary.ci[0]), width: `calc(${left(strategy.summary.ci[1])} - ${left(strategy.summary.ci[0])})`, background: strategy.color }} />
      <span className="confidence-point" style={{ left: left(strategy.summary.mean), borderColor: strategy.color }} />
      <div className="confidence-labels"><span>{pct(strategy.summary.ci[0])}</span><strong>{pct(strategy.summary.mean)}</strong><span>{pct(strategy.summary.ci[1])}</span></div>
    </div>
    <p>这里描述的是样本均值估计范围，不表示未来获利概率，也不形成操作建议。</p>
  </article>;
}

function Comparison({ activeId, onSelect }: { activeId: string; onSelect: (id: string) => void }) {
  return <section className="backtest-card comparison-card">
    <div className="backtest-card-head">
      <div><span className="eyebrow">相同口径</span><h2>三个历史输出的横向结果</h2></div>
      <span className="demo-note">仅比较，不排序</span>
    </div>
    <div className="comparison-grid">
      {strategyResults.map((strategy) => {
        const crossesZero = strategy.summary.ci[0] <= 0 && strategy.summary.ci[1] >= 0;
        return <button key={strategy.id} type="button" className={`comparison-item ${strategy.id === activeId ? "active" : ""}`} onClick={() => onSelect(strategy.id)}>
          <div className="comparison-name"><span style={{ background: strategy.color }} /><div><strong>{strategy.shortName}</strong><small>{strategy.sourceType} · {strategy.summary.valid} 条有效样本</small></div></div>
          <div className="comparison-values"><div><span>平均后续收益</span><strong>{pct(strategy.summary.mean)}</strong></div><div><span>相对基准</span><strong>{pct(strategy.summary.excess)}</strong></div></div>
          <div className="comparison-foot"><span className={crossesZero ? "uncertain-text" : "bounded-text"}>{crossesZero ? "置信区间跨 0" : "置信区间未跨 0"}</span><ChevronRight size={16} /></div>
        </button>;
      })}
    </div>
  </section>;
}

export default function BacktestPrototype() {
  const [activeId, setActiveId] = useState(strategyResults[0].id);
  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const [showComparison, setShowComparison] = useState(false);
  const [evidenceFilter, setEvidenceFilter] = useState<EvidenceFilter>("all");
  const [query, setQuery] = useState("");
  const strategy = strategyResults.find((item) => item.id === activeId) ?? strategyResults[0];
  const maxDistribution = Math.max(...strategy.distribution.map((item) => item.value));

  const evidence = useMemo(() => strategy.evidence.filter((item) => {
    const queryMatched = !query || `${item.code}${item.name}${item.output}`.toLowerCase().includes(query.toLowerCase());
    if (!queryMatched) return false;
    if (evidenceFilter === "missing") return item.status === "missing";
    if (item.realized === null) return evidenceFilter === "all";
    if (evidenceFilter === "positive") return item.realized > 0;
    if (evidenceFilter === "negative") return item.realized <= 0;
    return true;
  }), [evidenceFilter, query, strategy]);

  const selectStrategy = (id: string) => {
    setActiveId(id);
    setEvidenceFilter("all");
    setQuery("");
  };

  return <main className="backtest-page">
    <header className="backtest-topbar">
      <div className="backtest-topbar-inner">
        <div className="backtest-brand">
          <a href="./assistant" className="backtest-back" aria-label="返回 Fin Agent"><ArrowLeft size={18} /></a>
          <div className="backtest-brand-icon"><FlaskConical size={20} /></div>
          <div><div className="backtest-title-line"><h1>历史效果评估</h1><span>DEMO</span></div><p>客观复核上游策略或工具的历史输出</p></div>
        </div>
        <div className="backtest-top-actions">
          <span className="backtest-generated"><CheckCircle2 size={15} />评估已完成 · 2026-07-20 14:36</span>
          <button type="button" className={showComparison ? "primary" : "secondary"} onClick={() => setShowComparison((value) => !value)}><GitCompareArrows size={16} />{showComparison ? "返回单项结果" : "比较策略"}</button>
        </div>
      </div>
    </header>

    <div className="backtest-layout">
      <aside className="backtest-sidebar">
        <div className="backtest-sidebar-heading"><div><span>评估对象</span><strong>3 个历史输出</strong></div><Layers3 size={17} /></div>
        <nav aria-label="评估对象列表" className="backtest-strategy-list">
          {strategyResults.map((item) => <button key={item.id} type="button" aria-pressed={item.id === strategy.id} className={item.id === strategy.id ? "active" : ""} onClick={() => selectStrategy(item.id)}>
            <span className="strategy-accent" style={{ background: item.color }} />
            <span className="strategy-copy"><strong>{item.name}</strong><small>{item.sourceType} · {item.version}</small><em>{item.summary.valid} 条有效样本</em></span>
            <ChevronRight size={16} />
          </button>)}
        </nav>
        <div className="backtest-sidebar-note"><Info size={16} /><p>“策略”仅指待评估的历史输出源。它可以来自自定义工具、规则模块或模型。</p></div>
      </aside>

      <section className="backtest-workspace">
        <div className="backtest-workspace-head">
          <div>
            <div className="backtest-object-meta"><span style={{ color: strategy.color, background: `${strategy.color}13`, borderColor: `${strategy.color}2d` }}>{strategy.sourceType}</span><code>{strategy.version}</code><code>{strategy.runId}</code></div>
            <h2>{showComparison ? "策略结果比较" : strategy.name}</h2>
            <p>{showComparison ? "所有对象使用相同评估区间、观察窗口和基准；页面不进行优劣排序。" : strategy.description}</p>
          </div>
          <div className="backtest-period"><CalendarDays size={17} /><div><span>本次评估区间</span><strong>{strategy.period.start} — {strategy.period.end}</strong></div></div>
        </div>

        {showComparison ? <Comparison activeId={strategy.id} onSelect={(id) => { selectStrategy(id); setShowComparison(false); }} /> : <>
          <div className="backtest-tabs" role="tablist" aria-label="结果页面">
            {tabs.map((tab) => <button key={tab.id} type="button" role="tab" aria-selected={activeTab === tab.id} onClick={() => setActiveTab(tab.id)}>{tab.label}</button>)}
          </div>

          {activeTab === "overview" && <div className="backtest-tab-panel" role="tabpanel">
            <section className="backtest-metrics" aria-label="核心指标">
              <MetricCard label="有效样本" value={strategy.summary.valid.toLocaleString()} hint={`原始 ${strategy.summary.total} · 缺失 ${strategy.summary.missing}`} />
              <MetricCard label={`${strategy.horizonDays}日平均收益`} value={pct(strategy.summary.mean)} hint={`中位数 ${pct(strategy.summary.median)}`} tone="positive" />
              <MetricCard label="正收益样本占比" value={`${strategy.summary.positiveRate.toFixed(1)}%`} hint={`${Math.round(strategy.summary.valid * strategy.summary.positiveRate / 100)} / ${strategy.summary.valid} 条`} tone="positive" />
              <MetricCard label="相对沪深300" value={pct(strategy.summary.excess)} hint="同期基准差值" tone="neutral" />
            </section>

            <div className="backtest-overview-grid">
              <article className="backtest-card monthly-card">
                <div className="backtest-card-head"><div><span className="eyebrow">时间稳定性</span><h2>每月平均后续收益</h2></div><div className="chart-legend"><span><i style={{ background: strategy.color }} />当前输出</span><span><i className="benchmark" />沪深300</span></div></div>
                <LinePlot points={strategy.monthly} color={strategy.color} />
                <div className="monthly-sample-row">{strategy.monthly.map((point) => <span key={point.label}>{point.label} <strong>{point.count}</strong> 条</span>)}</div>
              </article>
              <article className="backtest-card sample-card">
                <div className="backtest-card-head"><div><span className="eyebrow">样本质量</span><h2>覆盖与可用性</h2></div><ShieldCheck size={20} /></div>
                <div className="sample-ring" style={{ background: `conic-gradient(${strategy.color} ${strategy.summary.valid / strategy.summary.total * 100}%, #edf0f4 0)` }}><div><strong>{(strategy.summary.valid / strategy.summary.total * 100).toFixed(1)}%</strong><span>可用率</span></div></div>
                <dl><div><dt>原始事件</dt><dd>{strategy.summary.total}</dd></div><div><dt>完整观察窗</dt><dd>{strategy.summary.valid}</dd></div><div><dt>有效样本量</dt><dd>{strategy.summary.effective}</dd></div><div><dt>未纳入统计</dt><dd>{strategy.summary.missing}</dd></div></dl>
                <p className="sample-explain"><CircleAlert size={15} />有效样本量已考虑同一股票重叠观察窗的相关性。</p>
              </article>
            </div>

            <div className="backtest-lower-grid">
              <ConfidenceRange strategy={strategy} />
              <article className="backtest-card notes-card">
                <div className="backtest-card-head"><div><span className="eyebrow">阅读提示</span><h2>结果边界</h2></div><Info size={19} /></div>
                <ul>{strategy.notes.map((note) => <li key={note}>{note}</li>)}</ul>
              </article>
            </div>
          </div>}

          {activeTab === "stability" && <div className="backtest-tab-panel" role="tabpanel">
            <div className="backtest-section-intro"><div><BarChart3 size={19} /><div><h3>观察结果是否随时间或输出强度发生变化</h3><p>分组口径在运行开始前固定；这里只展示统计结果，不在事后寻找最优参数。</p></div></div></div>
            <article className="backtest-card full-chart-card"><div className="backtest-card-head"><div><span className="eyebrow">按自然月</span><h2>月度表现与样本数</h2></div><span className="demo-note">观察窗口：{strategy.horizonDays} 个交易日</span></div><LinePlot points={strategy.monthly} color={strategy.color} /></article>
            <div className="backtest-analysis-grid">
              <article className="backtest-card distribution-card"><div className="backtest-card-head"><div><span className="eyebrow">结果分布</span><h2>后续收益区间</h2></div><span className="demo-note">共 {strategy.summary.valid} 条</span></div><div className="distribution-bars">{strategy.distribution.map((item) => <div key={item.label}><span>{item.label}</span><div><i style={{ width: `${item.value / maxDistribution * 100}%`, background: strategy.color }} /></div><strong>{item.value}</strong></div>)}</div></article>
              <article className="backtest-card group-card"><div className="backtest-card-head"><div><span className="eyebrow">预设分组</span><h2>输出强度与结果</h2></div><span className="demo-note">平均 {strategy.horizonDays} 日收益</span></div><div className="group-bars">{strategy.groups.map((item) => <div key={item.label}><div className="group-label"><span>{item.label}</span><small>{item.count} 条</small></div><div className="group-track"><i style={{ width: `${Math.max(12, item.value / Math.max(...strategy.groups.map((group) => group.value)) * 100)}%`, background: strategy.color }} /><strong>{pct(item.value)}</strong></div></div>)}</div></article>
            </div>
          </div>}

          {activeTab === "evidence" && <div className="backtest-tab-panel" role="tabpanel">
            <div className="backtest-evidence-toolbar">
              <div className="evidence-title"><TableProperties size={19} /><div><h3>逐条历史证据</h3><p>每条结果均可追溯到上游输出和对应真实行情。</p></div></div>
              <div className="evidence-controls"><label className="evidence-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="股票或输出内容" aria-label="搜索证据" /></label><div className="evidence-filters"><ListFilter size={15} />{(["all", "positive", "negative", "missing"] as EvidenceFilter[]).map((filter) => <button type="button" key={filter} className={evidenceFilter === filter ? "active" : ""} onClick={() => setEvidenceFilter(filter)}>{({ all: "全部", positive: "正收益", negative: "非正收益", missing: "数据缺失" })[filter]}</button>)}</div></div>
            </div>
            <article className="backtest-card evidence-card"><div className="evidence-table-wrap"><table><thead><tr><th>输出日期</th><th>标的</th><th>上游历史输出</th><th>随后 {strategy.horizonDays} 日</th><th>同期基准</th><th>相对基准</th><th>状态</th></tr></thead><tbody>{evidence.length ? evidence.map((item) => <tr key={`${item.date}-${item.code}`}><td>{item.date}</td><td><strong>{item.name}</strong><code>{item.code}</code></td><td>{item.output}</td><td className={item.realized !== null && item.realized < 0 ? "negative-value" : "positive-value"}>{item.realized === null ? "—" : pct(item.realized)}</td><td>{item.benchmark === null ? "—" : pct(item.benchmark)}</td><td>{item.realized === null || item.benchmark === null ? "—" : pct(item.realized - item.benchmark)}</td><td><span className={`evidence-status ${item.status}`}>{item.status === "valid" ? "已计入" : "未计入"}</span></td></tr>) : <tr><td colSpan={7} className="empty-evidence">没有符合当前筛选条件的样本</td></tr>}</tbody></table></div><footer>当前为原型抽样展示 · 完整结果将支持分页与导出证据快照</footer></article>
          </div>}

          {activeTab === "spec" && <div className="backtest-tab-panel" role="tabpanel">
            <div className="backtest-spec-grid">
              <article className="backtest-card spec-card"><div className="backtest-card-head"><div><span className="eyebrow">运行身份</span><h2>可复现信息</h2></div><Database size={20} /></div><dl><div><dt>运行 ID</dt><dd><code>{strategy.runId}</code></dd></div><div><dt>评估对象</dt><dd>{strategy.name} <small>{strategy.version}</small></dd></div><div><dt>输出类型</dt><dd>{strategy.sourceType}</dd></div><div><dt>运行状态</dt><dd><span className="run-complete"><CheckCircle2 size={14} />已完成</span></dd></div></dl></article>
              <article className="backtest-card spec-card"><div className="backtest-card-head"><div><span className="eyebrow">本次用户口径</span><h2>评估参数</h2></div><SlidersHorizontal size={20} /></div><dl><div><dt>历史区间</dt><dd>{strategy.period.start} 至 {strategy.period.end}</dd></div><div><dt>结果观察窗</dt><dd>{strategy.horizonDays} 个交易日</dd></div><div><dt>真实值定义</dt><dd>复权收盘价区间收益</dd></div><div><dt>对比基准</dt><dd>沪深300同期收益</dd></div><div><dt>置信水平</dt><dd>{strategy.confidenceLevel}%</dd></div></dl></article>
            </div>
            <article className="backtest-card boundary-card"><div><ShieldCheck size={21} /><div><span className="eyebrow">模块边界</span><h2>本运行只评价已存在的历史输出</h2></div></div><div className="boundary-items"><span><CheckCircle2 size={16} />不产生新策略</span><span><CheckCircle2 size={16} />不生成买卖信号</span><span><CheckCircle2 size={16} />不模拟仓位与成交</span><span><CheckCircle2 size={16} />不替上游调整模型</span></div></article>
          </div>}
        </>}
      </section>
    </div>
  </main>;
}
