import { ArrowUp, ImagePlus, LoaderCircle, Paperclip, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Attachment } from "../types";

const commands = [
  ["/custom_tool create ", "创建一个新的个人金融工具"],
  ["/custom_tool edit ", "优化当前个人工具设计"],
  ["/custom_tool commit ", "提交已通过测试的工具"],
  ["/skills", "查看可用 Skills"],
  ["/tools", "查看工具目录"],
  ["/applications", "查看应用"],
];

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  busy: boolean;
  attachments: Attachment[];
  onFiles: (files: File[]) => void;
  onRemoveAttachment: (index: number) => void;
}

export default function Composer(props: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [focused, setFocused] = useState(false);
  const trimmed = props.value.trimStart();
  const matching = trimmed.startsWith("/") ? commands.filter(([command]) => command.startsWith(trimmed) || trimmed.startsWith(command.trim())).slice(0, 6) : [];

  useEffect(() => {
    const textarea = textRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(180, Math.max(52, textarea.scrollHeight))}px`;
  }, [props.value]);

  return (
    <div className="composer-wrap">
      {matching.length > 0 && focused && <div className="command-menu">{matching.map(([command, description]) => <button type="button" key={command} onMouseDown={(event) => { event.preventDefault(); props.onChange(command); textRef.current?.focus(); }}><code>{command.trim()}</code><span>{description}</span></button>)}</div>}
      {props.attachments.length > 0 && <div className="attachment-preview">{props.attachments.map((attachment, index) => <div key={attachment.attachment_id || index}>{attachment.preview_url ? <img src={attachment.preview_url} alt={attachment.file_name || "附件"} /> : <span className="attachment-file"><Paperclip size={15} />{attachment.file_name || "附件"}</span>}<button type="button" onClick={() => props.onRemoveAttachment(index)} aria-label="移除附件"><X size={13} /></button></div>)}</div>}
      <div className={`composer ${focused ? "focused" : ""}`}>
        <textarea
          ref={textRef}
          value={props.value}
          onChange={(event) => props.onChange(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => window.setTimeout(() => setFocused(false), 100)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              props.onSend();
            }
          }}
          placeholder="描述你的金融问题，或输入 / 调用能力…"
          aria-label="消息输入"
        />
        <div className="composer-toolbar">
          <div>
            <button type="button" className="tool-button" onClick={() => fileRef.current?.click()} title="上传文档或表格"><Paperclip size={17} /><span>附件</span></button>
            <button type="button" className="tool-button" onClick={() => imageRef.current?.click()} title="上传图片"><ImagePlus size={17} /><span>图片</span></button>
          </div>
          <button type="button" className="send-button" disabled={props.busy || (!props.value.trim() && !props.attachments.length)} onClick={props.onSend} aria-label="发送消息">{props.busy ? <LoaderCircle className="spin" size={18} /> : <ArrowUp size={19} />}</button>
        </div>
      </div>
      <div className="composer-hint">Ctrl / ⌘ + Enter 发送 · AI 输出可能存在误差，关键结论请结合数据核验</div>
      <input ref={fileRef} type="file" multiple accept=".pdf,.doc,.docx,.txt,.md,.csv,.xls,.xlsx,application/pdf,text/plain,text/csv" hidden onChange={(event) => { props.onFiles(Array.from(event.target.files || [])); event.target.value = ""; }} />
      <input ref={imageRef} type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={(event) => { props.onFiles(Array.from(event.target.files || [])); event.target.value = ""; }} />
    </div>
  );
}
