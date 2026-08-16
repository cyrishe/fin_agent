import { ArrowUp, Blocks, ImagePlus, LoaderCircle, Paperclip, Sparkles, Terminal, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  completeSuggestion,
  completeEditTool,
  filterSuggestions,
  findInvocation,
  removeInvocation,
  type ComposerSuggestion,
} from "../composerSuggestions";
import type { Attachment, InvocationAsset, ResearchMode } from "../types";

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
  researchMode: ResearchMode;
  onResearchModeChange: (mode: ResearchMode) => void;
}

const researchModes: Array<{
  value: ResearchMode;
  label: string;
  description: string;
}> = [
  { value: "fast", label: "快速", description: "聚焦核心结论和最少必要证据" },
  { value: "auto", label: "智能", description: "由业务 Skill 判断本题需要的分析深度" },
  { value: "deep", label: "深度", description: "扩展关键证据、反证和验证点" },
];

export default function Composer(props: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const selectedRef = useRef<HTMLButtonElement>(null);
  const composingRef = useRef(false);
  const [focused, setFocused] = useState(false);
  const [cursor, setCursor] = useState(() => props.value.length);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [dismissedInvocation, setDismissedInvocation] = useState<string | null>(null);
  const invocation = useMemo(() => findInvocation(props.value, cursor), [cursor, props.value]);
  const invocationKey = invocation ? `${props.value}:${invocation.start}:${invocation.end}` : "";
  const assetSuggestions = useMemo<ComposerSuggestion[]>(() => props.assets.map((asset) => ({
    id: asset.ref,
    kind: asset.kind,
    value: asset.invocation,
    label: asset.displayName,
    description: asset.summary,
    keywords: [...asset.aliases, ...asset.tags],
    customTool: asset.customTool,
    editable: asset.editable,
    assetName: asset.name,
  })), [props.assets]);
  const matching = useMemo(() => filterSuggestions(invocation, assetSuggestions), [invocation, assetSuggestions]);
  const invocationFields = useMemo(() => {
    if (props.selectedAsset?.inputFields?.length) {
      return [...props.selectedAsset.inputFields]
        .sort((left, right) => Number(right.required) - Number(left.required));
    }
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
        ...(definition.default !== null && definition.default !== undefined
          ? { defaultValue: definition.default }
          : {}),
      };
    }).sort((left, right) => Number(right.required) - Number(left.required));
  }, [props.selectedAsset]);
  const menuOpen = Boolean(invocation) &&
    invocationKey !== dismissedInvocation &&
    (invocation?.trigger === "$" || invocation?.context === "edit_tool" || matching.length > 0 || invocation?.query === "/");

  useEffect(() => { setSelectedIndex(0); }, [invocation?.query]);
  useEffect(() => {
    setSelectedIndex((current) => Math.min(current, Math.max(0, matching.length - 1)));
  }, [matching.length]);
  useEffect(() => { selectedRef.current?.scrollIntoView({ block: "nearest" }); }, [selectedIndex]);
  useEffect(() => {
    if (!props.focusRequest) return;
    window.requestAnimationFrame(() => {
      const textarea = textRef.current;
      textarea?.focus();
      textarea?.setSelectionRange(textarea.value.length, textarea.value.length);
      setCursor(textarea?.value.length || 0);
    });
  }, [props.focusRequest]);
  useEffect(() => {
    setCursor((current) => Math.min(current, props.value.length));
  }, [props.value.length]);

  useEffect(() => {
    const textarea = textRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(180, Math.max(52, textarea.scrollHeight))}px`;
  }, [props.value]);

  const choose = (suggestion: ComposerSuggestion) => {
    if (!invocation) return;
    if (invocation.context === "edit_tool") {
      const completed = completeEditTool(props.value, invocation, suggestion);
      props.onChange(completed.value);
      setCursor(completed.cursor);
      setDismissedInvocation(`${completed.value}:${completed.cursor}:${completed.cursor}`);
      window.requestAnimationFrame(() => {
        textRef.current?.focus();
        textRef.current?.setSelectionRange(completed.cursor, completed.cursor);
      });
      return;
    }
    const isAsset = suggestion.kind === "tool" || suggestion.kind === "skill";
    const completed = isAsset
      ? removeInvocation(props.value, invocation)
      : completeSuggestion(props.value, invocation, suggestion);
    props.onChange(completed.value);
    setCursor(completed.cursor);
    if (isAsset) {
      const asset = props.assets.find((item) => item.ref === suggestion.id);
      if (asset) props.onSelectAsset(asset);
    }
    setDismissedInvocation(
      suggestion.id === "command:custom_tool_edit"
        ? null
        : `${completed.value}:${completed.cursor}:${completed.cursor}`,
    );
    window.requestAnimationFrame(() => {
      textRef.current?.focus();
      textRef.current?.setSelectionRange(completed.cursor, completed.cursor);
    });
  };
  const assetScope = invocation?.trigger === "$" && invocation.query.slice(1).toLowerCase().startsWith("tool:") ? "工具"
    : invocation?.trigger === "$" && invocation.query.slice(1).toLowerCase().startsWith("skill:") ? "Skills"
      : "工具与 Skills";
  const menuTitle = invocation?.context === "edit_tool"
    ? "选择要修改的个人工具"
    : invocation?.trigger === "/" ? "系统命令" : assetScope;
  const menuId = "composer-suggestion-menu";

  return (
    <div className="composer-wrap">
      {menuOpen && <div id={menuId} className="command-menu" role="listbox" aria-label={invocation?.context === "edit_tool" ? "选择要修改的个人工具" : invocation?.trigger === "/" ? "系统命令" : "工具和 Skill"}>
        <div className="command-menu-head"><span>{invocation?.context === "edit_tool" ? <Blocks size={14} /> : invocation?.trigger === "/" ? <Terminal size={14} /> : <Sparkles size={14} />}{menuTitle}<em>{matching.length}</em></span><small>↑↓ 选择 · Tab 补全 · Esc 关闭</small></div>
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
        ><span className={`suggestion-icon ${suggestion.kind}`}>{suggestion.kind === "command" ? <Terminal size={14} /> : suggestion.kind === "tool" ? <Blocks size={14} /> : <Sparkles size={14} />}</span>{suggestion.kind === "command"
          ? <div><code>{suggestion.label}</code><span>{suggestion.description}</span></div>
          : <div className="suggestion-copy"><strong>{suggestion.label}</strong><code>{suggestion.value}</code><span>{suggestion.description || "暂无作用说明"}</span></div>}<small>{suggestion.kind === "command" ? "命令" : suggestion.kind === "skill" ? "Skill" : suggestion.customTool ? "个人工具" : "工具"}</small></button>) : <div className="command-menu-empty">{invocation?.context === "edit_tool" ? "没有匹配的可编辑个人工具，请尝试工具名称、中文名称或作用关键词" : `没有匹配的${invocation?.trigger === "/" ? "系统命令" : "工具或 Skill"}，可尝试输入名称或作用关键词`}</div>}
      </div>}
      {props.attachments.length > 0 && <div className="attachment-preview">{props.attachments.map((attachment, index) => <div key={attachment.attachment_id || index}>{attachment.preview_url ? <img src={attachment.preview_url} alt={attachment.file_name || "附件"} /> : <span className="attachment-file"><Paperclip size={15} />{attachment.file_name || "附件"}</span>}<button type="button" onClick={() => props.onRemoveAttachment(index)} aria-label="移除附件"><X size={13} /></button></div>)}</div>}
      <div className={`composer ${focused ? "focused" : ""}`}>
        {props.selectedAsset && <div className="invocation-guide">
          <span className={`suggestion-icon ${props.selectedAsset.kind}`} aria-hidden="true">{props.selectedAsset.kind === "tool" ? <Blocks size={14} /> : <Sparkles size={14} />}</span>
          <div className="invocation-identity">
            <div><strong>{props.selectedAsset.displayName}</strong><small>{props.selectedAsset.kind === "skill" ? "Skill" : props.selectedAsset.customTool ? "个人工具" : "工具"}</small></div>
            <code>{props.selectedAsset.invocation}</code>
            <span>{props.selectedAsset.summary || "暂无作用说明"}</span>
          </div>
          <div className="invocation-parameters">
            {invocationFields.map((field) => <span key={field.name} className={field.required ? "required" : "optional"} title={field.description}>
              {`<${field.label}${field.defaultValue !== undefined && field.defaultValue !== null ? `=${String(field.defaultValue)}` : ""}>`}
            </span>)}
            {!invocationFields.length && <span className="ready">无需参数，可直接运行</span>}
            {invocationFields.length > 0 && !invocationFields.some((field) => field.required) && <span className="ready">可直接运行</span>}
          </div>
          <button type="button" onClick={props.onClearSelectedAsset} aria-label={`取消调用${props.selectedAsset.displayName}`}><X size={13} /></button>
        </div>}
        <textarea
          ref={textRef}
          value={props.value}
          onChange={(event) => {
            props.onChange(event.target.value);
            setCursor(event.target.selectionStart ?? event.target.value.length);
          }}
          onSelect={(event) => setCursor(event.currentTarget.selectionStart ?? event.currentTarget.value.length)}
          onFocus={(event) => {
            setFocused(true);
            setDismissedInvocation(null);
            setCursor(event.currentTarget.selectionStart ?? event.currentTarget.value.length);
          }}
          onBlur={() => window.setTimeout(() => {
            setFocused(false);
            setDismissedInvocation(invocationKey);
          }, 100)}
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={(event) => {
            composingRef.current = false;
            setCursor(event.currentTarget.selectionStart ?? event.currentTarget.value.length);
          }}
          onKeyDown={(event) => {
            if (composingRef.current || event.nativeEvent.isComposing) return;
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
              setDismissedInvocation(invocationKey);
            }
          }}
          placeholder={props.selectedAsset ? "直接用自然语言填写参数，也可以附加表格或文档批量执行…" : "描述金融问题，输入 / 调用命令，或用 $ 选择工具与 Skill…"}
          role="combobox"
          aria-label="消息输入"
          aria-autocomplete="list"
          aria-haspopup="listbox"
          aria-controls={menuOpen ? menuId : undefined}
          aria-expanded={menuOpen}
          aria-activedescendant={menuOpen && matching.length ? `composer-suggestion-${Math.min(selectedIndex, matching.length - 1)}` : undefined}
        />
        <div className="composer-toolbar">
          <div>
            <button type="button" className="tool-button" onClick={() => fileRef.current?.click()} title="上传文档或表格"><Paperclip size={17} /><span>附件</span></button>
            <button type="button" className="tool-button" onClick={() => imageRef.current?.click()} title="上传图片"><ImagePlus size={17} /><span>图片</span></button>
          </div>
          <div className="research-mode-control" role="group" aria-label="分析模式">
            {researchModes.map((mode) => <button
              key={mode.value}
              type="button"
              className={props.researchMode === mode.value ? "active" : ""}
              aria-pressed={props.researchMode === mode.value}
              disabled={props.busy}
              title={mode.description}
              onClick={() => props.onResearchModeChange(mode.value)}
            >{mode.label}</button>)}
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
