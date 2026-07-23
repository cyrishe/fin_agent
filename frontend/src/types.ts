export type UnknownRecord = Record<string, unknown>;

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
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  attachments?: Attachment[];
  run?: AgentRun;
  payload?: UnknownRecord;
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

export interface InvocationAsset {
  name: string;
  displayName: string;
  description: string;
  kind: "tool" | "skill";
  inputSchema?: UnknownRecord;
  sampleInput?: UnknownRecord;
  requiresNaturalLanguage?: boolean;
}
