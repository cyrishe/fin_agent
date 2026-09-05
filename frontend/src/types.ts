export type UnknownRecord = Record<string, unknown>;

export type ResearchMode = "fast" | "auto" | "deep";

export interface AuthUser {
  user_id: string;
  display_name: string;
  mobile_masked: string;
}

export interface AuthSession {
  authenticated: boolean;
  user: AuthUser | null;
}

export interface Attachment {
  attachment_id?: string;
  file_name?: string;
  preview_url?: string;
  kind?: string;
  mime_type?: string;
  [key: string]: unknown;
}

export interface SurfaceBlock {
  block_id: string;
  block_type: string;
  kind?: string;
  semantic?: string;
  payload?: UnknownRecord;
  presentation_hint?: UnknownRecord;
  domain_context?: UnknownRecord;
  meta?: UnknownRecord;
  mode?: "append" | "replace" | string;
  title?: string;
  content?: string;
  data?: UnknownRecord;
  elapsed_ms?: number;
  seq?: number;
  [key: string]: unknown;
}

export interface AgentRun {
  runId?: string;
  status: "running" | "done" | "error";
  summary: string;
  artifacts: SurfaceBlock[];
  process: SurfaceBlock[];
  startedAt?: number;
  finishedAt?: number;
  durationMs?: number;
  tokenUsage?: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
  };
  modelName?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  attachments?: Attachment[];
  run?: AgentRun;
  payload?: UnknownRecord;
  threadId?: number;
  turnId?: number;
  createdAt: number;
}

export interface ThreadSummary {
  thread_id: number;
  title?: string;
  latest_user_input?: string;
  latest_assistant_output?: string;
  last_event_at?: string;
  updated_at?: string;
}

export interface ThreadDetail {
  ok: boolean;
  thread?: ThreadSummary;
  turns?: UnknownRecord[];
  custom_tool_active?: boolean;
  custom_tool_status?: string;
  error?: string;
}

export interface ThreadListResult {
  items: ThreadSummary[];
  activeThreadId: number | null;
}

export interface StreamEvent extends UnknownRecord {
  event?: string;
  event_type?: string;
  run_id?: string;
  message?: string;
  block_id?: string;
  block_type?: string;
  mode?: string;
  title?: string;
  content?: string;
  data?: UnknownRecord;
  result?: UnknownRecord;
  thread_id?: number;
  turn_id?: number;
}

export interface InteractionResponse {
  interaction_id: string;
  action_id: string;
  action: string;
  expected_revision: number;
  label?: string;
  subject_ref?: string;
  answers?: InteractionSubmittedAnswer[];
  feedback_text?: string;
}

export interface InteractionSubmittedAnswer {
  question: string;
  answer: string;
}

export interface InteractionAnswer {
  question_id: string;
  question: string;
  value: string;
  label: string;
  mode: "option" | "custom";
}

export interface InteractionDraft {
  key: string;
  interactionId: string;
  actionId: string;
  action: string;
  expectedRevision: number;
  answers: Record<string, InteractionAnswer>;
}

export interface InteractionFeedbackRequest {
  key: string;
  prompt: string;
  label: string;
  response: InteractionResponse;
}

export interface InvocationInputField {
  name: string;
  label: string;
  description: string;
  type?: string;
  required: boolean;
  defaultValue?: unknown;
}

export interface InvocationAsset {
  ref: string;
  name: string;
  displayName: string;
  summary: string;
  description: string;
  kind: "tool" | "skill";
  invocation: string;
  inputFields: InvocationInputField[];
  aliases: string[];
  tags: string[];
  customTool: boolean;
  editable?: boolean;
  version?: string;
  revision?: number;
  inputSchema?: UnknownRecord;
  sampleInput?: UnknownRecord;
  requiresNaturalLanguage?: boolean;
}

export interface ScheduledTaskStep {
  step_id: string;
  type: "tool" | "skill";
  target_ref: {
    kind: "tool" | "skill";
    name: string;
    version?: string;
    revision?: number;
  };
  inputs: UnknownRecord;
  depends_on: string[];
}

export interface ScheduledTaskDraft {
  requirement_brief: string;
  trigger: {
    cron: string;
    timezone: string;
  };
  execution_plan: {
    steps: ScheduledTaskStep[];
  };
  next_run_at?: string | null;
  preview?: UnknownRecord;
  compile_source?: string;
  llm_usage?: UnknownRecord;
}

export interface ScheduledTask extends ScheduledTaskDraft {
  schedule_id: string;
  enabled: boolean;
  revision_no: number;
  created_at?: string;
  updated_at?: string;
}

export interface ScheduledTaskRun {
  run_id: string;
  schedule_id: string;
  schedule_revision_no: number;
  status: string;
  result?: UnknownRecord | null;
  error_text?: string;
  scheduled_for?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
}
