import type { ComponentProps } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { applyStreamEvent, initialRun } from "../surface";
import type { AgentRun } from "../types";
import MessageItem from "./MessageItem";

const noop = () => undefined;
const props: Omit<ComponentProps<typeof MessageItem>, "message"> = {
  interactionDrafts: {}, selectedInteractions: {}, submittedInteractions: new Set(), disabled: false,
  onDraftChange: noop, onRequestCustomAnswer: noop, onClearCustomAnswer: noop,
  onSubmitDraft: noop, onInteraction: noop, onRequestFeedback: noop,
  onSubmitFeedback: noop, onUseAsset: noop,
};
function render(run: AgentRun) {
  return renderToStaticMarkup(<MessageItem {...props} message={{ id: "test", role: "assistant", content: "", createdAt: 1, run }} />);
}
function queryCompleted() {
  return applyStreamEvent(initialRun(), {
    event: "block", block_id: "query", block_type: "status", title: "数据查询",
    content: "已查询，取得 100 条记录。", data: { role: "process", status: "completed" },
  });
}

describe("whole-request activity visibility", () => {
  it("shows an explicit activity indicator before the first event", () => {
    const html = render(initialRun());
    expect(html).toContain("正在处理…");
    expect(html).toContain("正在连接 Agent");
    expect(html).toContain('role="status" aria-live="polite"');
  });

  it("keeps the request active while all recorded steps are complete", () => {
    const run = queryCompleted();
    const html = render(run);
    expect(run.status).toBe("running");
    expect(html).toContain("正在处理…");
    expect(html).toContain("本轮尚未完成");
    expect(html).toContain("最新进展：已查询，取得 100 条记录。");
    expect(html).toContain("process-item completed");
    expect(html).toContain("进行中");
    expect(html).toContain("1 个节点");
    expect(run.process).toHaveLength(1); // No synthetic thinking node or status is added.
  });

  it("does not hide activity when partial results or answer text have arrived", () => {
    const run = applyStreamEvent(queryCompleted(), {
      event: "block", block_id: "answer", block_type: "narrative",
      semantic: "finance.answer", content: "已获取数据，正在整理可比口径。",
    });
    const html = render(run);
    expect(html).toContain("正在处理…");
    expect(html).toContain("已获取数据，正在整理可比口径。");
  });

  it.each(["done", "run.finished", "error", "stream.error"])("clears activity on %s", (event) => {
    const run = applyStreamEvent(queryCompleted(), { event, message: "连接已中断" });
    const html = render(run);
    expect(html).not.toContain("answer-pending");
    expect(html).not.toContain("turn-process-active");
    expect(html).not.toContain('class="spinner"');
    expect(html).toContain("已查询，取得 100 条记录。");
    if (event.includes("error")) expect(html).toContain("连接已中断");
  });
});

it("renders persisted follow-ups only after the answer completes", () => {
  const message = { id: "follow", role: "assistant" as const, content: "", payload: { follow_up_questions: ["下一步核验什么？"] }, run: { ...initialRun(), status: "done" as const } };
  const html = renderToStaticMarkup(<MessageItem {...props} message={message} onFollowUp={noop} />);
  expect(html).toContain('aria-label="进一步提问"');
  expect(html).toContain("下一步核验什么？");
  expect(renderToStaticMarkup(<MessageItem {...props} message={{ ...message, run: initialRun() }} />)).not.toContain("下一步核验什么？");
});
