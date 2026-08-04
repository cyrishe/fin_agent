import { Bot, FileText, UserRound } from "lucide-react";
import type { ChatMessage, InteractionDraft, InteractionFeedbackRequest, InteractionResponse } from "../types";
import BlockRenderer from "./BlockRenderer";
import MarkdownContent from "./MarkdownContent";
import ReportExportButton from "./ReportExportButton";
import TurnMeta from "./TurnMeta";
import TurnProcess from "./TurnProcess";
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
}

function UserContent({ content }: { content: string }) {
  const match = content.match(/^((?:\/[a-z0-9_-]+(?:\s+[a-z0-9_-]+)?)|(?:\$[a-z0-9_.-]+))([\s\S]*)$/i);
  if (!match) return <>{content}</>;
  return <><span className="command-token">{match[1]}</span>{match[2]}</>;
}

export default function MessageItem({ message, interactionDrafts, selectedInteractions, submittedInteractions, disabled, onDraftChange, onRequestCustomAnswer, onClearCustomAnswer, onSubmitDraft, onInteraction, onRequestFeedback, onSubmitFeedback, onUseAsset }: Props) {
  const run = message.run;
  const firstArtifact = run?.artifacts[0];
  const remainingArtifacts = run?.artifacts.slice(1) || [];
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
            {run.status === "running" && run.artifacts.length === 0 && <div className="answer-pending"><span className="pulse-dot" /><span>{run.summary}</span></div>}
            {run.status === "running" ? <>
              {firstArtifact ? renderBlock(firstArtifact) : null}
              <TurnProcess run={run} />
              {remainingArtifacts.map(renderBlock)}
            </> : <>
              {run.artifacts.map(renderBlock)}
              <TurnProcess run={run} />
            </>}
          </div>}
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
