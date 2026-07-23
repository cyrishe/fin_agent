import type {
  Attachment,
  InteractionResponse,
  InvocationAsset,
  StreamEvent,
  ThreadDetail,
  ThreadListResult,
  ThreadSummary,
  UnknownRecord,
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  const body = await response.text();
  if (!body.trim()) {
    throw new Error(`服务未返回内容（HTTP ${response.status}），可能正在重启，请稍后重试`);
  }

  let payload: T & { ok?: boolean; error?: string };
  try {
    payload = JSON.parse(body) as T & { ok?: boolean; error?: string };
  } catch {
    throw new Error(`服务返回格式异常（HTTP ${response.status}），请刷新后重试`);
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

export async function loadThreads(): Promise<ThreadListResult> {
  const payload = await readJson<{ ok: boolean; items: ThreadSummary[]; active_thread_id?: number | null }>(
    await fetch("/api/assistant/threads", { credentials: "include" }),
  );
  return {
    items: Array.isArray(payload.items) ? payload.items : [],
    activeThreadId: payload.active_thread_id ? Number(payload.active_thread_id) : null,
  };
}

export async function loadThread(threadId: number): Promise<ThreadDetail> {
  return readJson<ThreadDetail>(
    await fetch(`/api/assistant/threads/${encodeURIComponent(threadId)}`, {
      credentials: "include",
    }),
  );
}

export async function loadInvocationAssets(): Promise<InvocationAsset[]> {
  const loadCatalog = async (path: string) => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 2000);
    try {
      return await readJson<{ items?: UnknownRecord[] }>(await fetch(path, {
        credentials: "include",
        signal: controller.signal,
      }));
    } finally {
      window.clearTimeout(timeout);
    }
  };
  const [tools, skills] = await Promise.all([
    loadCatalog("/api/tools/catalog"),
    loadCatalog("/api/skills/catalog").catch(() => ({ items: [] })),
  ]);
  return mapInvocationAssets(tools.items, skills.items);
}

function mapInvocationAssets(toolItems: UnknownRecord[] = [], skillItems: UnknownRecord[] = []): InvocationAsset[] {
  const record = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};
  return [
    ...toolItems.map((item) => ({
      name: String(item.tool_name || item.name || ""),
      displayName: String(item.display_name || item.tool_name || item.name || ""),
      description: String(item.description || ""),
      kind: "tool" as const,
      inputSchema: record(item.input_schema),
      sampleInput: record(item.sample_input),
      requiresNaturalLanguage: Boolean(item.requires_natural_language),
    })),
    ...skillItems.map((item) => ({
      name: String(item.skill_name || item.name || ""),
      displayName: String(item.display_name || item.skill_name || item.name || ""),
      description: String(item.description || item.purpose || ""),
      kind: "skill" as const,
      inputSchema: record(item.input_schema),
      sampleInput: record(item.sample_input),
      requiresNaturalLanguage: Boolean(item.requires_natural_language),
    })),
  ].filter((item) => item.name);
}

export async function loadInvocationTools(): Promise<InvocationAsset[]> {
  const payload = await readJson<{ items?: UnknownRecord[] }>(await fetch("/api/tools/catalog", { credentials: "include" }));
  return mapInvocationAssets(payload.items || [], []);
}

export async function loadInvocationSkills(): Promise<InvocationAsset[]> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 2000);
  try {
    const payload = await readJson<{ items?: UnknownRecord[] }>(await fetch("/api/skills/catalog", {
      credentials: "include",
      signal: controller.signal,
    }));
    return mapInvocationAssets([], payload.items || []);
  } finally {
    window.clearTimeout(timeout);
  }
}

export async function resetThread(): Promise<void> {
  await readJson(await fetch("/api/assistant/thread/reset", {
    method: "POST",
    credentials: "include",
  }));
}

export async function dispatchChat(input: {
  text: string;
  threadId: number | null;
  attachmentIds: string[];
  selectedAsset?: Pick<InvocationAsset, "kind" | "name"> | null;
}): Promise<UnknownRecord> {
  return readJson<UnknownRecord>(await fetch("/api/chat/dispatch", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: input.text,
      thread_id: input.threadId,
      application_name: "investment_workbench",
      attachment_ids: input.attachmentIds,
      selected_asset: input.selectedAsset || undefined,
    }),
  }));
}

export async function uploadAttachments(files: File[]): Promise<Attachment[]> {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  const payload = await readJson<{ ok: boolean; items: Attachment[] }>(
    await fetch("/api/attachments/upload", {
      method: "POST",
      credentials: "include",
      body,
    }),
  );
  return payload.items || [];
}

export async function startCustomToolStream(input: {
  text: string;
  threadId: number | null;
  interactionResponse?: InteractionResponse;
  attachmentIds?: string[];
  selectedAsset?: { kind: "tool" | "skill"; name: string } | null;
  onEvent: (event: StreamEvent) => void;
}): Promise<void> {
  const started = await readJson<{ ok: boolean; run_id: string; stream_url: string }>(
    await fetch("/api/custom_tool/stream/start", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: input.text,
        thread_id: input.threadId,
        application_name: "investment_workbench",
        attachment_ids: input.attachmentIds || [],
        selected_asset: input.selectedAsset || undefined,
        ...(input.interactionResponse ? { interaction_response: input.interactionResponse } : {}),
      }),
    }),
  );

  await new Promise<void>((resolve, reject) => {
    const source = new EventSource(started.stream_url, { withCredentials: true });
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      source.close();
      if (error) reject(error);
      else resolve();
    };

    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data || "{}") as StreamEvent;
        input.onEvent(event);
        const type = String(event.event || event.event_type || "");
        if (["done", "run.finished"].includes(type)) finish();
        if (["error", "stream.error"].includes(type)) {
          finish(new Error(String(event.message || "Agent 运行失败")));
        }
      } catch (error) {
        finish(error instanceof Error ? error : new Error(String(error)));
      }
    };
    source.onerror = () => finish(new Error("Agent 流连接中断"));
  });
}
