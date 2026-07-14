import { Bot, FileText, UserRound } from "lucide-react";
import type { ChatMessage, InteractionResponse } from "../types";
import BlockRenderer from "./BlockRenderer";
import MarkdownContent from "./MarkdownContent";

interface Props {
  message: ChatMessage;
  onInsertText: (text: string) => void;
  onInteraction: (response: InteractionResponse, label: string) => void;
  resolvedInteractions: Set<string>;
}

function UserContent({ content }: { content: string }) {
  const match = content.match(/^(\/[a-z0-9_-]+(?:\s+[a-z0-9_-]+)?)([\s\S]*)$/i);
  if (!match) return <>{content}</>;
  return <><span className="command-token">{match[1]}</span>{match[2]}</>;
}

export default function MessageItem({ message, onInsertText, onInteraction, resolvedInteractions }: Props) {
  const run = message.run;
  return (
    <article className={`message message-${message.role}`}>
      <div className="message-avatar">{message.role === "assistant" ? <Bot size={18} /> : <UserRound size={17} />}</div>
      <div className="message-main">
        <div className="message-name">{message.role === "assistant" ? "Fin Agent" : "你"}</div>
        <div className="message-content">
          {message.role === "user" ? <div className="user-bubble"><UserContent content={message.content} /></div> : message.content ? <MarkdownContent content={message.content} /> : null}
          {message.attachments?.length ? <div className="message-attachments">{message.attachments.map((attachment) => attachment.preview_url ? <img src={attachment.preview_url} alt={attachment.file_name || "附件"} key={attachment.attachment_id || attachment.preview_url} /> : <div className="message-file" key={attachment.attachment_id || attachment.file_name}><FileText size={18} /><span>{attachment.file_name || "附件"}</span></div>)}</div> : null}
          {run && <div className="agent-run-content">
            {run.status === "running" && run.artifacts.length === 0 && <div className="answer-pending"><span className="pulse-dot" /><span>{run.summary}</span></div>}
            {run.artifacts.map((block) => <BlockRenderer
              key={block.block_id}
              block={block}
              onInsertText={onInsertText}
              onInteraction={onInteraction}
              resolved={resolvedInteractions.has(String(block.data?.interaction_id || ""))}
            />)}
          </div>}
        </div>
      </div>
    </article>
  );
}
