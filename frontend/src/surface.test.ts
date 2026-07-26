import { describe, expect, it } from "vitest";
import { applyStreamEvent, blocksFromPayload, initialRun, isProcessBlock, mergeBlock } from "./surface";

describe("surface stream reducer", () => {
  it("appends only to the addressed block", () => {
    const blocks = [
      { block_id: "stable", block_type: "narrative", content: "已提交" },
      { block_id: "live", block_type: "narrative", content: "第一段" },
    ];
    const next = mergeBlock(blocks, {
      block_id: "live",
      block_type: "narrative",
      mode: "append",
      content: "第二段",
    });

    expect(next[0]).toBe(blocks[0]);
    expect(next[1].content).toBe("第一段第二段");
    expect(next).toHaveLength(2);
  });

  it("keeps process blocks out of formal artifacts", () => {
    const run = applyStreamEvent(initialRun(), {
      event: "block",
      block_id: "design_thinking",
      block_type: "status",
      content: "正在确认数据口径",
    });

    expect(run.process).toHaveLength(1);
    expect(run.artifacts).toHaveLength(0);
    expect(run.summary).toBe("正在确认数据口径");
  });

  it("keeps live agent activity out of the formal conversation", () => {
    const run = applyStreamEvent(initialRun(), {
      event: "block",
      block_id: "coding_live_progress",
      block_type: "workflow",
      title: "代码实现",
      data: { role: "live_progress", summary: "正在实现并验证代码…" },
    });

    expect(run.process).toHaveLength(1);
    expect(run.artifacts).toHaveLength(0);
  });

  it("preserves completed artifacts when a later block arrives", () => {
    const first = applyStreamEvent(initialRun(), {
      event: "block",
      block_id: "design",
      block_type: "artifact",
      content: "完整设计",
    });
    const second = applyStreamEvent(first, {
      event: "block",
      block_id: "confirm",
      block_type: "interaction",
      content: "是否确认",
    });

    expect(second.artifacts.map((block) => block.block_id)).toEqual(["design", "confirm"]);
  });

  it("turns a stream error into a visible assessment without dropping prior output", () => {
    const withDesign = applyStreamEvent(initialRun(), {
      event: "block",
      block_id: "design",
      block_type: "artifact",
      content: "已生成设计",
    });
    const failed = applyStreamEvent(withDesign, {
      event: "error",
      message: "代码执行失败",
    });

    expect(failed.status).toBe("error");
    expect(failed.artifacts.map((block) => block.block_id)).toEqual(["design", "stream_error"]);
    expect(failed.artifacts.at(-1)?.block_type).toBe("assessment");
  });

  it("ignores unknown transport events without corrupting state", () => {
    const run = initialRun("等待结果");
    expect(applyStreamEvent(run, { event: "future.event", payload: { any: "value" } })).toBe(run);
  });

  it("collects legacy render blocks and Agent Surface sections through one adapter", () => {
    const legacy = blocksFromPayload({
      render_payload: { sections: [{ blocks: [{ type: "kline", title: "日线", data: { candles: [] } }] }] },
    });
    const v1 = blocksFromPayload({
      surface: { sections: [{ blocks: [{ block_id: "n1", kind: "narrative", payload: { format: "markdown", text: "结论" } }] }] },
    });

    expect(legacy[0].block_type).toBe("kline");
    expect(v1[0].block_type).toBe("narrative");
  });

  it("renders tool results stored under task_result for persisted conversations", () => {
    const blocks = blocksFromPayload({
      task_result: {
        render_payload: {
          sections: [{ blocks: [{ type: "table", title: "评价结果", data: { columns: ["status"], rows: [{ status: "normal" }] } }] }],
        },
      },
    });

    expect(blocks).toHaveLength(1);
    expect(blocks[0].block_type).toBe("table");
    expect(blocks[0].data?.rows).toEqual([{ status: "normal" }]);
  });

  it("keeps the invocation preview together with the executed tool result", () => {
    const blocks = blocksFromPayload({
      surface_blocks: [{ block_id: "asset_invocation_preview", block_type: "structured_text", content: "标的：贵州茅台（600519.SH）" }],
      task_result: {
        render_payload: {
          sections: [{ blocks: [{ block_id: "result", type: "structured_text", data: { summary: "流动性稳定" } }] }],
        },
      },
    });

    expect(blocks.map((block) => block.block_id)).toEqual(["asset_invocation_preview", "result"]);
  });

  it("uses the design artifact as the single display source for persisted designs", () => {
    const blocks = blocksFromPayload({
      surface_blocks: [
        { block_id: "design_final_summary", block_type: "narrative", content: "重复的完整设计" },
        { block_id: "design_artifact", block_type: "artifact", data: { artifact_type: "finance.tool_spec" } },
        { block_id: "design_design_review", block_type: "interaction" },
      ],
    });

    expect(blocks.map((block) => block.block_id)).toEqual([
      "design_artifact",
      "design_design_review",
    ]);
  });

  it("keeps legacy catalog results visible after moving to the React surface", () => {
    const blocks = blocksFromPayload({
      mode: "tools_catalog",
      message: "当前共有 1 个 tools。",
      items: [{ tool_name: "finance_data_query", display_name: "金融数据查询", description: "查询金融数据", workspace_url: "/tools" }],
      workspace: { title: "Tools Catalog", url: "/tools" },
    });

    expect(blocks.map((block) => block.block_type)).toEqual(["narrative", "resource"]);
    expect(blocks[1].data?.resources).toEqual([
      expect.objectContaining({ resource_id: "finance_data_query", title: "金融数据查询" }),
    ]);
  });

  it("moves task-state steps to the process panel instead of the formal answer", () => {
    const blocks = blocksFromPayload({
      surface_blocks: [{ block_id: "answer", block_type: "narrative", content: "查询完成" }],
      task_state: {
        job: { result_summary: "已完成行情查询" },
        steps: [{ id: "query", title: "查询行情", status: "completed" }],
      },
    });

    expect(isProcessBlock(blocks[0])).toBe(true);
    expect(blocks[1].block_id).toBe("answer");
  });
});
