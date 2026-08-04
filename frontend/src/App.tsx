import { Menu, MessageSquarePlus, PanelRight, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { dispatchChat, loadAuthSession, loadInvocationAssets, loadThread, loadThreads, logoutAccount, resetThread, startChatStream, startCustomToolStream, uploadAttachments } from "./api";
import Composer from "./components/Composer";
import MessageItem from "./components/MessageItem";
import RunPanel from "./components/RunPanel";
import ScheduledTasksPanel from "./components/ScheduledTasksPanel";
import Sidebar from "./components/Sidebar";
import { applyStreamEvent, blocksFromPayload, initialRun, isProcessBlock, reconcileBlockOrder, settleProcessBlocks } from "./surface";
import { customAnswerPrompt, prepareClarificationSubmission, readFeedbackValue, removeComposerPrompt, upsertComposerPrompt } from "./interactionDraft";
import type { AgentRun, Attachment, AuthUser, ChatMessage, InteractionDraft, InteractionFeedbackRequest, InteractionResponse, InvocationAsset, StreamEvent, ThreadSummary, UnknownRecord } from "./types";

const intro: ChatMessage = {
  id: "intro",
  role: "assistant",
  content: "你好，我是 **Fin Agent**。你可以直接描述金融问题，也可以使用 `/custom_tool create` 创建个人金融工具。",
  createdAt: Date.now(),
};

const id = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const asRecord = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};
const parseTimestamp = (value: unknown): number => {
  const text = String(value || "").trim();
  if (!text) return 0;
  const parsed = Date.parse(text.includes("T") ? text : text.replace(" ", "T"));
  return Number.isFinite(parsed) ? parsed : 0;
};
const readTokenUsage = (value: unknown): AgentRun["tokenUsage"] | undefined => {
  const source = asRecord(value);
  const promptTokens = Number(source.prompt_tokens ?? source.input_tokens ?? 0) || 0;
  const completionTokens = Number(source.completion_tokens ?? source.output_tokens ?? 0) || 0;
  const totalTokens = Number(source.total_tokens ?? promptTokens + completionTokens) || 0;
  return totalTokens > 0 ? { promptTokens, completionTokens, totalTokens } : undefined;
};
const readPendingPrompt = (): string => {
  if (typeof window === "undefined") return "";
  return String(window.sessionStorage.getItem("fin_agent.pending_prompt") || "").trim();
};

function hydrateMessages(turns: UnknownRecord[], threadId: number): ChatMessage[] {
  const messages: ChatMessage[] = [];
  turns.forEach((turn, index) => {
    const startedAt = parseTimestamp(turn.started_at);
    const finishedAt = parseTimestamp(turn.finished_at);
    const durationMs = startedAt && finishedAt && finishedAt >= startedAt ? finishedAt - startedAt : undefined;
    const inputPayload = asRecord(turn.input_payload);
    const attachments = Array.isArray(inputPayload.attachments) ? inputPayload.attachments as Attachment[] : [];
    const userText = String(turn.user_input_text || "").trim();
    if (userText || attachments.length) messages.push({ id: `turn-${index}-user`, role: "user", content: userText || `已发送 ${attachments.length} 个附件`, attachments, createdAt: startedAt || Date.now() });

    const output = asRecord(turn.output_payload);
    const blocks = blocksFromPayload(output);
    const assistantText = String(turn.assistant_output_text || "").trim();
    if (assistantText || blocks.length) {
      const runStatus: AgentRun["status"] = String(turn.status || "completed") === "failed" ? "error" : "done";
      const run: AgentRun = {
        status: runStatus,
        summary: assistantText || "本轮处理完成",
        artifacts: blocks.filter((block) => !isProcessBlock(block)),
        process: settleProcessBlocks(blocks.filter(isProcessBlock), runStatus),
        startedAt: startedAt || undefined,
        finishedAt: finishedAt || undefined,
        durationMs,
        tokenUsage: readTokenUsage(turn.token_usage),
        modelName: String(turn.model_name || "").trim() || undefined,
      };
      messages.push({ id: `turn-${index}-assistant`, role: "assistant", content: blocks.length ? "" : assistantText, run, payload: output, threadId, turnId: Number(turn.turn_id) || undefined, createdAt: finishedAt || startedAt || Date.now() });
    }
  });
  return messages.length ? messages : [intro];
}

export default function App() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadId, setThreadId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([intro]);
  const [input, setInput] = useState(readPendingPrompt);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [, setCustomToolActive] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [interactionDrafts, setInteractionDrafts] = useState<Record<string, InteractionDraft>>({});
  const [selectedInteractions, setSelectedInteractions] = useState<Record<string, string>>({});
  const [submittedInteractions, setSubmittedInteractions] = useState<Set<string>>(new Set());
  const [pendingFeedback, setPendingFeedback] = useState<InteractionFeedbackRequest | null>(null);
  const [composerFocusRequest, setComposerFocusRequest] = useState(0);
  const [error, setError] = useState("");
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [invocationAssets, setInvocationAssets] = useState<InvocationAsset[]>([]);
  const [selectedInvocationAsset, setSelectedInvocationAsset] = useState<InvocationAsset | null>(null);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const latestRunMessage = useMemo(() => [...messages].reverse().find((message) => message.run), [messages]);
  const latestRun = latestRunMessage?.run;
  const latestRunSignal = useMemo(() => {
    if (!latestRun) return "";
    const latestArtifact = latestRun.artifacts.at(-1);
    const latestProcess = latestRun.process.at(-1);
    return [
      latestRun.status,
      latestRun.summary,
      latestArtifact?.block_id,
      String(asRecord(latestArtifact?.data).summary || latestArtifact?.content || ""),
      latestProcess?.block_id,
      String(asRecord(latestProcess?.data).summary || latestProcess?.content || ""),
    ].join("|");
  }, [latestRun]);
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
  const refreshInvocationAssets = useCallback(async () => {
    try {
      setInvocationAssets(await loadInvocationAssets());
    } catch {
      // Keep the last usable catalog when a background refresh fails.
    }
  }, []);

  useEffect(() => {
    const pendingPrompt = readPendingPrompt();
    if (pendingPrompt) {
      window.sessionStorage.removeItem("fin_agent.pending_prompt");
      setComposerFocusRequest((current) => current + 1);
    }
    void loadAuthSession()
      .then((session) => setAuthUser(session.user))
      .catch(() => setAuthUser(null));
  }, []);
  useEffect(() => {
    void (async () => {
      const result = await refreshThreads();
      if (!result?.activeThreadId) return;
      try {
        const detail = await loadThread(result.activeThreadId);
        setThreadId(Number(detail.thread?.thread_id || result.activeThreadId));
        setMessages(hydrateMessages(detail.turns || [], Number(detail.thread?.thread_id || result.activeThreadId)));
        setCustomToolActive(Boolean(detail.custom_tool_active));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
  }, [refreshThreads]);
  useEffect(() => { void refreshInvocationAssets(); }, [refreshInvocationAssets]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: busy ? "auto" : "smooth", block: "end" }); }, [messages.length, busy]);
  useEffect(() => {
    if (busy) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [busy, latestRunSignal]);

  const selectThread = async (selectedId: number) => {
    setError("");
    setScheduleOpen(false);
    try {
      const detail = await loadThread(selectedId);
      setThreadId(Number(detail.thread?.thread_id || selectedId));
      setMessages(hydrateMessages(detail.turns || [], Number(detail.thread?.thread_id || selectedId)));
      setCustomToolActive(Boolean(detail.custom_tool_active));
      setInteractionDrafts({});
      setSelectedInteractions({});
      setSubmittedInteractions(new Set());
      setPendingFeedback(null);
      setSelectedInvocationAsset(null);
      setLeftOpen(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  const newThread = async () => {
    try { await resetThread(); } catch { /* local reset remains useful */ }
    setThreadId(null);
    setMessages([{ ...intro, id: id(), createdAt: Date.now() }]);
    setCustomToolActive(false);
    setInteractionDrafts({});
    setSelectedInteractions({});
    setSubmittedInteractions(new Set());
    setPendingFeedback(null);
    setAttachments([]);
    setSelectedInvocationAsset(null);
    setInput("");
    setLeftOpen(false);
    setScheduleOpen(false);
  };

  const updateRun = useCallback((messageId: string, event: StreamEvent) => {
    const result = asRecord(event.result);
    const eventType = String(event.event || event.event_type || "");
    const turnMeta = asRecord(result.turn_meta);
    const serverStartedAt = parseTimestamp(turnMeta.started_at);
    const finalEvent = ["done", "run.finished", "error", "stream.error"].includes(eventType);
    setMessages((current) => {
      const assistantIndex = current.findIndex((message) => message.id === messageId);
      return current.map((message, index) => {
        if (finalEvent && serverStartedAt && index === assistantIndex - 1 && message.role === "user") {
          return { ...message, createdAt: serverStartedAt };
        }
        if (message.id !== messageId || !message.run) return message;
        const nextRun = applyStreamEvent(message.run, event);
        const finalMessage = String(result.message || "").trim();
        const resultBlocks = blocksFromPayload(result);
        let mergedRun = resultBlocks.reduce((run, block) => applyStreamEvent(run, { event: "block", ...block }), nextRun);
        if (finalEvent) {
          const canonicalArtifacts = resultBlocks.filter((block) => !isProcessBlock(block));
          const canonicalProcess = resultBlocks.filter(isProcessBlock);
          const serverFinishedAt = parseTimestamp(turnMeta.finished_at);
          const finishedAt = serverFinishedAt || Date.now();
          const startedAt = serverStartedAt || mergedRun.startedAt;
          const serverDurationMs = Number(turnMeta.duration_ms);
          mergedRun = {
            ...mergedRun,
            artifacts: reconcileBlockOrder(mergedRun.artifacts, canonicalArtifacts),
            process: settleProcessBlocks(
              reconcileBlockOrder(mergedRun.process, canonicalProcess),
              mergedRun.status,
            ),
            startedAt,
            finishedAt,
            durationMs: Number.isFinite(serverDurationMs) && serverDurationMs >= 0
              ? serverDurationMs
              : startedAt
                ? Math.max(0, finishedAt - startedAt)
                : undefined,
            tokenUsage: readTokenUsage(result.llm_usage) || mergedRun.tokenUsage,
            modelName: String(result.model_name || "").trim() || mergedRun.modelName,
          };
        }
        return {
          ...message,
          content: event.event === "done" && !mergedRun.artifacts.length ? finalMessage : message.content,
          run: mergedRun,
          payload: finalEvent ? result : message.payload,
          threadId: Number(event.thread_id) || message.threadId,
          turnId: Number(event.turn_id) || message.turnId,
          createdAt: mergedRun.finishedAt || message.createdAt,
        };
      });
    });
    if (event.thread_id) setThreadId(Number(event.thread_id));
    const patch = asRecord(asRecord(event.result).thread_context_patch);
    if (Object.prototype.hasOwnProperty.call(patch, "custom_tool_state")) setCustomToolActive(Boolean(patch.custom_tool_state));
    if (event.event === "done" && String(asRecord(event.result).mode || "") !== "asset_invocation_needs_input") {
      setSelectedInvocationAsset(null);
    }
  }, []);

  const runStream = useCallback(async (
    text: string,
    assistantId: string,
    interactionResponse?: InteractionResponse,
    invocation?: { attachmentIds?: string[]; selectedAsset?: { ref?: string; kind: "tool" | "skill"; name: string } | null },
    streamKind: "chat" | "custom_tool" = "custom_tool",
  ) => {
    const startStream = streamKind === "chat" ? startChatStream : startCustomToolStream;
    await startStream({ text, threadId, interactionResponse, ...invocation, onEvent: (event) => updateRun(assistantId, event) });
    await refreshThreads();
    void refreshInvocationAssets();
  }, [refreshInvocationAssets, refreshThreads, threadId, updateRun]);

  const interact = async (response: InteractionResponse, label: string, key: string, text = "") => {
    if (busy) return;
    setSelectedInteractions((current) => ({ ...current, [key]: response.action_id }));
    setSubmittedInteractions((current) => new Set(current).add(key));
    const assistantId = id();
    setMessages((current) => [...current, { id: id(), role: "user", content: label, createdAt: Date.now() }, { id: assistantId, role: "assistant", content: "", run: initialRun("正在继续当前任务"), createdAt: Date.now() }]);
    setBusy(true);
    setError("");
    try { await runStream(text, assistantId, { ...response, label }); }
    catch (reason) {
      setSubmittedInteractions((current) => { const next = new Set(current); next.delete(key); return next; });
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      updateRun(assistantId, { event: "error", message });
    } finally { setBusy(false); }
  };

  const requestCustomAnswer = (question: string) => {
    setInput((current) => upsertComposerPrompt(current, customAnswerPrompt(question)));
    setComposerFocusRequest((current) => current + 1);
  };

  const clearCustomAnswer = (question: string) => {
    setInput((current) => removeComposerPrompt(current, customAnswerPrompt(question)));
  };

  const submitDraft = async (draft: InteractionDraft) => {
    if (busy) return;
    const submission = prepareClarificationSubmission(draft, input);
    if (submission.missing.length) {
      setError(`请先填写：${submission.missing.join("、")}`);
      setComposerFocusRequest((current) => current + 1);
      return;
    }
    setInput("");
    await interact(submission.response, submission.summary || "已确认待确认项", draft.key);
  };

  const requestFeedback = (request: InteractionFeedbackRequest) => {
    setPendingFeedback(request);
    setSelectedInteractions((current) => ({ ...current, [request.key]: request.response.action_id }));
    setInput((current) => upsertComposerPrompt(current, request.prompt));
    setComposerFocusRequest((current) => current + 1);
  };

  const submitFeedback = async () => {
    if (!pendingFeedback || busy) return;
    const value = readFeedbackValue(input, pendingFeedback.prompt);
    if (!value) {
      setError("请先在输入框中写明希望 Fin Agent 如何处理");
      setComposerFocusRequest((current) => current + 1);
      return;
    }
    const request = pendingFeedback;
    setInput("");
    setPendingFeedback(null);
    await interact({ ...request.response, feedback_text: value }, `${request.prompt}${value}`, request.key, value);
  };

  const send = async () => {
    if (pendingFeedback) {
      await submitFeedback();
      return;
    }
    const pendingCustomDraft = Object.values(interactionDrafts).reverse().find((draft) =>
      !submittedInteractions.has(draft.key) && Object.values(draft.answers).some((answer) => answer.mode === "custom"),
    );
    if (pendingCustomDraft) {
      await submitDraft(pendingCustomDraft);
      return;
    }
    const text = input.trim();
    if ((!text && !attachments.length && !selectedInvocationAsset) || busy) return;
    const invocationLabel = selectedInvocationAsset ? `$${selectedInvocationAsset.name}` : "";
    const userMessage: ChatMessage = {
      id: id(),
      role: "user",
      content: [invocationLabel, text].filter(Boolean).join(" ") || `已发送 ${attachments.length} 个附件`,
      attachments,
      createdAt: Date.now(),
    };
    const assistantId = id();
    const isAssetInvocation = Boolean(selectedInvocationAsset) || text.startsWith("$");
    const useStream = isAssetInvocation || !attachments.length;
    const assistantMessage: ChatMessage = { id: assistantId, role: "assistant", content: "", run: initialRun(useStream ? "正在连接 Agent" : "正在处理你的问题"), createdAt: Date.now() };
    const attachmentIds = attachments.map((item) => String(item.attachment_id || "")).filter(Boolean);
    setMessages((current) => [...current.filter((message) => message.id !== "intro" || current.length === 1), userMessage, assistantMessage]);
    setInput("");
    setAttachments([]);
    setError("");
    setBusy(true);
    try {
      if (useStream) {
        await runStream(text, assistantId, undefined, {
          attachmentIds,
          selectedAsset: selectedInvocationAsset ? {
            ref: selectedInvocationAsset.ref,
            kind: selectedInvocationAsset.kind,
            name: selectedInvocationAsset.name,
          } : null,
        }, "chat");
      } else {
        const payload = await dispatchChat({
          text,
          threadId,
          attachmentIds,
          selectedAsset: selectedInvocationAsset ? {
            ref: selectedInvocationAsset.ref,
            kind: selectedInvocationAsset.kind,
            name: selectedInvocationAsset.name,
          } : null,
        });
        if (payload.thread_id) setThreadId(Number(payload.thread_id));
        const patch = asRecord(payload.thread_context_patch);
        if (Object.prototype.hasOwnProperty.call(patch, "custom_tool_state")) setCustomToolActive(Boolean(patch.custom_tool_state));
        const blocks = blocksFromPayload(payload);
        const turnMeta = asRecord(payload.turn_meta);
        const serverStartedAt = parseTimestamp(turnMeta.started_at);
        const serverFinishedAt = parseTimestamp(turnMeta.finished_at);
        const completedAt = serverFinishedAt || Date.now();
        const serverDurationMs = Number(turnMeta.duration_ms);
        setMessages((current) => {
          const assistantIndex = current.findIndex((message) => message.id === assistantId);
          return current.map((message, index) => {
            if (serverStartedAt && index === assistantIndex - 1 && message.role === "user") {
              return { ...message, createdAt: serverStartedAt };
            }
            return message.id === assistantId ? {
              ...message,
              content: blocks.length ? "" : String(payload.message || "已处理。"),
              payload,
              threadId: Number(payload.thread_id) || threadId || undefined,
              turnId: Number(payload.turn_id) || undefined,
              createdAt: completedAt,
              run: {
                status: "done",
                summary: "本轮处理完成",
                artifacts: blocks.filter((block) => !isProcessBlock(block)),
                process: blocks.filter(isProcessBlock),
                startedAt: serverStartedAt || message.run?.startedAt,
                finishedAt: completedAt,
                durationMs: Number.isFinite(serverDurationMs) && serverDurationMs >= 0
                  ? serverDurationMs
                  : message.run?.startedAt
                    ? Math.max(0, completedAt - message.run.startedAt)
                    : undefined,
                tokenUsage: readTokenUsage(payload.llm_usage),
                modelName: String(payload.model_name || "").trim() || undefined,
              },
            } : message;
          });
        });
        if (String(payload.mode || "") !== "asset_invocation_needs_input") setSelectedInvocationAsset(null);
        await refreshThreads();
        window.setTimeout(() => void refreshThreads(), 3000);
      }
    } catch (reason) {
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

  const logout = async () => {
    try {
      await logoutAccount();
      window.location.assign("/");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "退出登录失败，请稍后重试。");
    }
  };

  const focusArtifact = useCallback((blockId: string) => {
    const messageId = latestRunMessage?.id;
    if (!messageId) return;
    const messageNode = Array.from(document.querySelectorAll<HTMLElement>("[data-message-id]"))
      .find((node) => node.dataset.messageId === messageId);
    const target = Array.from(messageNode?.querySelectorAll<HTMLElement>("[data-block-id]") || [])
      .find((node) => node.dataset.blockId === blockId);
    if (!target) return;
    setRightOpen(false);
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
    target.classList.remove("surface-block-target");
    window.requestAnimationFrame(() => target.classList.add("surface-block-target"));
    window.setTimeout(() => target.classList.remove("surface-block-target"), 1_600);
  }, [latestRunMessage]);

  return (
    <div className="app-shell">
      <div className={`mobile-backdrop ${leftOpen || rightOpen ? "show" : ""}`} onClick={() => { setLeftOpen(false); setRightOpen(false); }} />
      <div className={`sidebar-slot ${leftOpen ? "mobile-open" : ""}`}><Sidebar threads={threads} activeId={scheduleOpen ? null : threadId} query={query} onQuery={setQuery} onSelect={(value) => void selectThread(value)} onNew={() => void newThread()} onOpenSchedules={() => { setScheduleOpen(true); setLeftOpen(false); }} onClose={() => setLeftOpen(false)} authUser={authUser} onLogout={() => void logout()} /></div>
      <main className="conversation-column">
        <header className="conversation-header">
          <button className="icon-button mobile-only" onClick={() => setLeftOpen(true)} aria-label="打开会话列表"><Menu size={20} /></button>
          <div className="conversation-title"><div className="title-icon"><Sparkles size={17} /></div><div><strong>{scheduleOpen ? "定时任务" : activeThread?.title || "Fin Agent"}</strong><span>{scheduleOpen ? "自然语言调度工作台" : threadId ? `会话 #${threadId}` : "新的金融对话"}</span></div></div>
          <div className="header-actions"><button className="header-new" type="button" onClick={() => void newThread()}><MessageSquarePlus size={16} /><span>新对话</span></button><button className="icon-button mobile-only" onClick={() => setRightOpen(true)} aria-label="打开运行信息"><PanelRight size={20} /></button></div>
        </header>
        {scheduleOpen ? <ScheduledTasksPanel /> : (
          <>
            <section className="message-scroll" aria-live="polite">
              <div className="message-list">{messages.map((message) => <MessageItem
                key={message.id}
                message={message}
                interactionDrafts={interactionDrafts}
                selectedInteractions={selectedInteractions}
                submittedInteractions={submittedInteractions}
                disabled={busy}
                onDraftChange={(draft) => setInteractionDrafts((current) => ({ ...current, [draft.key]: draft }))}
                onRequestCustomAnswer={requestCustomAnswer}
                onClearCustomAnswer={clearCustomAnswer}
                onSubmitDraft={(draft) => void submitDraft(draft)}
                onInteraction={(response, label, key) => void interact(response, label, key)}
                onRequestFeedback={requestFeedback}
                onSubmitFeedback={() => void submitFeedback()}
                onUseAsset={(asset) => {
                  const catalogAsset = invocationAssets.find((item) => item.kind === asset.kind && item.name === asset.name);
                  setSelectedInvocationAsset(catalogAsset || {
                    ref: `${asset.kind}:${asset.name}`,
                    kind: asset.kind,
                    name: asset.name,
                    displayName: asset.displayName,
                    summary: asset.description,
                    description: asset.description,
                    invocation: `$${asset.name}`,
                    inputFields: [],
                    aliases: [],
                    tags: [],
                    customTool: true,
                  });
                  setComposerFocusRequest((current) => current + 1);
                }}
              />)}<div ref={bottomRef} /></div>
            </section>
            {error && <div className="global-error" role="alert">{error}<button type="button" onClick={() => setError("")}>关闭</button></div>}
            <Composer value={input} onChange={setInput} onSend={() => void send()} busy={busy} focusRequest={composerFocusRequest} assets={invocationAssets} selectedAsset={selectedInvocationAsset} onSelectAsset={setSelectedInvocationAsset} onClearSelectedAsset={() => setSelectedInvocationAsset(null)} attachments={attachments} onFiles={(files) => void addFiles(files)} onRemoveAttachment={(index) => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))} />
          </>
        )}
      </main>
      <div className={`run-slot ${rightOpen ? "mobile-open" : ""}`}><RunPanel run={latestRun} onArtifactSelect={focusArtifact} onClose={() => setRightOpen(false)} /></div>
    </div>
  );
}
