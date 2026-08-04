import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import TurnMeta from "./TurnMeta";
import TurnProcess from "./TurnProcess";

describe("turn history presentation", () => {
  it("keeps completed public process summaries in a collapsed review trail", () => {
    const html = renderToStaticMarkup(<TurnProcess run={{
      status: "done",
      summary: "本轮处理完成",
      artifacts: [],
      durationMs: 12_500,
      process: [{
        block_id: "finance_query",
        block_type: "status",
        title: "数据查询",
        data: {
          role: "process",
          status: "completed",
          summary: "已取得最新行情。",
        },
      }],
    }} />);

    expect(html).toContain("<details");
    expect(html).not.toContain("<details open");
    expect(html).toContain("本轮过程");
    expect(html).toContain("1 个节点");
    expect(html).toContain("13 秒");
    expect(html).toContain("已取得最新行情");
  });

  it("does not replay a stale running spinner in completed history", () => {
    const html = renderToStaticMarkup(<TurnProcess run={{
      status: "done",
      summary: "本轮处理完成",
      artifacts: [],
      process: [
        { block_id: "design_custom_tool_understanding", block_type: "status", title: "工具需求与设计", data: { role: "process", status: "running" } },
        { block_id: "runtime_custom_tool_understanding", block_type: "status", title: "工具需求与设计", data: { role: "process", status: "completed", summary: "设计已经形成。" } },
      ],
    }} />);

    expect(html).toContain("1 个节点");
    expect(html).not.toContain('class="spinner"');
  });

  it("shows elapsed time and token use only when authoritative values exist", () => {
    const html = renderToStaticMarkup(<TurnMeta
      createdAt={new Date("2026-07-31T10:20:30").getTime()}
      run={{
        status: "done",
        summary: "本轮处理完成",
        artifacts: [],
        process: [],
        durationMs: 4_600,
        tokenUsage: {
          promptTokens: 1_000,
          completionTokens: 234,
          totalTokens: 1_234,
        },
      }}
    />);

    expect(html).toContain("用时 4.6s");
    expect(html).toContain("1,234 tokens");
  });
});
