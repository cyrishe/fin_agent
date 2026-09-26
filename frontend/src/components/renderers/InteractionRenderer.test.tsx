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

  it("renders business defaults as a readable list without exposing protocol names", () => {
    const html = renderToStaticMarkup(<InteractionRenderer
      data={{ ...data, notice: ["按最新完整交易日处理", "默认覆盖全部 A 股"] }}
      content=""
      draft={createInteractionDraft(data)}
      {...handlers}
    />);

    expect(html).toContain("我会按这些理解继续");
    expect(html).toContain("<li>按最新完整交易日处理</li>");
    expect(html).not.toContain(">notice<");
  });

  it("renders one confirmation action when no questions need answers", () => {
    const confirmation = {
      interaction_id: "custom_tool.requirement_clarification",
      prompt: "如果上述理解符合你的预期，请确认。",
      notice: [],
      questions: [],
      actions: [{
        action_id: "custom_tool.submit_clarification",
        label: "确认需求",
        intent: "submit",
        style: "primary",
      }],
    };
    const html = renderToStaticMarkup(<InteractionRenderer data={confirmation} content="" {...handlers} />);

    expect(html).toContain(">确认需求</button>");
    expect(html).toContain('type="button"');
    expect(html).not.toContain('disabled=""');
  });

  it("keeps a blocked activation visible, explained and non-interactive", () => {
    const html = renderToStaticMarkup(<InteractionRenderer data={{
      interaction_id: "custom_tool.coding_review",
      subject_ref: "ct_demo",
      subject_revision: 2,
      prompt: "候选版本验证未通过。",
      actions: [{
        action_id: "custom_tool.activate_draft",
        label: "启用候选版本",
        intent: "accept",
        style: "primary",
        expected_revision: 2,
        disabled: true,
        disabled_reason: "候选版本验证未通过，修正后才能启用。",
      }],
    }} content="" {...handlers} />);

    expect(html).toContain("启用候选版本");
    expect(html).toContain('type="button"');
    expect(html).toContain('disabled=""');
    expect(html).toContain('aria-disabled="true"');
    expect(html).toContain("候选版本验证未通过，修正后才能启用。");
  });
});
