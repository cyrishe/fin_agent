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
      id: "Q1",
      question: "计算周期是什么？",
      options: [
        { value: "20d", label: "20 个交易日" },
        { value: "30d", label: "30 个交易日", recommended: true },
      ],
    },
    {
      id: "Q2",
      question: "金额口径如何处理？",
      options: [{ value: "api", label: "按数据接口口径" }],
    },
  ],
};

describe("interaction draft", () => {
  it("preselects recommended options and falls back to the first option", () => {
    const draft = createInteractionDraft(data);
    expect(draft.answers.Q1).toMatchObject({ value: "30d", label: "30 个交易日", mode: "option" });
    expect(draft.answers.Q2).toMatchObject({ value: "api", label: "按数据接口口径" });
  });

  it("keeps ordinary option selections in frontend state only", () => {
    const draft = createInteractionDraft(data);
    const question = data.questions[0];
    const changed = selectInteractionAnswer(draft, question, question.options[0]);
    expect(changed.answers.Q1.value).toBe("20d");
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
      question_id: "Q2",
      value: "请由系统查询数据接口定义",
      mode: "custom",
    });
  });
});
