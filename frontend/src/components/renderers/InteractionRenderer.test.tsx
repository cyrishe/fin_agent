import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { createInteractionDraft, selectInteractionAnswer } from "../../interactionDraft";
import InteractionRenderer from "./InteractionRenderer";

const data = {
  interaction_id: "custom_tool.requirement_clarification",
  subject_revision: 2,
  prompt: "请确认两个口径",
  questions: [{
    question: "观察窗口？",
    candidate: ["30 个交易日", "60 个交易日"],
  }],
};

const handlers = {
  onDraftChange: () => undefined,
  onRequestCustomAnswer: () => undefined,
  onClearCustomAnswer: () => undefined,
  onSubmitDraft: () => undefined,
  onAction: () => undefined,
  onRequestFeedback: () => undefined,
  onSubmitFeedback: () => undefined,
};

describe("InteractionRenderer", () => {
  it("renders streamed questions as read-only until the final design arrives", () => {
    const preview = { ...data, provisional: true };
    const html = renderToStaticMarkup(<InteractionRenderer data={preview} content="" {...handlers} />);

    expect(html).toContain("设计仍在生成");
    expect(html).not.toContain(">确定</button>");
    expect(html).toContain("disabled=\"\"");
  });

  it("renders the default answer as visibly selected", () => {
    const html = renderToStaticMarkup(<InteractionRenderer data={data} content="" draft={createInteractionDraft(data)} {...handlers} />);
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("已选择");
    expect(html).toContain(">确定</button>");
  });

  it("renders the custom handling choice and dedicated composer hint", () => {
    const initial = createInteractionDraft(data);
    const draft = selectInteractionAnswer(initial, data.questions[0], undefined, "custom");
    const html = renderToStaticMarkup(<InteractionRenderer data={data} content="" draft={draft} {...handlers} />);
    expect(html).toContain("告诉 Fin Agent 如何处理");
    expect(html).toContain("已在输入框中准备");
    expect(html).toContain("关于「观察窗口？」，我希望：");
  });
});
