import { describe, expect, it } from "vitest";
import { applyStreamEvent, blocksFromPayload, initialRun, isProcessBlock, mergeBlock, reconcileBlockOrder, settleProcessBlocks } from "./surface";
import type { UnknownRecord } from "./types";

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

  it("reconciles streamed arrival order with the authoritative final Surface order", () => {
    const streamed = [
      { block_id: "progress", block_type: "workflow", data: { role: "conversation_progress" } },
      { block_id: "activation", block_type: "interaction" },
      { block_id: "edit_summary", block_type: "artifact" },
    ];
    const finalSurface = [
      { block_id: "edit_summary", block_type: "artifact" },
      { block_id: "progress", block_type: "workflow", data: { role: "conversation_progress" } },
      { block_id: "activation", block_type: "interaction" },
    ];

    expect(reconcileBlockOrder(streamed, finalSurface).map((block) => block.block_id)).toEqual([
      "edit_summary",
      "progress",
      "activation",
    ]);
  });

  it("lets the final Surface replace stale stream state and removes provisional extras", () => {
    const streamed = [
      { block_id: "understanding", block_type: "status", data: { status: "running", summary: "处理中" } },
      { block_id: "provisional", block_type: "status", data: { status: "running" } },
    ];
    const finalSurface = [
      { block_id: "understanding", block_type: "status", data: { status: "completed", summary: "已完成" } },
    ];

    expect(reconcileBlockOrder(streamed, finalSurface)).toEqual([
      expect.objectContaining({
        block_id: "understanding",
        data: expect.objectContaining({ status: "completed", summary: "已完成" }),
      }),
    ]);
  });

  it("settles and compacts the same logical process after its stage changes", () => {
    const process = settleProcessBlocks([
      {
        block_id: "design_custom_tool_understanding",
        block_type: "status",
        title: "工具需求与设计",
        data: { role: "process", status: "running" },
      },
      {
        block_id: "runtime_custom_tool_understanding",
        block_type: "status",
        title: "工具需求与设计",
        data: { role: "process", status: "completed" },
      },
    ], "done");

    expect(process).toHaveLength(1);
    expect(process[0]).toEqual(expect.objectContaining({
      block_id: "runtime_custom_tool_understanding",
      data: expect.objectContaining({ status: "completed" }),
    }));
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

  it("does not replace the completed run summary when persisted progress is merged", () => {
    const done = applyStreamEvent(initialRun(), { event: "done" });
    const merged = applyStreamEvent(done, {
      event: "block",
      block_id: "runtime_finance_synthesis",
      block_type: "status",
      content: "证据与回答已整理完成。",
      data: { role: "process", status: "completed" },
    });

    expect(merged.status).toBe("done");
    expect(merged.summary).toBe("本轮处理完成");
    expect(merged.process).toHaveLength(1);
  });

  it("does not leave a running child behind after the run finishes", () => {
    const running = applyStreamEvent(initialRun(), {
      event: "block",
      block_id: "design_custom_tool_understanding",
      block_type: "status",
      data: { role: "process", status: "running" },
    });
    const done = applyStreamEvent(running, { event: "done" });

    expect(done.status).toBe("done");
    expect(done.process[0].data?.status).toBe("completed");
  });

  it("keeps generic live agent activity out of the formal conversation", () => {
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

  it("shows user-facing coding progress in the conversation", () => {
    const run = applyStreamEvent(initialRun(), {
      event: "block",
      block_id: "coding_module_progress",
      block_type: "workflow",
      title: "实现进展",
      data: {
        role: "conversation_progress",
        status: "running",
        summary: "正在实现 MACD 金叉判断。",
        items: [{ id: "update_1", summary: "正在实现 MACD 金叉判断。", status: "running" }],
      },
    });

    expect(run.process).toHaveLength(0);
    expect(run.artifacts).toHaveLength(1);
    expect(run.artifacts[0].block_id).toBe("coding_module_progress");
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

  it("keeps same-name Skill Hub rows distinct by their catalog identity", () => {
    const blocks = blocksFromPayload({
      mode: "skills_catalog",
      items: [
        { catalog_id: "skill:business_method:research", skill_name: "research", skill_type: "business_method" },
        { catalog_id: "skill:legacy_compiled:research", skill_name: "research", skill_type: "legacy_compiled" },
      ],
      workspace: { title: "Skill Hub", url: "/skills" },
    });
    const resources = blocks[0].data?.resources as UnknownRecord[];

    expect(resources.map((item) => item.resource_id)).toEqual([
      "skill:business_method:research",
      "skill:legacy_compiled:research",
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

  it("keeps a plain assistant answer visible when task state only adds process blocks", () => {
    const blocks = blocksFromPayload({
      mode: "financial_qa_cc",
      message: "贵州茅台今日上涨 2.35%。",
      items: [],
      task_state: {
        job: { result_summary: "已完成行情查询" },
        steps: [{ id: "query", title: "查询行情", status: "completed" }],
      },
    });

    expect(isProcessBlock(blocks[0])).toBe(true);
    expect(blocks[1]).toEqual(expect.objectContaining({
      block_id: "legacy_result_summary",
      block_type: "narrative",
      content: "贵州茅台今日上涨 2.35%。",
    }));
  });

  it("adapts an edit summary into one result artifact and the existing draft activation action", () => {
    const blocks = blocksFromPayload({
      edit_summary: {
        tool_name: "ct_market_buy_decision",
        display_name: "大盘状态与买入决策",
        route: "local_patch",
        impact_summary: "调整成交额过滤口径。",
        base_revision: 3,
        candidate_revision: 4,
        affected_assets: ["成交额过滤模块"],
        changes: [{ field: "成交额阈值", before: "5000 万", after: "1 亿" }],
        verification: { status: "passed", summary: "样例通过", cases: [] },
      },
    });

    expect(blocks.map((block) => block.block_id)).toEqual([
      "custom_tool_edit_summary",
      "custom_tool_edit_activation",
    ]);
    expect(blocks[0].data).toEqual(expect.objectContaining({
      artifact_type: "finance.custom_tool_edit",
      tool_name: "ct_market_buy_decision",
      candidate_revision: 4,
    }));
    expect(blocks[1].data).toEqual(expect.objectContaining({
      interaction_id: "custom_tool.coding_review",
      subject_ref: "ct_market_buy_decision",
      subject_revision: 4,
      actions: [expect.objectContaining({
        action_id: "custom_tool.activate_draft",
        expected_revision: 4,
        disabled: false,
      })],
    }));
  });

  it("keeps activation visible but disabled while verification is running or failed", () => {
    for (const status of ["running", "failed"]) {
      const blocks = blocksFromPayload({
        task_result: {
          edit_summary: {
            tool_name: "ct_demo",
            base_revision: 1,
            candidate_revision: 2,
            verification: { status, cases: [] },
          },
        },
      });
      const action = (blocks[1].data?.actions as UnknownRecord[])[0];

      expect(action.action_id).toBe("custom_tool.activate_draft");
      expect(action.disabled).toBe(true);
      expect(String(action.disabled_reason)).toContain(status === "running" ? "仍在验证" : "未通过");
    }
  });

  it("does not duplicate edit blocks already supplied by the backend surface", () => {
    const blocks = blocksFromPayload({
      edit_summary: {
        tool_name: "ct_demo",
        base_revision: 1,
        candidate_revision: 2,
        verification: { status: "passed" },
      },
      surface_blocks: [
        {
          block_id: "backend_edit",
          block_type: "artifact",
          data: { artifact_type: "finance.custom_tool_edit", tool_name: "ct_demo" },
        },
        {
          block_id: "backend_activation",
          block_type: "interaction",
          data: { actions: [{ action_id: "custom_tool.activate_draft" }] },
        },
      ],
    });

    expect(blocks.map((block) => block.block_id)).toEqual(["backend_edit", "backend_activation"]);
  });

  it("puts the edit summary before the backend activation and keeps that action authoritative", () => {
    const blocks = blocksFromPayload({
      edit_summary: {
        tool_name: "ct_demo",
        display_name: "演示工具",
        route: "local_patch",
        base_revision: 3,
        candidate_revision: 4,
        verification: { status: "passed", cases: [] },
      },
      surface_blocks: [
        { block_id: "custom_tool_draft_summary", block_type: "artifact", data: { artifact_type: "finance.custom_tool_implementation" } },
        { block_id: "custom_tool_test_result", block_type: "assessment", data: { overall: "pass" } },
        {
          block_id: "custom_tool_coding_review",
          block_type: "interaction",
          data: {
            subject_ref: "ct_demo",
            subject_revision: 4,
            actions: [{ action_id: "custom_tool.activate_draft", expected_revision: 4 }],
          },
        },
      ],
    });

    expect(blocks.map((block) => block.block_id)).toEqual([
      "custom_tool_edit_summary",
      "custom_tool_draft_summary",
      "custom_tool_test_result",
      "custom_tool_coding_review",
    ]);
    const activationActions = blocks.flatMap((block) => Array.isArray(block.data?.actions) ? block.data.actions : []) as UnknownRecord[];
    expect(activationActions.filter((action) => action.action_id === "custom_tool.activate_draft")).toHaveLength(1);
  });

  it("does not invent an activation when a failed backend Surface deliberately omits it", () => {
    const blocks = blocksFromPayload({
      edit_summary: {
        tool_name: "ct_demo",
        base_revision: 3,
        candidate_revision: 4,
        verification: { status: "failed", summary: "构造样本验证失败", cases: [] },
      },
      surface_blocks: [
        { block_id: "custom_tool_draft_summary", block_type: "artifact", data: { artifact_type: "finance.custom_tool_implementation" } },
        { block_id: "custom_tool_test_result", block_type: "assessment", data: { overall: "fail" } },
      ],
    });

    expect(blocks.map((block) => block.block_id)).toEqual([
      "custom_tool_edit_summary",
      "custom_tool_draft_summary",
      "custom_tool_test_result",
    ]);
    expect(blocks.some((block) => block.block_type === "interaction")).toBe(false);
  });
});
