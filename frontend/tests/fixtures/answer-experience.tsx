// Development-only fixture: fabricated data, no account or production requests.
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import MessageItem from "../../src/components/MessageItem";
import RunPanel from "../../src/components/RunPanel";
import { applyStreamEvent, initialRun } from "../../src/surface";
import type { AgentRun, SurfaceBlock } from "../../src/types";
import "../../src/styles.css";

const answer: SurfaceBlock = { block_id: "answer", block_type: "narrative", semantic: "finance.answer", title: "分析回答", content: `基于示例企业两期年度数据，考察收入、利润与现金流的匹配程度。以下为界面验收用虚构数据。

> **增长保持，但现金转化需要关注。** 收入与利润同向增长，经营现金流增速偏慢；现有数字足以描述变化，尚不足以确认一次性因素。

## 增长与盈利

| 关键指标 | 上期 | 本期 | 变化 |
|---|---:|---:|---:|
| 营业收入 | 100 亿元 | 108 亿元 | +8.0% |
| 归母净利润 | 20 亿元 | 22 亿元 | +10.0% |
| 经营现金流 | 18 亿元 | 19 亿元 | +5.6% |

利润增速高于收入，利润率有所改善；经营现金流仍为正，但增长速度低于利润。

## 证据与待核实事项

- **盈利端：** 净利率由 20.0% 升至约 20.4%。
- **现金端：** 经营现金流与利润之比有所回落，需结合回款与营运资本变化解释。
- **一次性因素：** 尚缺扣非利润及附注明细，暂不判断是否为主要驱动。` };
const table: SurfaceBlock = { block_id: "financial_rows", block_type: "data", kind: "data", semantic: "finance.financial.records", title: "年度财务明细", presentation_hint: { preferred_renderer: "data.table" }, domain_context: { source: "示例 · 年度财务数据" }, payload: { shape: "records", data: { columns: ["期间", "收入", "利润"], rows: Array.from({ length: 22 }, (_, i) => ({ 期间: `示例 ${i + 1}`, 收入: 100 + i, 利润: 20 + i })), row_count: 22 } } };
const skill: SurfaceBlock = { block_id: "runtime_skill_example", block_type: "status", title: "加载方法 · 财报分析", content: "已加载专业方法，用于指导本轮取证与分析。", data: { role: "process", status: "completed", skill_id: "earnings-analysis", display_name: "财报分析" } };
function Fixture() {
  const [mode, setMode] = useState("done");
  const run: AgentRun = mode === "loading" ? applyStreamEvent(initialRun("正在按专业方法取证"), { event: "block", ...skill }) : {
    status: "done", summary: "本轮处理完成", durationMs: 49000,
    artifacts: mode === "data" ? [table] : [answer, table, { ...table, block_id: "segment_rows", title: "业务分部明细" }],
    process: mode === "data" ? [] : [skill, { block_id: "query", block_type: "status", title: "查询年度财务数据", content: "已取得 22 条示例记录。", data: { role: "process", status: "completed" } }],
  };
  const noop = () => undefined;
  return <div style={{ height: "100vh", overflow: "auto", background: "#f7fafc" }}>
    <header style={{ padding: "16px 24px", display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}><strong>分析体验验收</strong><span>虚构数据 · 不连接生产</span>{[["done", "已完成分析"], ["loading", "模拟 Skill 加载"], ["data", "纯取数"]].map(([key, label]) => <button key={key} onClick={() => setMode(key)}>{label}</button>)}</header>
    <main style={{ display: "flex", maxWidth: 1300, margin: "0 auto", padding: 16, gap: 20 }}>
      <div style={{ flex: 1, minWidth: 0 }}><MessageItem key={mode} message={{ id: "fixture", role: "assistant", content: "", run, createdAt: Date.now() }} interactionDrafts={{}} selectedInteractions={{}} submittedInteractions={new Set()} disabled={false} onDraftChange={noop} onRequestCustomAnswer={noop} onClearCustomAnswer={noop} onSubmitDraft={noop} onInteraction={noop} onRequestFeedback={noop} onSubmitFeedback={noop} onUseAsset={noop} /></div>
      <div className="fixture-sidebar" style={{ width: 300, flexShrink: 0, background: "#eff5f8" }}><RunPanel run={run} /></div>
    </main><style>{`@media(max-width:850px){.fixture-sidebar{display:none}}`}</style>
  </div>;
}
const root = createRoot(document.getElementById("root")!);
root.render(<Fixture />);
if (import.meta.hot) import.meta.hot.dispose(() => root.unmount());
