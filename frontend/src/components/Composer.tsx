import { ArrowUp, Blocks, ImagePlus, LoaderCircle, Paperclip, Sparkles, Terminal, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { completeSuggestion, filterSuggestions, findInvocation, type ComposerSuggestion } from "../composerSuggestions";
import type { Attachment, InvocationAsset } from "../types";

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  busy: boolean;
  focusRequest?: number;
  assets: InvocationAsset[];
  selectedAsset: InvocationAsset | null;
  onSelectAsset: (asset: InvocationAsset) => void;
  onClearSelectedAsset: () => void;
  attachments: Attachment[];
  onFiles: (files: File[]) => void;
  onRemoveAttachment: (index: number) => void;
}

export default function Composer(props: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const selectedRef = useRef<HTMLButtonElement>(null);
  const [focused, setFocused] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [dismissedValue, setDismissedValue] = useState<string | null>(null);
  const invocation = useMemo(() => findInvocation(props.value, props.value.length), [props.value]);
  const assetSuggestions = useMemo<ComposerSuggestion[]>(() => props.assets.map((asset) => ({
    id: `${asset.kind}:${asset.name}`,
    kind: asset.kind,
    value: `$${asset.name}`,
    label: `$${asset.name}`,
    description: asset.displayName === asset.name ? asset.description : `${asset.displayName}${asset.description ? ` · ${asset.description}` : ""}`,
  })), [props.assets]);
  const matching = useMemo(() => filterSuggestions(invocation, assetSuggestions), [invocation, assetSuggestions]);
  const invocationFields = useMemo(() => {
    const schema = props.selectedAsset?.inputSchema || {};
    const properties = schema.properties && typeof schema.properties === "object" && !Array.isArray(schema.properties)
      ? schema.properties as Record<string, unknown>
      : {};
    const required = new Set(Array.isArray(schema.required) ? schema.required.map(String) : []);
    return Object.entries(properties).map(([name, raw]) => {
      const definition = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
      return {
        name,
        label: ["question", "request"].includes(name) ? "自然语言要求" : name,
        required: required.has(name),
        description: String(definition.description || definition.title || ""),
        defaultValue: definition.default,
      };
    }).sort((left, right) => Number(right.required) - Number(left.required));
  }, [props.selectedAsset]);
  const menuOpen = Boolean(invocation) && props.value !== dismissedValue && (matching.length > 0 || invocation?.query === "/" || invocation?.query === "$");

  useEffect(() => { setSelectedIndex(0); }, [invocation?.query]);
  useEffect(() => { selectedRef.current?.scrollIntoView({ block: "nearest" }); }, [selectedIndex]);
  useEffect(() => {
    if (!props.focusRequest) return;
    window.requestAnimationFrame(() => {
      const textarea = textRef.current;
      textarea?.focus();
      textarea?.setSelectionRange(textarea.value.length, textarea.value.length);
    });
  }, [props.focusRequest]);

  useEffect(() => {
    const textarea = textRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(180, Math.max(52, textarea.scrollHeight))}px`;
  }, [props.value]);

  const choose = (suggestion: ComposerSuggestion) => {
    if (!invocation) return;
    const completed = completeSuggestion(props.value, invocation, suggestion);
    props.onChange(completed.value);
    if (suggestion.kind === "tool" || suggestion.kind === "skill") {
      const asset = props.assets.find((item) => item.kind === suggestion.kind && `$${item.name}` === suggestion.value);
      if (asset) props.onSelectAsset(asset);
    }
    setDismissedValue(completed.value);
    window.requestAnimationFrame(() => {
      textRef.current?.focus();
      textRef.current?.setSelectionRange(completed.cursor, completed.cursor);
    });
  };
  const assetScope = invocation?.trigger === "$" && invocation.query.slice(1).toLowerCase().startsWith("tool:") ? "工具"
    : invocation?.trigger === "$" && invocation.query.slice(1).toLowerCase().startsWith("skill:") ? "Skills"
      : "工具与 Skills";
  const menuId = "composer-suggestion-menu";

  return (
    <div className="composer-wrap">
      {menuOpen && <div id={menuId} className="command-menu" role="listbox" aria-label={invocation?.trigger === "/" ? "系统命令" : "工具和 Skill"}>
        <div className="command-menu-head"><span>{invocation?.trigger === "/" ? <Terminal size={14} /> : <Sparkles size={14} />}{invocation?.trigger === "/" ? "系统命令" : assetScope}<em>{matching.length}</em></span><small>↑↓ 选择 · Tab 补全 · Esc 关闭</small></div>
        {matching.length > 0 ? matching.map((suggestion, index) => <button
          id={`composer-suggestion-${index}`}
          ref={index === selectedIndex ? selectedRef : undefined}
          type="button"
          role="option"
          aria-selected={index === selectedIndex}
          className={index === selectedIndex ? "selected" : ""}
          key={suggestion.id}
          onMouseEnter={() => setSelectedIndex(index)}
          onMouseDown={(event) => { event.preventDefault(); choose(suggestion); }}
        ><span className={`suggestion-icon ${suggestion.kind}`}>{suggestion.kind === "command" ? <Terminal size={14} /> : suggestion.kind === "tool" ? <Blocks size={14} /> : <Sparkles size={14} />}</span><div><code>{suggestion.label}</code><span>{suggestion.description || (suggestion.kind === "tool" ? "系统工具" : "系统 Skill")}</span></div><small>{suggestion.kind === "command" ? "命令" : suggestion.kind === "tool" ? "工具" : "Skill"}</small></button>) : <div className="command-menu-empty">没有匹配的{invocation?.trigger === "/" ? "系统命令" : "工具或 Skill"}</div>}
      </div>}
      {props.attachments.length > 0 && <div className="attachment-preview">{props.attachments.map((attachment, index) => <div key={attachment.attachment_id || index}>{attachment.preview_url ? <img src={attachment.preview_url} alt={attachment.file_name || "附件"} /> : <span className="attachment-file"><Paperclip size={15} />{attachment.file_name || "附件"}</span>}<button type="button" onClick={() => props.onRemoveAttachment(index)} aria-label="移除附件"><X size={13} /></button></div>)}</div>}
      <div className={`composer ${focused ? "focused" : ""}`}>
        {props.selectedAsset && <div className="invocation-guide">
          <code>${props.selectedAsset.name}</code>
          <div className="invocation-parameters">
            {invocationFields.map((field) => <span key={field.name} className={field.required ? "required" : "optional"} title={field.description}>
              {`<${field.label}${field.defaultValue !== undefined ? `=${String(field.defaultValue)}` : ""}>`}
            </span>)}
            {!invocationFields.length && <span className="ready">无需参数，可直接运行</span>}
            {invocationFields.length > 0 && !invocationFields.some((field) => field.required) && <span className="ready">可直接运行</span>}
          </div>
          <button type="button" onClick={() => {
            const prefix = `$${props.selectedAsset?.name || ""}`;
            if (props.value.trimStart().startsWith(prefix)) props.onChange(props.value.trimStart().slice(prefix.length).trimStart());
            props.onClearSelectedAsset();
          }} aria-label="取消工具调用"><X size={13} /></button>
        </div>}
        <textarea
          ref={textRef}
          value={props.value}
          onChange={(event) => props.onChange(event.target.value)}
          onFocus={() => { setFocused(true); setDismissedValue(null); }}
          onBlur={() => window.setTimeout(() => { setFocused(false); setDismissedValue(props.value); }, 100)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              props.onSend();
              return;
            }
            if (menuOpen && matching.length > 0 && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
              event.preventDefault();
              setSelectedIndex((current) => event.key === "Home" ? 0 : event.key === "End" ? matching.length - 1 : event.key === "ArrowDown" ? (current + 1) % matching.length : (current - 1 + matching.length) % matching.length);
              return;
            }
            if (menuOpen && matching.length > 0 && ((event.key === "Tab" && !event.shiftKey) || event.key === "Enter")) {
              event.preventDefault();
              choose(matching[Math.min(selectedIndex, matching.length - 1)]);
              return;
            }
            if (menuOpen && event.key === "Escape") {
              event.preventDefault();
              setDismissedValue(props.value);
            }
          }}
          placeholder={props.selectedAsset ? "直接用自然语言填写参数，也可以附加表格或文档批量执行…" : "描述金融问题，输入 / 调用命令，或用 $ 选择工具与 Skill…"}
          aria-label="消息输入"
          aria-controls={menuOpen ? menuId : undefined}
          aria-expanded={menuOpen}
          aria-activedescendant={menuOpen && matching.length ? `composer-suggestion-${Math.min(selectedIndex, matching.length - 1)}` : undefined}
        />
        <div className="composer-toolbar">
          <div>
            <button type="button" className="tool-button" onClick={() => fileRef.current?.click()} title="上传文档或表格"><Paperclip size={17} /><span>附件</span></button>
            <button type="button" className="tool-button" onClick={() => imageRef.current?.click()} title="上传图片"><ImagePlus size={17} /><span>图片</span></button>
          </div>
          <button type="button" className="send-button" disabled={props.busy || (!props.value.trim() && !props.attachments.length && !props.selectedAsset)} onClick={props.onSend} aria-label="发送消息">{props.busy ? <LoaderCircle className="spin" size={18} /> : <ArrowUp size={19} />}</button>
        </div>
      </div>
      <div className="composer-hint"><span><kbd>/</kbd> 命令</span><span><kbd>$</kbd> 工具与 Skill</span><span><kbd>Tab</kbd> 补全</span><span>Ctrl / ⌘ + Enter 发送</span></div>
      <input ref={fileRef} type="file" multiple accept=".pdf,.doc,.docx,.txt,.md,.csv,.xls,.xlsx,application/pdf,text/plain,text/csv" hidden onChange={(event) => { props.onFiles(Array.from(event.target.files || [])); event.target.value = ""; }} />
      <input ref={imageRef} type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={(event) => { props.onFiles(Array.from(event.target.files || [])); event.target.value = ""; }} />
    </div>
  );
}
