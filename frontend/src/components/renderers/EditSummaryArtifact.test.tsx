import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import BlockRenderer from "../BlockRenderer";
import EditSummaryArtifact from "./EditSummaryArtifact";

describe("EditSummaryArtifact", () => {
  it("shows the edited tool, impact, concise diff, evidence and inactive candidate state", () => {
    const html = renderToStaticMarkup(<EditSummaryArtifact data={{
      tool_name: "ct_market_buy_decision",
      display_name: "大盘状态与买入决策",
      route: "local_patch",
      impact_summary: "只调整成交额过滤口径，不改变输入与输出。",
      base_revision: 3,
      candidate_revision: 4,
      affected_assets: ["implementation", { name: "成交额过滤模块", description: "判断逻辑" }],
      changes: [{
        field: "成交额阈值",
        operation: "update",
        before: "5000 万元",
        after: "1 亿元",
        reason: "降低低流动性样本干扰",
      }],
      verification: {
        status: "passed",
        summary: "代表性样例均通过。",
        cases: [{
          name: "贵州茅台样例",
          status: "passed",
          summary: "新阈值生效且输出字段保持一致。",
          input: { stock: "600519.SH" },
          actual: { decision: "可买" },
        }],
      },
    }} />);

    expect(html).toContain("大盘状态与买入决策");
    expect(html).toContain("$ct_market_buy_decision");
    expect(html).toContain("局部修改");
    expect(html).toContain("只调整成交额过滤口径");
    expect(html).toContain("工具实现");
    expect(html).toContain("成交额过滤模块");
    expect(html).toContain("5000 万元");
    expect(html).toContain("1 亿元");
    expect(html).toContain("贵州茅台样例");
    expect(html).toContain("验证通过");
    expect(html).toContain("候选版本尚未生效");
    expect(html).toContain('aria-label="版本变化"');
  });

  it("keeps loading and empty evidence explicit", () => {
    const html = renderToStaticMarkup(<EditSummaryArtifact data={{
      tool_name: "ct_demo",
      display_name: "演示工具",
      route: "full_revision",
      base_revision: 1,
      candidate_revision: 2,
      changes: [],
      affected_assets: [],
      verification: {
        status: "running",
        summary: "正在执行回归样例。",
        cases: [],
      },
    }} />);

    expect(html).toContain("完整设计与实现");
    expect(html).toContain("正在验证候选版本");
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain("暂无逐项验证证据");
    expect(html).toContain("本轮没有可展示的逐项差异");
    expect(html).toContain("未单独列出关联资产");
  });

  it("announces failed verification as an alert", () => {
    const html = renderToStaticMarkup(<EditSummaryArtifact data={{
      tool_name: "ct_demo",
      base_revision: 1,
      candidate_revision: 2,
      changes: ["保持候选版本，不覆盖当前版本"],
      verification: {
        status: "failed",
        summary: "一个关键样例未通过。",
        cases: [{ name: "边界样例", status: "failed", error: "输出不一致" }],
      },
    }} />);

    expect(html).toContain('role="alert"');
    expect(html).toContain("验证未通过");
    expect(html).toContain("边界样例");
    expect(html).toContain("输出不一致");
  });

  it("is selected by the shared artifact renderer without duplicating the surface title", () => {
    const html = renderToStaticMarkup(<BlockRenderer block={{
      block_id: "custom_tool_edit_summary",
      block_type: "artifact",
      title: "工具修改结果",
      data: {
        artifact_type: "finance.custom_tool_edit",
        tool_name: "ct_demo",
        display_name: "演示工具",
        base_revision: 1,
        candidate_revision: 2,
        verification: { status: "passed", cases: [] },
      },
    }} />);

    expect(html).toContain('data-renderer="artifact.spec"');
    expect(html).toContain("edit-summary-artifact");
    expect(html).not.toContain("surface-title");
  });

  it("uses progressive disclosure when changes or evidence are numerous", () => {
    const html = renderToStaticMarkup(<EditSummaryArtifact data={{
      tool_name: "ct_demo",
      base_revision: 1,
      candidate_revision: 2,
      changes: Array.from({ length: 6 }, (_, index) => `变更内容 ${index + 1}`),
      verification: {
        status: "passed",
        cases: Array.from({ length: 5 }, (_, index) => ({ name: `样例 ${index + 1}`, status: "passed" })),
      },
    }} />);

    expect(html).toContain("查看其余 2 项变更");
    expect(html).toContain("查看其余 2 项验证证据");
  });
});
