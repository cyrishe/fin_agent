import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { InvocationAsset } from "../types";
import Composer from "./Composer";

const asset: InvocationAsset = {
  ref: "tool:ct_market_buy_decision",
  kind: "tool",
  name: "ct_market_buy_decision",
  displayName: "大盘状态与买入决策",
  summary: "根据大盘和个股 K 线输出可买或不可买。",
  description: "根据大盘和个股 K 线输出可买或不可买。",
  invocation: "$ct_market_buy_decision",
  aliases: ["大盘买入"],
  tags: ["择时"],
  customTool: true,
  editable: true,
  inputFields: [{
    name: "stocks",
    label: "股票列表",
    description: "待判断的股票代码",
    type: "array",
    required: true,
  }],
  inputSchema: {},
  sampleInput: {},
};

const baseProps = {
  value: "",
  onChange: vi.fn(),
  onSend: vi.fn(),
  busy: false,
  assets: [asset],
  attachments: [],
  onFiles: vi.fn(),
  onRemoveAttachment: vi.fn(),
  onSelectAsset: vi.fn(),
  onClearSelectedAsset: vi.fn(),
};

describe("Composer asset identity", () => {
  it("renders a selected asset as a structured chip with its name, id, summary and inputs", () => {
    const html = renderToStaticMarkup(<Composer {...baseProps} selectedAsset={asset} />);
    expect(html).toContain("大盘状态与买入决策");
    expect(html).toContain("$ct_market_buy_decision");
    expect(html).toContain("根据大盘和个股 K 线输出可买或不可买。");
    expect(html).toContain("&lt;股票列表&gt;");
    expect(html).toContain("个人工具");
    expect(html).toContain('role="combobox"');
    expect(html).toContain('aria-autocomplete="list"');
  });

  it("keeps the asset outside the textarea value", () => {
    const html = renderToStaticMarkup(
      <Composer {...baseProps} value="分析贵州茅台" selectedAsset={asset} />,
    );
    expect(html).toContain(">分析贵州茅台</textarea>");
    expect(html).not.toContain(">$ct_market_buy_decision 分析贵州茅台</textarea>");
  });

  it("shows fuzzy personal-tool choices after the edit command", () => {
    const html = renderToStaticMarkup(
      <Composer {...baseProps} value="/custom_tool edit 大盘" selectedAsset={null} />,
    );
    expect(html).toContain("选择要修改的个人工具");
    expect(html).toContain("大盘状态与买入决策");
    expect(html).toContain("ct_market_buy_decision");
    expect(html).toContain("根据大盘和个股 K 线输出可买或不可买。");
  });
});
