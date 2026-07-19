import { describe, expect, it } from "vitest";
import { completeSuggestion, filterSuggestions, findInvocation, type ComposerSuggestion } from "./composerSuggestions";

const assets: ComposerSuggestion[] = [
  { id: "tool:finance_data_query", kind: "tool", value: "$finance_data_query", label: "$finance_data_query", description: "金融数据查询" },
  { id: "skill:stock_deep_dive", kind: "skill", value: "$stock_deep_dive", label: "$stock_deep_dive", description: "个股深度分析" },
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

  it("keeps a matching skill visible when many tools also match", () => {
    const crowded = [
      ...Array.from({ length: 9 }, (_, index): ComposerSuggestion => ({ id: `tool:stock_${index}`, kind: "tool", value: `$stock_${index}`, label: `$stock_${index}`, description: "股票工具" })),
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

  it("replaces only the active invocation and returns the completion cursor", () => {
    const value = "请调用 $fin";
    const range = findInvocation(value, value.length)!;
    const result = completeSuggestion(value, range, assets[0]);
    expect(result.value).toBe("请调用 $finance_data_query ");
    expect(result.cursor).toBe(result.value.length);
  });

  it("does not treat URL slashes or ordinary dollar amounts as invocations", () => {
    expect(findInvocation("https://example.com", 19)).toBeNull();
    expect(findInvocation("价格是100$", 7)).toBeNull();
  });
});
