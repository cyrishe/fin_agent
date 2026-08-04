import { afterEach, describe, expect, it, vi } from "vitest";
import { dispatchChat, loadInvocationAssets } from "./api";

const jsonResponse = (value: unknown, status = 200) => new Response(
  JSON.stringify(value),
  { status, headers: { "Content-Type": "application/json" } },
);

describe("invocable asset API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("prefers the unified invocable asset contract and retains searchable metadata", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      ok: true,
      items: [{
        ref: "tool:ct_market_buy_decision",
        kind: "tool",
        name: "ct_market_buy_decision",
        display_name: "大盘状态与买入决策",
        summary: "根据大盘和个股 K 线输出可买或不可买。",
        invocation: "$ct_market_buy_decision",
        input_fields: [{
          name: "stocks",
          label: "股票列表",
          description: "待判断的股票代码",
          type: "array",
          required: true,
          default_value: null,
        }],
        aliases: ["大盘买入"],
        tags: ["择时", "风控"],
        custom_tool: true,
        version: "1",
        revision: 1,
      }],
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadInvocationAssets()).resolves.toEqual([expect.objectContaining({
      ref: "tool:ct_market_buy_decision",
      kind: "tool",
      name: "ct_market_buy_decision",
      displayName: "大盘状态与买入决策",
      summary: "根据大盘和个股 K 线输出可买或不可买。",
      invocation: "$ct_market_buy_decision",
      aliases: ["大盘买入"],
      tags: ["择时", "风控"],
      customTool: true,
      version: "1",
      revision: 1,
      inputFields: [{
        name: "stocks",
        label: "股票列表",
        description: "待判断的股票代码",
        type: "array",
        required: true,
      }],
    })]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/assets/invocable");
  });

  it("falls back to the legacy tool and skill catalogs while deriving refs and input fields", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ ok: false, error: "not found" }, 404))
      .mockResolvedValueOnce(jsonResponse({
        ok: true,
        items: [{
          tool_name: "finance_data_query",
          display_name: "金融数据协议查询",
          description: "查询金融数据",
          input_schema: {
            type: "object",
            required: ["question"],
            properties: {
              question: { type: "string", title: "查询要求", description: "自然语言金融问题" },
            },
          },
        }],
      }))
      .mockResolvedValueOnce(jsonResponse({
        ok: true,
        items: [{
          skill_name: "stock_deep_dive",
          purpose: "分析单只股票",
          tags: ["stock"],
        }],
      }));
    vi.stubGlobal("fetch", fetchMock);

    const assets = await loadInvocationAssets();
    expect(assets).toHaveLength(2);
    expect(assets[0]).toEqual(expect.objectContaining({
      ref: "tool:finance_data_query",
      invocation: "$finance_data_query",
      inputFields: [expect.objectContaining({
        name: "question",
        label: "查询要求",
        required: true,
      })],
    }));
    expect(assets[1]).toEqual(expect.objectContaining({
      ref: "skill:stock_deep_dive",
      displayName: "stock_deep_dive",
      summary: "分析单只股票",
      tags: ["stock"],
    }));
  });

  it("does not revive retired or internal skills from the legacy fallback catalog", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ ok: false, error: "not found" }, 404))
      .mockResolvedValueOnce(jsonResponse({ ok: true, items: [] }))
      .mockResolvedValueOnce(jsonResponse({
        ok: true,
        items: [
          {
            skill_name: "stock_deep_dive",
            availability: { lifecycle: "retired", retrieval_mode: "direct_only" },
            auth: "public",
          },
          {
            skill_name: "quant_factor_screening",
            availability: { lifecycle: "active", retrieval_mode: "retrievable" },
            auth: "internal",
          },
        ],
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadInvocationAssets()).resolves.toEqual([]);
  });

  it("forwards the stable ref together with the compatible kind and name", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await dispatchChat({
      text: "分析 600519",
      threadId: 7,
      attachmentIds: [],
      selectedAsset: {
        ref: "tool:ct_market_buy_decision",
        kind: "tool",
        name: "ct_market_buy_decision",
      },
    });

    const options = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(options.body)).selected_asset).toEqual({
      ref: "tool:ct_market_buy_decision",
      kind: "tool",
      name: "ct_market_buy_decision",
    });
  });
});
