import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ToolIdentityArtifact from "./ToolIdentityArtifact";

describe("ToolIdentityArtifact", () => {
  it("shows the stable invocation identity and purpose for an active custom tool", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "active",
        version: "2",
        summary: "判断当前市场状态并给出买入决策。",
        asset_ref: {
          kind: "tool",
          name: "ct_market_buy_decision",
          display_name: "大盘状态与买入决策",
          description: "判断当前市场状态并给出买入决策。",
          revision: 2,
        },
        details: {
          inputs: [{ name: "stock", required: true, description: "股票代码" }],
          outputs: [{ name: "decision", description: "买入决策" }],
        },
      }}
      onUseAsset={() => undefined}
    />);

    expect(html).toContain("大盘状态与买入决策");
    expect(html).toContain("判断当前市场状态并给出买入决策");
    expect(html).toContain("$ct_market_buy_decision");
    expect(html).toContain("已启用");
    expect(html).toContain("立即使用");
  });

  it("does not offer execution while the implementation is still a draft", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "draft",
        asset_ref: {
          kind: "tool",
          name: "ct_demo",
          display_name: "演示工具",
        },
      }}
      onUseAsset={() => undefined}
    />);

    expect(html).toContain("$ct_demo");
    expect(html).toContain("待确认");
    expect(html).not.toContain("立即使用");
  });

  it("shows a schema-driven interactive test workbench for the immutable revision", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "draft",
        version: "3",
        asset_ref: {
          kind: "tool",
          name: "ct_batch_quality",
          display_name: "批量质量评价",
          revision: 3,
        },
        details: {
          input_schema: {
            type: "object",
            required: ["stock_codes"],
            properties: {
              stock_codes: {
                type: "array",
                title: "股票列表",
                description: "待评价的股票代码。",
                items: { type: "string" },
              },
              threshold: { type: "number", title: "阈值" },
            },
          },
          sample_input: { stock_codes: ["600519.SH"], threshold: 0.8 },
        },
      }}
    />);

    expect(html).toContain("交互测试台");
    expect(html).toContain("固定测试候选版本 3");
    expect(html).toContain("股票列表");
    expect(html).toContain("600519.SH");
    expect(html).toContain("运行这个测试用例");
  });

  it("keeps a JSON test surface for open input schemas without declared fields", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "draft",
        summary: "运行开放参数工具。",
        asset_ref: {
          kind: "tool",
          name: "ct_open_payload",
          display_name: "开放参数工具",
          revision: 2,
        },
        details: {
          input_schema: { type: "object", additionalProperties: true },
          sample_input: { query: "测试" },
        },
      }}
    />);

    expect(html).toContain("交互测试台");
    expect(html).toContain("测试输入 JSON");
    expect(html).toContain("&quot;query&quot;: &quot;测试&quot;");
  });

  it("reuses the authoritative Design flow and summarizes implementation evidence", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "draft",
        summary: "识别放量突破信号。",
        asset_ref: {
          kind: "tool",
          name: "ct_volume_breakout",
          display_name: "放量突破识别",
        },
        details: {
          modules: [
            { name: "行情准备", role: "读取所需行情" },
            { name: "信号判断", role: "按阈值输出结果" },
          ],
          runtime: "策略运行 Wrapper 负责标的展开与交易日处理",
          verification: { status: "passed", summary: "3 项代表性样例通过" },
          strategy_compatibility: {
            strategy_wrapper_ready: true,
            portfolio_backtest_contract_ready: true,
            execution_shape: "per_instrument",
            summary: "支持逐标的运行，可接入日频选股回测。",
          },
          design_flow: {
            mermaid: "flowchart LR\n  A[读取行情] --> B{成交量比 >= 1.5}\n  B -->|是| C[命中]",
          },
        },
      }}
    />);

    expect(html).toContain("实现概览");
    expect(html).toContain("2 个");
    expect(html).toContain("3 项代表性样例通过");
    expect(html).toContain("运行可用 · 回测契约已声明");
    expect(html).toContain("已确认的设计主流程");
    expect(html).toContain("沿用 Design 权威版本，不从代码重新推导");
    expect(html).toContain("正在绘制流程图");
  });

  it("keeps a runnable strategy positive when only portfolio backtest integration is limited", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "draft",
        asset_ref: { kind: "tool", name: "ct_intraday_signal", display_name: "盘中信号" },
        details: {
          finance_tool_profile: {
            protocol: "finance_tool_profile.v1",
            family: "strategy",
            execution_shape: "entity_local",
            output_semantic: "signal",
            summary: "盘中信号",
          },
          strategy_compatibility: {
            strategy_wrapper_ready: true,
            portfolio_backtest_contract_ready: false,
            execution_shape: "per_instrument",
            summary: "工具可正常运行；当前回测平台缺少分钟级组合回放契约。",
          },
        },
      }}
    />);

    expect(html).toContain("运行可用 · 组合回测受限");
    expect(html).toContain("策略工具 · 单实体独立 · 投资信号");
    expect(html).not.toContain("暂不兼容");
    expect(html).toContain("工具可正常运行");
  });

  it("shows an analytics profile without implying strategy or backtest restrictions", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "active",
        asset_ref: { kind: "tool", name: "ct_market_strength", display_name: "大盘强度" },
        details: {
          finance_tool_profile: {
            protocol: "finance_tool_profile.v1",
            family: "analytics",
            execution_shape: "aggregate_context",
            output_semantic: "assessment",
            summary: "大盘整体 · 点时快照",
          },
          strategy_compatibility: {
            strategy_wrapper_ready: false,
            portfolio_backtest_contract_ready: false,
            summary: "非策略工具不接组合回测。",
          },
        },
      }}
      onUseAsset={() => undefined}
    />);

    expect(html).toContain("分析工具 · 整体分析 · 评估结论");
    expect(html).toContain("立即使用");
    expect(html).not.toContain("运行与回测");
    expect(html).not.toContain("组合回测受限");
    expect(html).not.toContain("非策略工具不接组合回测");
  });

  it("keeps a planned action visibly design-only and non-executable", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "active",
        asset_ref: { kind: "tool", name: "ct_place_order", display_name: "条件下单" },
        details: {
          finance_tool_profile: {
            protocol: "finance_tool_profile.v1",
            family: "action",
            execution_shape: "portfolio_stateful",
            output_semantic: "action_receipt",
            summary: "实盘动作",
            execution_policy: "planned_non_executable",
          },
        },
      }}
      onUseAsset={() => undefined}
    />);

    expect(html).toContain("动作工具 · 组合状态 · 动作回执");
    expect(html).toContain("仅设计，未开放执行");
    expect(html).toContain("资产标识");
    expect(html).toContain("ct_place_order");
    expect(html).not.toContain("$ct_place_order");
    expect(html).not.toContain("调用名");
    expect(html).not.toContain("立即使用");
  });

  it("marks a strategy profile without compatibility evidence as incomplete", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "draft",
        asset_ref: { kind: "tool", name: "ct_ranked_selection", display_name: "横截面选股" },
        details: {
          finance_tool_profile: {
            protocol: "finance_tool_profile.v1",
            family: "strategy",
            execution_shape: "cross_sectional",
            output_semantic: "ranked_selection",
            summary: "横截面排名",
          },
        },
      }}
    />);

    expect(html).toContain("策略工具 · 横截面比较 · 排名筛选");
    expect(html).toContain("运行契约待完善");
    expect(html).toContain("尚未提供 Strategy Wrapper 与组合回测契约证据");
  });

  it("keeps optional source code inside the collapsed implementation details", () => {
    const html = renderToStaticMarkup(<ToolIdentityArtifact
      data={{
        lifecycle: "draft",
        asset_ref: { kind: "tool", name: "ct_demo", display_name: "演示工具" },
        details: {
          files: [{ name: "strategy.py", language: "python", content: "def run(inputs):\n    return inputs" }],
        },
      }}
    />);

    expect(html).toContain("查看输入、输出、模块与代码");
    expect(html).toContain("实现代码");
    expect(html).toContain("strategy.py");
    expect(html).toContain("按需查看");
  });
});
