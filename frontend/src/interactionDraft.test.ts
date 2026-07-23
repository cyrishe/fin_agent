import { describe, expect, it } from "vitest";
import {
  createInteractionDraft,
  customAnswerPrompt,
  prepareClarificationSubmission,
  removeComposerPrompt,
  selectInteractionAnswer,
  upsertComposerPrompt,
} from "./interactionDraft";

const data = {
  interaction_id: "custom_tool.requirement_clarification",
  subject_revision: 4,
  questions: [
    {
      question: "计算周期是什么？",
      candidate: ["30 个交易日", "20 个交易日"],
    },
    {
      question: "金额口径如何处理？",
      candidate: ["按数据接口口径"],
    },
  ],
};

describe("interaction draft", () => {
  it("preselects the first candidate as the default", () => {
    const draft = createInteractionDraft(data);
    expect(draft.answers.Q1).toMatchObject({ value: "30 个交易日", label: "30 个交易日", mode: "option" });
    expect(draft.answers.Q2).toMatchObject({ value: "按数据接口口径", label: "按数据接口口径" });
  });

  it("keeps ordinary option selections in frontend state only", () => {
    const draft = createInteractionDraft(data);
    const question = data.questions[0];
    const changed = selectInteractionAnswer(draft, question, { value: "20 个交易日", label: "20 个交易日" });
    expect(changed.answers.Q1.value).toBe("20 个交易日");
    expect(changed.answers.Q1.label).toBe("20 个交易日");
  });

  it("adds a custom answer prompt on its own line without duplicating it", () => {
    const prompt = customAnswerPrompt("金额口径如何处理？");
    const once = upsertComposerPrompt("已有说明", prompt);
    expect(once).toBe("已有说明\n关于「金额口径如何处理？」，我希望：");
    expect(upsertComposerPrompt(once, prompt)).toBe(once);
    expect(removeComposerPrompt(once, prompt)).toBe("已有说明");
  });

  it("submits all selected defaults as structured answers", () => {
    const result = prepareClarificationSubmission(createInteractionDraft(data), "");
    expect(result.missing).toEqual([]);
    expect(result.response).toMatchObject({
      interaction_id: "custom_tool.requirement_clarification",
      action_id: "custom_tool.submit_clarification",
      action: "submit",
      expected_revision: 4,
    });
    expect(result.response.answers).toHaveLength(2);
  });

  it("requires custom text and then includes it in the structured submission", () => {
    const initial = createInteractionDraft(data);
    const custom = selectInteractionAnswer(initial, data.questions[1], undefined, "custom");
    expect(prepareClarificationSubmission(custom, "关于「金额口径如何处理？」，我希望：").missing).toEqual(["金额口径如何处理？"]);

    const result = prepareClarificationSubmission(custom, "关于「金额口径如何处理？」，我希望：请由系统查询数据接口定义");
    expect(result.missing).toEqual([]);
    expect(result.response.answers?.[1]).toMatchObject({
      question: "金额口径如何处理？",
      answer: "请由系统查询数据接口定义",
    });
  });
});
