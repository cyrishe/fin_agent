import type {
  Attachment,
  AuthSession,
  InteractionResponse,
  InvocationAsset,
  ResearchMode,
  ScheduledTask,
  ScheduledTaskDraft,
  ScheduledTaskRun,
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

  let payload: T & { ok?: boolean; error?: string | { message?: string } };
  try {
    payload = JSON.parse(body) as T & { ok?: boolean; error?: string };
  } catch {
    throw new Error(`服务返回格式异常（HTTP ${response.status}），请刷新后重试`);
  }
  if (!response.ok || payload.ok === false) {
    const error = typeof payload.error === "string"
      ? payload.error
      : String(payload.error?.message || "");
    throw new Error(error || `HTTP ${response.status}`);
  }
  return payload;
}

export async function loadAuthSession(): Promise<AuthSession> {
  const payload = await readJson<{
    ok: boolean;
    authenticated?: boolean;
    user?: AuthSession["user"];
  }>(await fetch("/api/auth/session", { credentials: "include" }));
  return {
    authenticated: Boolean(payload.authenticated),
    user: payload.user || null,
  };
}

export async function loadAuthConfig(): Promise<{
  available: boolean;
  provider: string;
  method: string;
  storage_ready?: boolean;
  possession_available: boolean;
  identity_match_required: boolean;
}> {
  return readJson(await fetch("/api/auth/config", { credentials: "include" }));
}

export async function requestRegistrationCode(input: {
  mobile: string;
}): Promise<{
  challengeId: string;
  mobileMasked: string;
  expiresInSeconds: number;
  resendAfterSeconds: number;
}> {
  const payload = await readJson<{
    ok: boolean;
    challenge_id: string;
    mobile_masked: string;
    expires_in_seconds: number;
    resend_after_seconds: number;
  }>(await fetch("/api/auth/registration-code", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mobile: input.mobile }),
  }));
  return {
    challengeId: String(payload.challenge_id || ""),
    mobileMasked: String(payload.mobile_masked || ""),
    expiresInSeconds: Math.max(0, Number(payload.expires_in_seconds) || 0),
    resendAfterSeconds: Math.max(0, Number(payload.resend_after_seconds) || 0),
  };
}

export async function registerPhoneAccount(input: {
  realName?: string;
  mobile: string;
  challengeId: string;
  verificationCode: string;
  password: string;
  confirmPassword: string;
}): Promise<AuthSession> {
  const payload = await readJson<{
    ok: boolean;
    authenticated: boolean;
    user: AuthSession["user"];
  }>(await fetch("/api/auth/register", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...(input.realName?.trim() ? { real_name: input.realName.trim() } : {}),
      mobile: input.mobile,
      challenge_id: input.challengeId,
      verification_code: input.verificationCode,
      password: input.password,
      confirm_password: input.confirmPassword,
    }),
  }));
  return { authenticated: Boolean(payload.authenticated), user: payload.user || null };
}

export async function loginPhoneAccount(input: {
  mobile: string;
  password: string;
}): Promise<AuthSession> {
  const payload = await readJson<{
    ok: boolean;
    authenticated: boolean;
    user: AuthSession["user"];
  }>(await fetch("/api/auth/login", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mobile: input.mobile,
      password: input.password,
    }),
  }));
  return { authenticated: Boolean(payload.authenticated), user: payload.user || null };
}

export async function logoutAccount(): Promise<void> {
  await readJson(await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }));
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

export async function loadResultPage(input: {
  threadId: number;
  dataRef: string;
  offset: number;
  limit?: number;
}): Promise<{
  rows: Record<string, unknown>[];
  offset: number;
  limit: number;
  returned: number;
  total: number;
  hasMore: boolean;
}> {
  const query = new URLSearchParams({
    thread_id: String(input.threadId),
    data_ref: input.dataRef,
    offset: String(Math.max(0, input.offset)),
    limit: String(Math.max(1, input.limit || 10)),
  });
  const payload = await readJson<{
    ok: boolean;
    rows?: Record<string, unknown>[];
    page?: {
      offset?: number;
      limit?: number;
      returned?: number;
      total?: number;
      has_more?: boolean;
    };
  }>(await fetch(`/api/assistant/results/page?${query.toString()}`, {
    credentials: "include",
  }));
  const page = payload.page || {};
  return {
    rows: Array.isArray(payload.rows) ? payload.rows : [],
    offset: Number(page.offset) || 0,
    limit: Number(page.limit) || Math.max(1, input.limit || 10),
    returned: Number(page.returned) || 0,
    total: Number(page.total) || 0,
    hasMore: Boolean(page.has_more),
  };
}

const asRecord = (value: unknown): UnknownRecord =>
  value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};

const asStringList = (value: unknown): string[] =>
  (Array.isArray(value) ? value : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean);

function invocationInputFields(
  rawFields: unknown,
  inputSchema: UnknownRecord,
): InvocationAsset["inputFields"] {
  if (Array.isArray(rawFields)) {
    return rawFields.map((raw) => {
      const field = asRecord(raw);
      const name = String(field.name || field.field || "").trim();
      const defaultValue = field.default_value ?? field.default;
      return {
        name,
        label: String(field.label || field.title || name).trim(),
        description: String(field.description || "").trim(),
        type: String(field.type || "").trim() || undefined,
        required: Boolean(field.required),
        ...(defaultValue !== null && defaultValue !== undefined ? { defaultValue } : {}),
      };
    }).filter((field) => field.name);
  }

  const properties = asRecord(inputSchema.properties);
  const required = new Set(asStringList(inputSchema.required));
  return Object.entries(properties).map(([name, raw]) => {
    const field = asRecord(raw);
    return {
      name,
      label: String(
        field.title ||
        field.label ||
        (["question", "request"].includes(name) ? "自然语言要求" : name),
      ).trim(),
      description: String(field.description || "").trim(),
      type: String(field.type || "").trim() || undefined,
      required: required.has(name),
      ...(field.default !== null && field.default !== undefined ? { defaultValue: field.default } : {}),
    };
  });
}

function mapInvocationAsset(
  item: UnknownRecord,
  fallbackKind?: "tool" | "skill",
): InvocationAsset | null {
  const rawRef = String(item.ref || item.asset_ref || "").trim();
  const refSeparator = rawRef.indexOf(":");
  const refKind = refSeparator > 0 ? rawRef.slice(0, refSeparator) : "";
  const refName = refSeparator > 0 ? rawRef.slice(refSeparator + 1) : "";
  const rawKind = String(item.kind || item.asset_kind || refKind || fallbackKind || "").trim().toLowerCase();
  if (rawKind !== "tool" && rawKind !== "skill") return null;
  const kind = rawKind as "tool" | "skill";
  const availability = asRecord(item.availability);
  const status = String(item.status || "active").trim().toLowerCase() || "active";
  const lifecycle = String(availability.lifecycle || "active").trim().toLowerCase() || "active";
  const visibility = String(availability.visibility || "visible").trim().toLowerCase() || "visible";
  const auth = String(item.auth || "public").trim().toLowerCase() || "public";
  if (
    item.invocation_enabled === false ||
    status !== "active" ||
    lifecycle !== "active" ||
    visibility === "hidden" ||
    (kind === "skill" && auth !== "public")
  ) return null;
  const name = String(
    item.name ||
    (kind === "tool" ? item.tool_name : item.skill_name) ||
    refName,
  ).trim();
  if (!name) return null;

  const ref = rawRef || `${kind}:${name}`;
  const displayName = String(item.display_name || item.displayName || item.title || name).trim() || name;
  const summary = String(item.summary || item.description || item.purpose || "").trim();
  const inputSchema = asRecord(item.input_schema || item.inputSchema);
  const inputFields = invocationInputFields(item.input_fields || item.inputFields, inputSchema);
  const aliases = asStringList(item.aliases || item.alias);
  const tags = [
    ...asStringList(item.tags),
    ...asStringList(item.best_for),
  ].filter((value, index, values) => values.indexOf(value) === index);
  const invocation = String(item.invocation || `$${name}`).trim() || `$${name}`;
  const revision = Number(item.revision);

  return {
    ref,
    kind,
    name,
    displayName,
    summary,
    description: summary,
    invocation,
    inputFields,
    aliases,
    tags,
    customTool: Boolean(item.custom_tool || item.customTool),
    editable: Boolean(item.editable),
    version: String(item.version || "").trim() || undefined,
    revision: Number.isFinite(revision) ? revision : undefined,
    inputSchema,
    sampleInput: asRecord(item.sample_input || item.sampleInput),
    requiresNaturalLanguage: Boolean(
      item.requires_natural_language ||
      item.requiresNaturalLanguage ||
      inputFields.some((field) => field.name === "question" && field.required),
    ),
  };
}

function mapInvocationAssets(
  items: UnknownRecord[],
  fallbackKind?: "tool" | "skill",
): InvocationAsset[] {
  const assets = items
    .map((item) => mapInvocationAsset(item, fallbackKind))
    .filter((item): item is InvocationAsset => item !== null);
  const byRef = new Map<string, InvocationAsset>();
  assets.forEach((asset) => {
    if (!byRef.has(asset.ref)) byRef.set(asset.ref, asset);
  });
  return [...byRef.values()];
}

async function loadUnifiedInvocationAssets(): Promise<InvocationAsset[] | null> {
  const response = await fetch("/api/assets/invocable", { credentials: "include" });
  if ([404, 405].includes(response.status)) return null;
  const payload = await readJson<{ items?: UnknownRecord[]; assets?: UnknownRecord[] }>(response);
  const items = Array.isArray(payload.items)
    ? payload.items
    : Array.isArray(payload.assets)
      ? payload.assets
      : [];
  return mapInvocationAssets(items);
}

export async function loadInvocationAssets(): Promise<InvocationAsset[]> {
  const unified = await loadUnifiedInvocationAssets();
  if (unified !== null) return unified;

  const [tools, skills] = await Promise.allSettled([
    loadInvocationTools(),
    loadInvocationSkills(),
  ]);
  const assets = [
    ...(tools.status === "fulfilled" ? tools.value : []),
    ...(skills.status === "fulfilled" ? skills.value : []),
  ];
  if (!assets.length && tools.status === "rejected" && skills.status === "rejected") {
    throw tools.reason instanceof Error ? tools.reason : new Error(String(tools.reason));
  }
  return assets;
}

export async function loadInvocationTools(): Promise<InvocationAsset[]> {
  const payload = await readJson<{ items?: UnknownRecord[] }>(await fetch("/api/tools/catalog", { credentials: "include" }));
  return mapInvocationAssets(payload.items || [], "tool");
}

export async function loadInvocationSkills(): Promise<InvocationAsset[]> {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), 2000);
  try {
    const payload = await readJson<{ items?: UnknownRecord[] }>(await fetch("/api/skills/catalog", {
      credentials: "include",
      signal: controller.signal,
    }));
    return mapInvocationAssets(payload.items || [], "skill");
  } finally {
    globalThis.clearTimeout(timeout);
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
  selectedAsset?: Pick<InvocationAsset, "kind" | "name"> & { ref?: string } | null;
  researchMode?: ResearchMode;
  dataOnly?: boolean;
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
      research_mode: input.researchMode || "auto",
      ...(input.dataOnly === undefined ? {} : { data_only: input.dataOnly }),
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

type AgentStreamInput = {
  text: string;
  threadId: number | null;
  interactionResponse?: InteractionResponse;
  attachmentIds?: string[];
  selectedAsset?: { ref?: string; kind: "tool" | "skill"; name: string } | null;
  researchMode?: ResearchMode;
  dataOnly?: boolean;
  onEvent: (event: StreamEvent) => void;
};

async function startAgentStream(
  endpoint: "/api/chat/stream/start" | "/api/custom_tool/stream/start",
  input: AgentStreamInput,
): Promise<void> {
  const started = await readJson<{ ok: boolean; run_id: string; stream_url: string }>(
    await fetch(endpoint, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: input.text,
        thread_id: input.threadId,
        application_name: "investment_workbench",
        attachment_ids: input.attachmentIds || [],
        selected_asset: input.selectedAsset || undefined,
        research_mode: input.researchMode || "auto",
        ...(input.dataOnly === undefined ? {} : { data_only: input.dataOnly }),
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

export async function startChatStream(input: AgentStreamInput): Promise<void> {
  return startAgentStream("/api/chat/stream/start", input);
}

export async function startCustomToolStream(input: AgentStreamInput): Promise<void> {
  return startAgentStream("/api/custom_tool/stream/start", input);
}

export interface CustomToolInteractiveTest {
  run_id: string;
  tool_name: string;
  display_name: string;
  revision: number;
  status: "passed" | "failed" | string;
  elapsed_ms: number;
  input: UnknownRecord;
  contract: UnknownRecord;
  process: UnknownRecord[];
  result: UnknownRecord;
  error: string;
  diagnostics: UnknownRecord;
}

export async function runCustomToolInteractiveTest(input: {
  toolName: string;
  revision: number;
  arguments: UnknownRecord;
}): Promise<CustomToolInteractiveTest> {
  const payload = await readJson<{ ok: boolean; test: CustomToolInteractiveTest }>(
    await fetch(`/api/custom-tools/${encodeURIComponent(input.toolName)}/test`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        revision: input.revision,
        arguments: input.arguments,
      }),
    }),
  );
  return payload.test;
}

export async function previewScheduledTask(instruction: string): Promise<ScheduledTaskDraft> {
  const payload = await readJson<{ ok: boolean; preview: ScheduledTaskDraft }>(
    await fetch("/api/schedules/preview", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction }),
    }),
  );
  return payload.preview;
}

export async function createScheduledTask(input: {
  instruction: string;
  draft: ScheduledTaskDraft;
  idempotencyKey: string;
}): Promise<ScheduledTask> {
  const payload = await readJson<{ ok: boolean; schedule: ScheduledTask }>(
    await fetch("/api/schedules", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": input.idempotencyKey,
      },
      body: JSON.stringify({ instruction: input.instruction, draft: input.draft }),
    }),
  );
  return payload.schedule;
}

export async function loadScheduledTasks(): Promise<ScheduledTask[]> {
  const payload = await readJson<{ ok: boolean; schedules?: ScheduledTask[] }>(
    await fetch("/api/schedules", { credentials: "include" }),
  );
  return Array.isArray(payload.schedules) ? payload.schedules : [];
}

export async function updateScheduledTask(
  scheduleId: string,
  input: { enabled: boolean },
): Promise<ScheduledTask> {
  const payload = await readJson<{ ok: boolean; schedule: ScheduledTask }>(
    await fetch(`/api/schedules/${encodeURIComponent(scheduleId)}`, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
  return payload.schedule;
}

export async function runScheduledTask(scheduleId: string): Promise<ScheduledTaskRun> {
  const payload = await readJson<{ ok: boolean; run: ScheduledTaskRun }>(
    await fetch(`/api/schedules/${encodeURIComponent(scheduleId)}/run`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }),
  );
  return payload.run;
}

export async function loadScheduledTaskRuns(
  scheduleId: string,
  limit = 50,
): Promise<ScheduledTaskRun[]> {
  const payload = await readJson<{ ok: boolean; runs?: ScheduledTaskRun[] }>(
    await fetch(`/api/schedules/${encodeURIComponent(scheduleId)}/runs?limit=${limit}`, {
      credentials: "include",
    }),
  );
  return Array.isArray(payload.runs) ? payload.runs : [];
}
