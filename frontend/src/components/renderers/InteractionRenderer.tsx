import { Check, MessageSquareText } from "lucide-react";
import { useEffect, useMemo } from "react";
import {
  createInteractionDraft,
  customAnswerPrompt,
  interactionKey,
  selectInteractionAnswer,
} from "../../interactionDraft";
import type {
  InteractionDraft,
  InteractionFeedbackRequest,
  InteractionResponse,
  UnknownRecord,
} from "../../types";

const record = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];

interface Props {
  data: UnknownRecord;
  content: string;
  draft?: InteractionDraft;
  selectedActionId?: string;
  disabled?: boolean;
  onDraftChange: (draft: InteractionDraft) => void;
  onRequestCustomAnswer: (question: string) => void;
  onClearCustomAnswer: (question: string) => void;
  onSubmitDraft: (draft: InteractionDraft) => void;
  onAction: (response: InteractionResponse, label: string, key: string) => void;
  onRequestFeedback: (request: InteractionFeedbackRequest) => void;
  onSubmitFeedback: () => void;
}

function feedbackPrompt(interactionId: string): string {
  if (interactionId.includes("coding")) return "对于当前实现，我希望：";
  return "对于当前设计，我希望：";
}

export default function InteractionRenderer(props: Props) {
  const questions = list(props.data.questions).map((item, index) => typeof item === "object" ? record(item) : { id: `Q${index + 1}`, question: String(item) });
  const actions = list(props.data.actions).map(record);
  const baseDraft = useMemo(() => createInteractionDraft(props.data), [props.data]);
  const draft = props.draft || baseDraft;
  const key = interactionKey(props.data);
  const interactionId = String(props.data.interaction_id || "");
  const provisional = props.data.provisional === true;
  const controlsDisabled = Boolean(props.disabled || provisional);

  useEffect(() => {
    if (!provisional && questions.length && !props.draft) props.onDraftChange(baseDraft);
  }, [baseDraft, props, provisional, questions.length]);

  const selectedCount = questions.filter((question, index) => draft.answers[String(question.id || `Q${index + 1}`)]).length;
  const ready = questions.length > 0 && selectedCount === questions.length;

  return (
    <div className={`interaction-block${provisional ? " provisional" : ""}`}>
      <div className="interaction-prompt">{String(props.data.prompt || props.content || "请确认下一步")}</div>
      {questions.map((question, index) => {
        const questionId = String(question.id || `Q${index + 1}`);
        const answer = draft.answers[questionId];
        const options = list(question.candidate).map((candidate) => ({ label: String(candidate), value: String(candidate) }));
        const allowCustom = true;
        return <div className={`question-card ${answer ? "answered" : ""}`} key={questionId}>
          <div className="question-title"><strong>{String(question.question || "需要补充")}</strong>{question.required ? <em>必答</em> : null}{answer ? <span className="question-status"><Check size={12} />已选择</span> : null}</div>
          {Boolean(question.reason) && <p>{String(question.reason)}</p>}
          <div className="option-list">
            {options.map((option, optionIndex) => {
              const label = String(option.label || option.value || `选项 ${optionIndex + 1}`);
              const value = String(option.value ?? option.label ?? "");
              const selected = answer?.mode === "option" && answer.value === value;
              return <button
                aria-pressed={selected}
                className={selected ? "selected" : ""}
                disabled={controlsDisabled}
                key={`${questionId}-${value || optionIndex}`}
                type="button"
                onClick={() => {
                  if (answer?.mode === "custom") props.onClearCustomAnswer(String(question.question || "待确认项"));
                  props.onDraftChange(selectInteractionAnswer(draft, question, option));
                }}
              ><span className="option-check">{selected ? <Check size={13} /> : null}</span><span className="option-copy"><b>{label}</b></span></button>;
            })}
            {allowCustom && <button
              aria-pressed={answer?.mode === "custom"}
              className={`custom-option ${answer?.mode === "custom" ? "selected" : ""}`}
              disabled={controlsDisabled}
              type="button"
              onClick={() => {
                props.onDraftChange(selectInteractionAnswer(draft, question, undefined, "custom"));
                props.onRequestCustomAnswer(String(question.question || "待确认项"));
              }}
            ><span className="option-check">{answer?.mode === "custom" ? <Check size={13} /> : <MessageSquareText size={13} />}</span><span className="option-copy"><b>告诉 Fin Agent 如何处理</b><p>在下方输入框中填写你的明确意见</p></span></button>}
          </div>
          {answer?.mode === "custom" && <div className="custom-answer-hint">已在输入框中准备：<code>{customAnswerPrompt(answer.question)}</code></div>}
        </div>;
      })}

      {questions.length > 0 && <div className="interaction-submit-row">{provisional
        ? <span>设计仍在生成，完成后可统一选择并提交</span>
        : <><span>{selectedCount}/{questions.length} 项已选择，提交前仍可修改</span><button className="primary" type="button" disabled={!ready || props.disabled} onClick={() => props.onSubmitDraft(draft)}>{props.disabled ? "已提交" : "确定"}</button></>}
      </div>}

      {!questions.length && actions.length > 0 && <div className="interaction-actions">{actions.map((action, index) => {
        const actionId = String(action.action_id || index);
        const label = String(action.label || "继续");
        const intent = String(action.intent || "");
        const selected = props.selectedActionId === actionId;
        const response: InteractionResponse = {
          interaction_id: interactionId,
          action_id: actionId,
          action: intent === "accept" ? "accept" : intent,
          expected_revision: Number(action.expected_revision ?? props.data.subject_revision ?? 0),
          label,
          subject_ref: String(props.data.subject_ref || ""),
        };
        const isEdit = intent === "edit";
        return <button
          aria-pressed={selected}
          className={`${action.style === "primary" ? "primary" : ""} ${selected ? "selected" : ""}`}
          disabled={props.disabled}
          key={actionId}
          type="button"
          onClick={() => isEdit
            ? props.onRequestFeedback({ key, prompt: feedbackPrompt(interactionId), label, response })
            : props.onAction(response, label, key)}
        >{selected ? <Check size={14} /> : null}{isEdit ? "告诉 Fin Agent 如何处理" : label}{selected ? <span>已选择</span> : null}</button>;
      })}</div>}
      {!questions.length && actions.some((action) => String(action.intent || "") === "edit" && props.selectedActionId === String(action.action_id || "")) && <div className="interaction-submit-row feedback-submit"><span>请在下方输入框填写意见后提交</span><button className="primary" type="button" disabled={props.disabled} onClick={props.onSubmitFeedback}>确定</button></div>}
    </div>
  );
}
