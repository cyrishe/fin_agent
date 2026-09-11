import { Bot, FileText, UserRound } from "lucide-react";
import type { ChatMessage, InteractionDraft, InteractionFeedbackRequest, InteractionResponse } from "../types";
import BlockRenderer from "./BlockRenderer";
import MarkdownContent from "./MarkdownContent";
import ReportExportButton from "./ReportExportButton";
import TurnMeta from "./TurnMeta";
import TurnProcess from "./TurnProcess";
import AnswerEvidence, { splitAnswerEvidence } from "./AnswerEvidence";
import SkillActivity from "./SkillActivity";
import type { ToolIdentitySelection } from "./renderers/ToolIdentityArtifact";

interface Props {
  message: ChatMessage;
  interactionDrafts: Record<string, InteractionDraft>;
  selectedInteractions: Record<string, string>;
  submittedInteractions: Set<string>;
  disabled: boolean;
  onDraftChange: (draft: InteractionDraft) => void;
  onRequestCustomAnswer: (question: string) => void;
  onClearCustomAnswer: (question: string) => void;
  onSubmitDraft: (draft: InteractionDraft) => void;
  onInteraction: (response: InteractionResponse, label: string, key: string) => void;
  onRequestFeedback: (request: InteractionFeedbackRequest) => void;
  onSubmitFeedback: () => void;
  onUseAsset: (asset: ToolIdentitySelection) => void;
  onFollowUp?: (question: string) => void;
}

function UserContent({ content }: { content: string }) {
  const match = content.match(/^((?:\/[a-z0-9_-]+(?:\s+[a-z0-9_-]+)?)|(?:\$[a-z0-9_.-]+))([\s\S]*)$/i);
  if (!match) return <>{content}</>;
  return <><span className="command-token">{match[1]}</span>{match[2]}</>;
}

export default function MessageItem({ message, interactionDrafts, selectedInteractions, submittedInteractions, disabled, onDraftChange, onRequestCustomAnswer, onClearCustomAnswer, onSubmitDraft, onInteraction, onRequestFeedback, onSubmitFeedback, onUseAsset, onFollowUp }: Props) {
  const run = message.run;
  const followUps = Array.isArray(message.payload?.follow_up_questions)
    ? message.payload.follow_up_questions.filter((item): item is string => typeof item === "string" && Boolean(item.trim())) : [];
  const { primary, references } = splitAnswerEvidence(run?.artifacts || []);
  const firstArtifact = primary[0];
  const remainingArtifacts = primary.slice(1);
  const renderBlock = (block: NonNullable<typeof firstArtifact>) => <BlockRenderer
    key={block.block_id}
    block={block}
    interactionScope={`${message.id}:${block.block_id}`}
    interactionDrafts={interactionDrafts}
    selectedInteractions={selectedInteractions}
    submittedInteractions={submittedInteractions}
    disabled={disabled}
    onDraftChange={onDraftChange}
    onRequestCustomAnswer={onRequestCustomAnswer}
    onClearCustomAnswer={onClearCustomAnswer}
    onSubmitDraft={onSubmitDraft}
    onInteraction={onInteraction}
    onRequestFeedback={onRequestFeedback}
    onSubmitFeedback={onSubmitFeedback}
    onUseAsset={onUseAsset}
  />;
  return (
    <article className={`message message-${message.role}`} data-message-id={message.id}>
      <div className="message-avatar">{message.role === "assistant" ? <Bot size={18} /> : <UserRound size={17} />}</div>
      <div className="message-main">
        <div className="message-name"><span>{message.role === "assistant" ? "Fin Agent" : "你"}</span><TurnMeta createdAt={message.createdAt} run={message.role === "assistant" ? run : undefined} /></div>
        <div className="message-content">
          {message.role === "user" ? <div className="user-bubble"><UserContent content={message.content} /></div> : message.content ? <MarkdownContent content={message.content} /> : null}
          {message.attachments?.length ? <div className="message-attachments">{message.attachments.map((attachment) => attachment.preview_url ? <img src={attachment.preview_url} alt={attachment.file_name || "附件"} key={attachment.attachment_id || attachment.preview_url} /> : <div className="message-file" key={attachment.attachment_id || attachment.file_name}><FileText size={18} /><span>{attachment.file_name || "附件"}</span></div>)}</div> : null}
          {run && <div className="agent-run-content">
            <SkillActivity run={run} payload={message.payload} />
            {run.status === "running" && <div className="answer-pending" role="status" aria-live="polite" aria-atomic="true">
              <div className="answer-pending-heading"><span className="spinner" aria-hidden="true" /><strong>正在处理…</strong><span>本轮尚未完成</span></div>
              {run.summary && <p>最新进展：{run.summary}</p>}
            </div>}
            {run.status === "running" ? <>
              {firstArtifact ? renderBlock(firstArtifact) : null}
              <TurnProcess run={run} />
              {remainingArtifacts.map(renderBlock)}
            </> : <>
              {primary.map(renderBlock)}
              <TurnProcess run={run} />
            </>}
            <AnswerEvidence blocks={references} renderBlock={renderBlock} />
          </div>}
          {message.role === "assistant" && run?.status === "done" && followUps.length > 0 && <section className="follow-up-questions" aria-label="进一步提问">
            <strong>进一步提问</strong>
            {followUps.map(question => <button key={question} type="button" disabled={disabled} onClick={() => onFollowUp?.(question)}>{question}<span aria-hidden="true">↗</span></button>)}
          </section>}
          {message.role === "assistant" && run?.status === "done" && <ReportExportButton
            payload={message.payload}
            threadId={message.threadId}
            turnId={message.turnId}
          />}
        </div>
      </div>
    </article>
  );
}
