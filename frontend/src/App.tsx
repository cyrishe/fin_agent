import { Menu, MessageSquarePlus, PanelRight, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { dispatchChat, loadThread, loadThreads, resetThread, startCustomToolStream, uploadAttachments } from "./api";
import Composer from "./components/Composer";
import MessageItem from "./components/MessageItem";
import RunPanel from "./components/RunPanel";
import Sidebar from "./components/Sidebar";
import { applyStreamEvent, blocksFromPayload, initialRun, isProcessBlock } from "./surface";
import type { AgentRun, Attachment, ChatMessage, InteractionResponse, StreamEvent, ThreadSummary, UnknownRecord } from "./types";

const intro: ChatMessage = {
  id: "intro",
  role: "assistant",
  content: "你好，我是 **Fin Agent**。你可以直接描述金融问题，也可以使用 `/custom_tool create` 创建个人金融工具。",
  createdAt: Date.now(),
};

const id = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const asRecord = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};

function hydrateMessages(turns: UnknownRecord[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  turns.forEach((turn, index) => {
    const inputPayload = asRecord(turn.input_payload);
    const attachments = Array.isArray(inputPayload.attachments) ? inputPayload.attachments as Attachment[] : [];
    const userText = String(turn.user_input_text || "").trim();
    if (userText || attachments.length) messages.push({ id: `turn-${index}-user`, role: "user", content: userText || `已发送 ${attachments.length} 个附件`, attachments, createdAt: Date.now() });

    const output = asRecord(turn.output_payload);
    const blocks = blocksFromPayload(output);
    const assistantText = String(turn.assistant_output_text || "").trim();
    if (assistantText || blocks.length) {
      const run: AgentRun | undefined = blocks.length ? {
        status: String(turn.status || "completed") === "failed" ? "error" : "done",
        summary: assistantText || "任务已完成",
        artifacts: blocks.filter((block) => !isProcessBlock(block)),
        process: blocks.filter(isProcessBlock),
      } : undefined;
      messages.push({ id: `turn-${index}-assistant`, role: "assistant", content: run ? "" : assistantText, run, payload: output, createdAt: Date.now() });
    }
  });
  return messages.length ? messages : [intro];
}

export default function App() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadId, setThreadId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([intro]);
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [customToolActive, setCustomToolActive] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [resolvedInteractions, setResolvedInteractions] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const latestRun = useMemo(() => [...messages].reverse().find((message) => message.run)?.run, [messages]);
  const activeThread = threads.find((thread) => thread.thread_id === threadId);

  const refreshThreads = useCallback(async () => {
    try {
      const result = await loadThreads();
      setThreads(result.items);
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    }
  }, []);

  useEffect(() => {
    void (async () => {
      const result = await refreshThreads();
      if (!result?.activeThreadId) return;
      try {
        const detail = await loadThread(result.activeThreadId);
        setThreadId(Number(detail.thread?.thread_id || result.activeThreadId));
        setMessages(hydrateMessages(detail.turns || []));
        setCustomToolActive(Boolean(detail.custom_tool_active));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
  }, [refreshThreads]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: busy ? "auto" : "smooth", block: "end" }); }, [messages.length, busy]);

  const selectThread = async (selectedId: number) => {
    setError("");
    try {
      const detail = await loadThread(selectedId);
      setThreadId(Number(detail.thread?.thread_id || selectedId));
      setMessages(hydrateMessages(detail.turns || []));
      setCustomToolActive(Boolean(detail.custom_tool_active));
      setResolvedInteractions(new Set());
      setLeftOpen(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  const newThread = async () => {
    try { await resetThread(); } catch { /* local reset remains useful */ }
    setThreadId(null);
    setMessages([{ ...intro, id: id(), createdAt: Date.now() }]);
    setCustomToolActive(false);
    setResolvedInteractions(new Set());
    setAttachments([]);
    setInput("");
    setLeftOpen(false);
  };

  const updateRun = useCallback((messageId: string, event: StreamEvent) => {
    setMessages((current) => current.map((message) => {
      if (message.id !== messageId || !message.run) return message;
      const nextRun = applyStreamEvent(message.run, event);
      const result = asRecord(event.result);
      const finalMessage = String(result.message || "").trim();
      const resultBlocks = blocksFromPayload(result);
      const mergedRun = resultBlocks.reduce((run, block) => applyStreamEvent(run, { event: "block", ...block }), nextRun);
      return {
        ...message,
        content: event.event === "done" && !mergedRun.artifacts.length ? finalMessage : message.content,
        run: mergedRun,
      };
    }));
    if (event.thread_id) setThreadId(Number(event.thread_id));
    const patch = asRecord(asRecord(event.result).thread_context_patch);
    if (Object.prototype.hasOwnProperty.call(patch, "custom_tool_state")) setCustomToolActive(Boolean(patch.custom_tool_state));
  }, []);

  const runStream = useCallback(async (text: string, assistantId: string, interactionResponse?: InteractionResponse) => {
    await startCustomToolStream({ text, threadId, interactionResponse, onEvent: (event) => updateRun(assistantId, event) });
    await refreshThreads();
  }, [refreshThreads, threadId, updateRun]);

  const send = async () => {
    const text = input.trim();
    if ((!text && !attachments.length) || busy) return;
    const userMessage: ChatMessage = { id: id(), role: "user", content: text || `已发送 ${attachments.length} 个附件`, attachments, createdAt: Date.now() };
    const assistantId = id();
    const useStream = !attachments.length && (/^\/custom_tool\s+(create|edit)\b/i.test(text) || (customToolActive && !text.startsWith("/")));
    const assistantMessage: ChatMessage = { id: assistantId, role: "assistant", content: "", run: initialRun(useStream ? "正在连接 Agent" : "正在处理你的问题"), createdAt: Date.now() };
    const attachmentIds = attachments.map((item) => String(item.attachment_id || "")).filter(Boolean);
    setMessages((current) => [...current.filter((message) => message.id !== "intro" || current.length === 1), userMessage, assistantMessage]);
    setInput("");
    setAttachments([]);
    setError("");
    setBusy(true);
    try {
      if (useStream) {
        await runStream(text, assistantId);
      } else {
        const payload = await dispatchChat({ text, threadId, attachmentIds });
        if (payload.thread_id) setThreadId(Number(payload.thread_id));
        const patch = asRecord(payload.thread_context_patch);
        if (Object.prototype.hasOwnProperty.call(patch, "custom_tool_state")) setCustomToolActive(Boolean(patch.custom_tool_state));
        const blocks = blocksFromPayload(payload);
        setMessages((current) => current.map((message) => message.id === assistantId ? {
          ...message,
          content: blocks.length ? "" : String(payload.message || "已处理。"),
          payload,
          run: blocks.length ? { status: "done", summary: "任务已完成", artifacts: blocks.filter((block) => !isProcessBlock(block)), process: blocks.filter(isProcessBlock) } : undefined,
        } : message));
        await refreshThreads();
        window.setTimeout(() => void refreshThreads(), 3000);
      }
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      updateRun(assistantId, { event: "error", message });
    } finally { setBusy(false); }
  };

  const interact = async (response: InteractionResponse, label: string) => {
    if (busy) return;
    setResolvedInteractions((current) => new Set(current).add(response.interaction_id));
    const assistantId = id();
    setMessages((current) => [...current, { id: id(), role: "user", content: label, createdAt: Date.now() }, { id: assistantId, role: "assistant", content: "", run: initialRun("正在继续当前任务"), createdAt: Date.now() }]);
    setBusy(true);
    try { await runStream("", assistantId, response); }
    catch (reason) {
      setResolvedInteractions((current) => { const next = new Set(current); next.delete(response.interaction_id); return next; });
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      updateRun(assistantId, { event: "error", message });
    } finally { setBusy(false); }
  };

  const addFiles = async (files: File[]) => {
    if (!files.length) return;
    setError("");
    try {
      const uploaded = await uploadAttachments(files);
      setAttachments((current) => [...current, ...uploaded]);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  return (
    <div className="app-shell">
      <div className={`mobile-backdrop ${leftOpen || rightOpen ? "show" : ""}`} onClick={() => { setLeftOpen(false); setRightOpen(false); }} />
      <div className={`sidebar-slot ${leftOpen ? "mobile-open" : ""}`}><Sidebar threads={threads} activeId={threadId} query={query} onQuery={setQuery} onSelect={(value) => void selectThread(value)} onNew={() => void newThread()} onClose={() => setLeftOpen(false)} /></div>
      <main className="conversation-column">
        <header className="conversation-header">
          <button className="icon-button mobile-only" onClick={() => setLeftOpen(true)} aria-label="打开会话列表"><Menu size={20} /></button>
          <div className="conversation-title"><div className="title-icon"><Sparkles size={17} /></div><div><strong>{activeThread?.title || "Fin Agent"}</strong><span>{threadId ? `会话 #${threadId}` : "新的金融对话"}</span></div></div>
          <div className="header-actions"><button className="header-new" type="button" onClick={() => void newThread()}><MessageSquarePlus size={16} /><span>新对话</span></button><button className="icon-button mobile-only" onClick={() => setRightOpen(true)} aria-label="打开运行信息"><PanelRight size={20} /></button></div>
        </header>
        <section className="message-scroll" aria-live="polite">
          <div className="message-list">{messages.map((message) => <MessageItem key={message.id} message={message} onInsertText={(text) => setInput((current) => text ? `${current}${current ? "\n" : ""}${text}` : "")} onInteraction={(response, label) => void interact(response, label)} resolvedInteractions={resolvedInteractions} />)}<div ref={bottomRef} /></div>
        </section>
        {error && <div className="global-error" role="alert">{error}<button type="button" onClick={() => setError("")}>关闭</button></div>}
        <Composer value={input} onChange={setInput} onSend={() => void send()} busy={busy} attachments={attachments} onFiles={(files) => void addFiles(files)} onRemoveAttachment={(index) => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))} />
      </main>
      <div className={`run-slot ${rightOpen ? "mobile-open" : ""}`}><RunPanel run={latestRun} onClose={() => setRightOpen(false)} /></div>
    </div>
  );
}
