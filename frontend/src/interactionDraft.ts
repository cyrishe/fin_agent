import type { InteractionAnswer, InteractionDraft, InteractionResponse, UnknownRecord } from "./types";

const record = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];

export function interactionKey(data: UnknownRecord): string {
  const revision = Number(data.subject_revision ?? data.expected_revision ?? 0);
  return `${String(data.interaction_id || "interaction")}:${revision}`;
}

export function createInteractionDraft(data: UnknownRecord): InteractionDraft {
  const answers: Record<string, InteractionAnswer> = {};
  const submitAction = list(data.actions).map(record).find((action) => String(action.intent || "") === "submit");
  list(data.questions).map(record).forEach((question, index) => {
    const questionId = String(question.id || `Q${index + 1}`);
    const candidates = list(question.candidate).map((item) => String(item)).filter(Boolean);
    const selected = candidates[0];
    if (!selected) return;
    answers[questionId] = {
      question_id: questionId,
      question: String(question.question || "待确认项"),
      value: selected,
      label: selected,
      mode: "option",
    };
  });
  return {
    key: interactionKey(data),
    interactionId: String(data.interaction_id || ""),
    actionId: String(submitAction?.action_id || "custom_tool.submit_clarification"),
    action: String(submitAction?.intent || "submit"),
    expectedRevision: Number(submitAction?.expected_revision ?? data.subject_revision ?? data.expected_revision ?? 0),
    answers,
  };
}

export function selectInteractionAnswer(
  draft: InteractionDraft,
  question: UnknownRecord,
  option?: UnknownRecord,
  mode: "option" | "custom" = "option",
): InteractionDraft {
  const questionText = String(question.question || "待确认项");
  const questionId = String(
    question.id
    || Object.entries(draft.answers).find(([, answer]) => answer.question === questionText)?.[0]
    || "question",
  );
  const label = mode === "custom" ? "告诉 Fin Agent 如何处理" : String(option?.label || option?.value || "");
  return {
    ...draft,
    answers: {
      ...draft.answers,
      [questionId]: {
        question_id: questionId,
        question: questionText,
        value: mode === "custom" ? "" : String(option?.value ?? option?.label ?? ""),
        label,
        mode,
      },
    },
  };
}

export function customAnswerPrompt(question: string): string {
  return `关于「${question.trim()}」，我希望：`;
}

export function upsertComposerPrompt(current: string, prompt: string): string {
  const normalized = prompt.trim();
  const lines = current.split("\n");
  if (lines.some((line) => line.trim().startsWith(normalized))) return current;
  return [...lines.filter((line) => line.trim()), normalized].join("\n");
}

export function removeComposerPrompt(current: string, prompt: string): string {
  const normalized = prompt.trim();
  return current.split("\n").filter((line) => !line.trim().startsWith(normalized)).join("\n").trim();
}

export function readPromptValue(composer: string, prompt: string): string {
  const line = composer.split("\n").find((item) => item.trim().startsWith(prompt));
  return line ? line.trim().slice(prompt.length).trim() : "";
}

export function prepareClarificationSubmission(draft: InteractionDraft, composer: string): {
  answers: InteractionAnswer[];
  missing: string[];
  summary: string;
  response: InteractionResponse;
} {
  const answers = Object.values(draft.answers).map((answer) => {
    if (answer.mode !== "custom") return answer;
    const prompt = customAnswerPrompt(answer.question);
    const value = readPromptValue(composer, prompt);
    return { ...answer, value, label: value || answer.label };
  });
  const missing = answers.filter((answer) => !answer.value.trim()).map((answer) => answer.question);
  return {
    answers,
    missing,
    summary: answers.map((answer) => answer.mode === "custom"
      ? `关于「${answer.question}」，我希望：${answer.value}`
      : `关于「${answer.question}」，我选择：${answer.label}。`
    ).join("\n"),
    response: {
      interaction_id: draft.interactionId,
      action_id: draft.actionId,
      action: draft.action,
      expected_revision: draft.expectedRevision,
      answers: answers.map((answer) => ({
        question: answer.question,
        answer: answer.value,
      })),
    },
  };
}

export function readFeedbackValue(composer: string, prompt: string): string {
  return readPromptValue(composer, prompt);
}
