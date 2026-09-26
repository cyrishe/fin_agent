import { describe, expect, it } from "vitest";
import {
  completeSuggestion,
  completeEditTool,
  filterSuggestions,
  findInvocation,
  removeInvocation,
  type ComposerSuggestion,
} from "./composerSuggestions";

const assets: ComposerSuggestion[] = [
  {
    id: "tool:finance_data_query",
    kind: "tool",
    value: "$finance_data_query",
    label: "金融数据协议查询",
    description: "查询股票、指数和板块等金融数据",
    keywords: ["行情查询", "finance"],
  },
  {
    id: "skill:stock_deep_dive",
    kind: "skill",
    value: "$stock_deep_dive",
    label: "个股深度分析",
    description: "结合行情、资金、研报和新闻分析一只股票",
    keywords: ["股票分析", "投顾"],
  },
];

describe("composer suggestions", () => {
  it("opens slash commands again after the user deletes and retypes the prefix", () => {
    const range = findInvocation("/custom", 7);
    expect(range?.trigger).toBe("/");
    expect(filterSuggestions(range, assets).map((item) => item.value)).toContain("/custom_tool create");
  });

  it("supports command aliases without changing the inserted canonical command", () => {
    expect(filterSuggestions(findInvocation("/ct", 3), assets).map((item) => item.value)).toEqual([
      "/custom_tool commit", "/custom_tool create", "/custom_tool edit",
    ]);
  });

  it("matches real tool and skill assets after a dollar trigger", () => {
    expect(filterSuggestions(findInvocation("$fin", 4), assets)[0].value).toBe("$finance_data_query");
    expect(filterSuggestions(findInvocation("请用 $stock", 9), assets)[0].value).toBe("$stock_deep_dive");
  });

  it("ranks canonical name, display name, summary, tags and aliases deterministically", () => {
    expect(filterSuggestions(findInvocation("$金融数据", 5), assets)[0].value).toBe("$finance_data_query");
    expect(filterSuggestions(findInvocation("$行情查询", 5), assets)[0].value).toBe("$finance_data_query");
    expect(filterSuggestions(findInvocation("$研报", 3), assets)[0].value).toBe("$stock_deep_dive");
  });

  it("normalizes separators and tolerates a small typo without changing the canonical value", () => {
    expect(filterSuggestions(findInvocation("$finace", 7), assets)[0].value).toBe("$finance_data_query");
    expect(filterSuggestions(findInvocation("$stockdeepdive", 14), assets)[0].value).toBe("$stock_deep_dive");
  });

  it("keeps a matching skill visible when many tools also match", () => {
    const crowded = [
      ...Array.from({ length: 9 }, (_, index): ComposerSuggestion => ({ id: `tool:stock_${index}`, kind: "tool", value: `$stock_${index}`, label: `股票工具 ${index}`, description: "股票工具" })),
      assets[1],
    ];
    expect(filterSuggestions(findInvocation("$stock", 6), crowded).map((item) => item.value)).toContain("$stock_deep_dive");
  });

  it("supports tool and skill namespaces", () => {
    expect(filterSuggestions(findInvocation("$tool:fin", 9), assets).map((item) => item.kind)).toEqual(["tool"]);
    expect(filterSuggestions(findInvocation("$skill:stock", 12), assets).map((item) => item.kind)).toEqual(["skill"]);
  });

  it("closes suggestions after a complete command starts accepting arguments", () => {
    expect(filterSuggestions(findInvocation("/custom_tool create ", 20), assets)).toEqual([]);
    expect(filterSuggestions(findInvocation("/custom_tool ", 13), assets)).not.toHaveLength(0);
  });

  it("turns custom_tool edit into an editable personal-tool lookup", () => {
    const editableTool: ComposerSuggestion = {
      id: "tool:ct_market_buy_decision",
      kind: "tool",
      value: "$ct_market_buy_decision",
      assetName: "ct_market_buy_decision",
      label: "大盘状态与买入决策",
      description: "根据大盘和个股 K 线判断是否可买",
      keywords: ["择时", "买入"],
      customTool: true,
      editable: true,
    };
    const publicTool: ComposerSuggestion = {
      ...editableTool,
      id: "tool:ct_public_tool",
      value: "$ct_public_tool",
      assetName: "ct_public_tool",
      label: "公开工具",
      editable: false,
    };
    const range = findInvocation("/custom_tool edit 大盘", 20);

    expect(range).toMatchObject({ context: "edit_tool", query: "大盘" });
    expect(filterSuggestions(range, [...assets, publicTool, editableTool])).toEqual([editableTool]);
    expect(completeEditTool("/custom_tool edit 大盘", range!, editableTool)).toEqual({
      value: "/custom_tool edit ct_market_buy_decision ",
      cursor: 41,
    });
  });

  it("replaces only the active invocation and returns the completion cursor", () => {
    const value = "请调用 $fin";
    const range = findInvocation(value, value.length)!;
    const result = completeSuggestion(value, range, assets[0]);
    expect(result.value).toBe("请调用 $finance_data_query ");
    expect(result.cursor).toBe(result.value.length);
  });

  it("removes an accepted asset invocation so the structured chip owns its identity", () => {
    const value = "请用 $fin 分析贵州茅台";
    const range = findInvocation(value, "请用 $fin".length)!;
    const result = removeInvocation(value, range);
    expect(result).toEqual({ value: "分析贵州茅台", cursor: 0 });

    const onlyInvocation = "$fin";
    expect(removeInvocation(onlyInvocation, findInvocation(onlyInvocation, onlyInvocation.length)!)).toEqual({
      value: "",
      cursor: 0,
    });
  });

  it("uses the real cursor position for an invocation in the middle of the draft", () => {
    const value = "先看 $fin 再总结";
    const cursor = "先看 $fin".length;
    const range = findInvocation(value, cursor);
    expect(range).toMatchObject({ query: "$fin", start: 3, end: cursor });
  });

  it("deduplicates repeated catalog entries and prefers a personal tool on an otherwise equal match", () => {
    const repeated: ComposerSuggestion[] = [
      { id: "tool:shared", kind: "tool", value: "$shared", label: "同名能力", description: "分析股票" },
      { id: "tool:shared", kind: "tool", value: "$shared", label: "同名能力", description: "分析股票" },
      { id: "tool:personal", kind: "tool", value: "$personal", label: "同名能力", description: "分析股票", customTool: true },
    ];
    expect(filterSuggestions(findInvocation("$同名能力", 5), repeated).map((item) => item.id)).toEqual([
      "tool:personal",
      "tool:shared",
    ]);
  });

  it("does not treat URL slashes or ordinary dollar amounts as invocations", () => {
    expect(findInvocation("https://example.com", 19)).toBeNull();
    expect(findInvocation("价格是100$", 7)).toBeNull();
    expect(findInvocation("价格是 $100", 8)).toBeNull();
  });
});
