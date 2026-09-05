import { afterEach, describe, expect, it, vi } from "vitest";
import { runCustomToolInteractiveTest } from "./api";

const jsonResponse = (value: unknown, status = 200) => new Response(
  JSON.stringify(value),
  { status, headers: { "Content-Type": "application/json" } },
);

describe("custom tool interactive test API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("runs the selected immutable revision with structured arguments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      ok: true,
      test: {
        run_id: "ct_test_1",
        tool_name: "ct_demo",
        display_name: "演示工具",
        revision: 3,
        status: "passed",
        elapsed_ms: 120,
        input: { stock_codes: ["600519.SH"] },
        contract: { input_valid: true, runtime_ok: true, business_ok: true, output_valid: true },
        process: [],
        result: { summary: "完成" },
        error: "",
        diagnostics: {},
      },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(runCustomToolInteractiveTest({
      toolName: "ct_demo",
      revision: 3,
      arguments: { stock_codes: ["600519.SH"] },
    })).resolves.toEqual(expect.objectContaining({ status: "passed", revision: 3 }));

    expect(fetchMock).toHaveBeenCalledWith("/api/custom-tools/ct_demo/test", expect.objectContaining({
      method: "POST",
      credentials: "include",
    }));
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      revision: 3,
      arguments: { stock_codes: ["600519.SH"] },
    });
  });
});
