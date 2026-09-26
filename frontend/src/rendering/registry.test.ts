import { describe, expect, it } from "vitest";
import type { SurfaceBlock } from "../types";
import { normalizeCandles, normalizeGraph, normalizeRenderObject } from "./normalize";
import { chooseRenderer } from "./registry";

const block = (input: Partial<SurfaceBlock>): SurfaceBlock => ({
  block_id: "block-1",
  block_type: "data",
  ...input,
});

describe("renderer object normalization", () => {
  it("maps Agent Surface finance.ohlcv data to the K-line renderer", () => {
    const object = normalizeRenderObject(block({
      kind: "data",
      semantic: "finance.ohlcv",
      payload: {
        shape: "timeseries",
        content_type: "finance.ohlcv",
        data: {
          candles: [{ time: "2026-07-10", open: 10, high: 11, low: 9.8, close: 10.8, volume: 1000 }],
        },
      },
      domain_context: { frequency: "1d", adjustment: "forward" },
    }));

    expect(object.kind).toBe("data");
    expect(object.shape).toBe("timeseries");
    expect(chooseRenderer(object)).toBe("finance.kline");
    expect(normalizeCandles(object.payload)).toHaveLength(1);
  });

  it("keeps legacy K-line arrays compatible with the typed renderer", () => {
    const object = normalizeRenderObject(block({
      block_type: "kline",
      data: { candles: [["2026-07-10", 10, 10.8, 9.8, 11, 1000, 8]] },
    }));
    const candles = normalizeCandles(object.payload);

    expect(chooseRenderer(object)).toBe("finance.kline");
    expect(candles[0]).toMatchObject({ open: 10, close: 10.8, low: 9.8, high: 11 });
  });

  it("treats chatflow as a graph presentation alias instead of a business kind", () => {
    const object = normalizeRenderObject(block({
      block_type: "chatflow",
      data: {
        nodes: [{ id: "a", label: "查询" }, { id: "b", label: "判断" }],
        edges: [{ from: "a", to: "b" }],
      },
    }));

    expect(object.kind).toBe("data");
    expect(object.shape).toBe("graph");
    expect(chooseRenderer(object)).toBe("diagram.flow");
    expect(normalizeGraph(object.payload).edges).toEqual([{ from: "a", to: "b" }]);
  });

  it("maps code artifacts with runtime state to the code renderer", () => {
    const object = normalizeRenderObject(block({
      block_type: "artifact",
      kind: "artifact",
      semantic: "finance.tool_implementation",
      payload: {
        artifact_type: "finance.tool_code",
        content_type: "text/x-python",
        content: { files: [{ name: "main", language: "python", content: "def run(): pass" }] },
        runtime: { status: "succeeded", stdout: "ok" },
      },
    }));

    expect(chooseRenderer(object)).toBe("artifact.code");
  });

  it("falls back safely for an unknown semantic object", () => {
    const object = normalizeRenderObject(block({
      kind: "data",
      payload: { shape: "record", content_type: "application/x-unknown", data: { opaque: true } },
    }));
    expect(chooseRenderer(object)).toBe("data.metrics");
  });
});
