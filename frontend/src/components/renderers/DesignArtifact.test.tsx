import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import DesignArtifact from "./DesignArtifact";

describe("DesignArtifact", () => {
  it("keeps the design summary concise and puts the complete document behind details", () => {
    const html = renderToStaticMarkup(<DesignArtifact
      content=""
      data={{
        details: {
          understanding: { requirement_brief: "这是一段很长的需求原文，不应作为设计标题再次展示。" },
          document: "## 工具概述\n判断股票是否满足条件。\n\n| 输入 | 输出 |\n| --- | --- |\n| 股票代码 | 判断结果 |",
        },
      }}
    />);

    expect(html).toContain("需求与目标");
    expect(html).toContain("这是一段很长的需求原文，不应作为设计标题再次展示。");
    expect(html).toContain("查看完整设计细节");
    expect(html).toContain("<h2>工具概述</h2>");
    expect(html).toContain("<table>");
    expect(html).not.toContain("## 工具概述");
  });

  it("keeps the mandatory flow area visible when the backend has not supplied a flow", () => {
    const html = renderToStaticMarkup(<DesignArtifact
      content=""
      data={{
        details: {
          understanding: { requirement_brief: "筛选满足量价条件的股票。" },
          rules: [{ name: "涨幅条件", logic: "涨幅大于 5%" }],
        },
      }}
    />);

    expect(html).toContain("核心流程");
    expect(html).toContain("流程图尚未生成");
    expect(html).toContain("当前设计缺少必须的主流程，不应进入确认或编码。");
  });

  it("renders the authoritative Design flow and keeps branch thresholds visible in it", () => {
    const html = renderToStaticMarkup(<DesignArtifact
      content=""
      data={{
        details: {
          understanding: { requirement_brief: "判断放量突破。" },
          mermaid: "flowchart LR\n  A[读取行情] --> B{成交量比 >= 1.5}\n  B -->|是| C[输出命中]\n  B -->|否| D[输出未命中]",
        },
      }}
    />);

    expect(html).toContain("核心流程");
    expect(html).toContain("展示关键判断、分支条件与阈值");
    expect(html).toContain("正在绘制流程图");
    expect(html).not.toContain("流程图尚未生成");
  });

  it("shows the finance tool profile beside the design goal", () => {
    const html = renderToStaticMarkup(<DesignArtifact
      content=""
      data={{
        details: {
          understanding: { requirement_brief: "计算大盘强度。" },
          finance_tool_profile: {
            protocol: "finance_tool_profile.v1",
            family: "analytics",
            execution_shape: "aggregate_context",
            output_semantic: "assessment",
          },
        },
      }}
    />);

    expect(html).toContain("计算大盘强度");
    expect(html).toContain("金融工具画像");
    expect(html).toContain("分析工具 · 整体分析 · 评估结论");
  });

  it("makes the non-executable action boundary explicit during Design", () => {
    const html = renderToStaticMarkup(<DesignArtifact
      content=""
      data={{
        details: {
          understanding: { requirement_brief: "设计条件下单工具。" },
          finance_tool_profile: {
            protocol: "finance_tool_profile.v1",
            family: "action",
            execution_shape: "portfolio_stateful",
            output_semantic: "action_receipt",
            execution_policy: "planned_non_executable",
          },
        },
      }}
    />);

    expect(html).toContain("动作工具 · 组合状态 · 动作回执");
    expect(html).toContain("仅设计，未开放实现");
  });
});
