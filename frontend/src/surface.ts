import type { AgentRun, StreamEvent, SurfaceBlock, UnknownRecord } from "./types";

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRecord
    : {};
}

export function isProcessBlock(block: SurfaceBlock): boolean {
  const type = String(block.block_type || "");
  const id = String(block.block_id || "");
  const role = String(asRecord(block.data).role || "");
  if (role === "conversation_progress") return false;
  return type === "status" || role === "process" || role === "live_progress" ||
    id.includes("_thinking") || id.includes("_assistant") || id.includes("_tool_output");
}

export function normalizeBlock(event: StreamEvent): SurfaceBlock {
  return {
    ...event,
    block_id: String(event.block_id || event.target || `block_${event.seq || Date.now()}`),
    block_type: String(event.block_type || event.kind || event.type || "narrative"),
    mode: String(event.mode || "replace"),
    title: event.title ? String(event.title) : undefined,
    content: event.content ? String(event.content) : "",
    data: asRecord(event.data || event.payload),
  } as SurfaceBlock;
}

export function mergeBlock(list: SurfaceBlock[], incoming: SurfaceBlock): SurfaceBlock[] {
  const index = list.findIndex((item) => item.block_id === incoming.block_id);
  if (index < 0) return [...list, incoming];
  const current = list[index];
  const next = incoming.mode === "append"
    ? {
      ...current,
      ...incoming,
      content: `${current.content || ""}${incoming.content || ""}`,
      data: Object.keys(incoming.data || {}).length ? incoming.data : current.data,
    }
    : incoming;
  return list.map((item, itemIndex) => itemIndex === index ? next : item);
}

export function reconcileBlockOrder(current: SurfaceBlock[], canonical: SurfaceBlock[]): SurfaceBlock[] {
  if (!canonical.length) return current;
  const currentById = new Map(current.map((block) => [block.block_id, block]));
  const seen = new Set<string>();
  return canonical.flatMap((reference) => {
    if (seen.has(reference.block_id)) return [];
    seen.add(reference.block_id);
    const streamed = currentById.get(reference.block_id);
    if (!streamed) return [reference];
    return [{
      ...streamed,
      ...reference,
      data: {
        ...asRecord(streamed.data),
        ...asRecord(reference.data),
      },
    }];
  });
}

const ACTIVE_PROCESS_STATUSES = new Set(["running", "loading", "in_progress", "verifying"]);
const PROCESS_STAGE_PREFIX = /^(?:edit_plan|requirement|design|flowchart|coding|test|view|runtime)_(.+)$/;

function logicalProcessId(block: SurfaceBlock): string {
  const explicitId = String(asRecord(block.data).progress_id || "").trim();
  if (explicitId) return explicitId;
  return String(block.block_id || "").match(PROCESS_STAGE_PREFIX)?.[1] || String(block.block_id || "");
}

export function settleProcessBlocks(
  blocks: SurfaceBlock[],
  runStatus: AgentRun["status"],
): SurfaceBlock[] {
  if (runStatus === "running") return blocks;
  const terminalStatus = runStatus === "error" ? "error" : "completed";
  const settled = blocks.map((block) => {
    const data = asRecord(block.data);
    const status = String(data.status || "").trim().toLowerCase();
    if (!ACTIVE_PROCESS_STATUSES.has(status)) return block;
    return { ...block, data: { ...data, status: terminalStatus } };
  });
  const lastByLogicalId = new Map<string, number>();
  settled.forEach((block, index) => lastByLogicalId.set(logicalProcessId(block), index));
  return settled.filter((block, index) => lastByLogicalId.get(logicalProcessId(block)) === index);
}

export function initialRun(summary = "正在连接 Agent", startedAt = Date.now()): AgentRun {
  return { status: "running", summary, artifacts: [], process: [], startedAt };
}

export function applyStreamEvent(run: AgentRun, event: StreamEvent): AgentRun {
  const type = String(event.event || event.event_type || "");
  if (["run_started", "run.started"].includes(type)) {
    return { ...run, runId: String(event.run_id || run.runId || ""), status: "running", summary: String(event.message || "正在处理") };
  }
  if (["block", "block.create", "block.append", "block.patch", "block.complete"].includes(type)) {
    const block = normalizeBlock(event);
    if (isProcessBlock(block)) {
      return {
        ...run,
        summary: run.status === "done"
          ? run.summary
          : block.block_type === "status" && block.content
            ? block.content
            : run.summary,
        process: mergeBlock(run.process, block),
      };
    }
    const data = asRecord(block.data);
    return {
      ...run,
      summary: block.block_type === "workflow" && data.summary ? String(data.summary) : run.summary,
      artifacts: mergeBlock(run.artifacts, block),
    };
  }
  if (["done", "run.finished"].includes(type)) {
    return {
      ...run,
      status: "done",
      summary: "本轮处理完成",
      process: settleProcessBlocks(run.process, "done"),
    };
  }
  if (["error", "stream.error"].includes(type)) {
    const message = String(event.message || "任务失败");
    const failed: AgentRun = {
      ...run,
      status: "error",
      summary: message,
      artifacts: mergeBlock(run.artifacts, {
        block_id: "stream_error",
        block_type: "assessment",
        title: "处理失败",
        content: message,
        data: { overall: "fail", summary: message },
      }),
    };
    return { ...failed, process: settleProcessBlocks(failed.process, "error") };
  }
  return run;
}

function editSummarySurfaceBlocks(editSummary: UnknownRecord): UnknownRecord[] {
  if (!Object.keys(editSummary).length) return [];
  const toolName = String(editSummary.tool_name || "").trim();
  const candidateRevision = Number(editSummary.candidate_revision);
  const verification = asRecord(editSummary.verification);
  const verificationStatus = String(verification.status || "").trim().toLowerCase();
  const verificationPending = ["running", "loading", "pending", "queued", "verifying", "in_progress"].includes(verificationStatus);
  const verificationFailed = ["fail", "failed", "failure", "error", "blocked", "rejected"].includes(verificationStatus);
  const activationDisabled = verificationPending || verificationFailed;
  const activationReason = verificationPending
    ? "候选版本仍在验证，完成前不会切换当前版本。"
    : verificationFailed
      ? "候选版本验证未通过，修正并重新验证后才能启用。"
      : "候选版本尚未生效。确认后才会切换当前启用版本。";
  const blocks: UnknownRecord[] = [{
    block_id: "custom_tool_edit_summary",
    block_type: "artifact",
    title: "工具修改结果",
    stage: "coding",
    data: {
      ...editSummary,
      artifact_type: "finance.custom_tool_edit",
    },
  }];

  if (toolName && Number.isInteger(candidateRevision) && candidateRevision > 0) {
    blocks.push({
      block_id: "custom_tool_edit_activation",
      block_type: "interaction",
      title: "确认候选版本",
      stage: "coding",
      content: activationReason,
      data: {
        interaction_id: "custom_tool.coding_review",
        intent: "confirm",
        submission_mode: "action",
        prompt: activationReason,
        subject_ref: toolName,
        subject_revision: candidateRevision,
        notice: [
          `当前仍使用版本 ${String(editSummary.base_revision ?? "—")}`,
          `待启用版本 ${String(editSummary.candidate_revision ?? "—")}`,
        ],
        actions: [{
          action_id: "custom_tool.activate_draft",
          label: "启用候选版本",
          intent: "accept",
          style: "primary",
          expected_revision: candidateRevision,
          disabled: activationDisabled,
          disabled_reason: activationDisabled ? activationReason : "",
        }],
      },
    });
  }
  return blocks;
}

export function blocksFromPayload(payload: UnknownRecord): SurfaceBlock[] {
  const direct = Array.isArray(payload.surface_blocks)
    ? payload.surface_blocks
    : Array.isArray(payload.render_blocks)
      ? payload.render_blocks
      : [];
  const taskResult = asRecord(payload.task_result);
  const editSummary = Object.keys(asRecord(payload.edit_summary)).length
    ? asRecord(payload.edit_summary)
    : asRecord(taskResult.edit_summary);
  const nestedRenderPayload = asRecord(taskResult.render_payload);
  const renderPayload = payload.render_payload && typeof payload.render_payload === "object"
    ? payload.render_payload as UnknownRecord
    : nestedRenderPayload;
  const sections = Array.isArray(renderPayload.sections) ? renderPayload.sections : [];
  const sectionBlocks = sections.flatMap((section) => {
    const candidate = section && typeof section === "object" ? section as UnknownRecord : {};
    return Array.isArray(candidate.blocks) ? candidate.blocks : [];
  });
  const surface = payload.surface && typeof payload.surface === "object" ? payload.surface as UnknownRecord : {};
  const surfaceSections = Array.isArray(surface.sections) ? surface.sections : [];
  const v1Blocks = surfaceSections.flatMap((section) => {
    const candidate = section && typeof section === "object" ? section as UnknownRecord : {};
    return Array.isArray(candidate.blocks) ? candidate.blocks : [];
  });
  const hasInvocationPreview = direct.some((item) =>
    item && typeof item === "object" && String((item as UnknownRecord).block_id || "") === "asset_invocation_preview"
  );
  const resultBlocks = v1Blocks.length ? v1Blocks : sectionBlocks;
  const selectedWithoutEdit = direct.length
    ? hasInvocationPreview ? [...direct, ...resultBlocks] : direct
    : resultBlocks;
  const editBlocks = editSummarySurfaceBlocks(editSummary).filter((editBlock) => {
    const editId = String(editBlock.block_id || "");
    // Once the backend supplies a Surface, it remains authoritative for which
    // lifecycle actions are actually available. The adapter only synthesizes
    // an activation interaction for legacy payloads that have no Surface.
    if (editId === "custom_tool_edit_activation" && selectedWithoutEdit.length) return false;
    return !selectedWithoutEdit.some((item) => {
      const candidate = asRecord(item);
      const data = asRecord(candidate.data || candidate.payload);
      const actions = Array.isArray(data.actions) ? data.actions.map(asRecord) : [];
      if (editId === "custom_tool_edit_summary") {
        return String(candidate.block_id || candidate.id || "") === editId ||
          String(data.artifact_type || "") === "finance.custom_tool_edit";
      }
      return String(candidate.block_id || candidate.id || "") === editId ||
        actions.some((action) => String(action.action_id || "") === "custom_tool.activate_draft");
    });
  });
  const editArtifacts = editBlocks.filter((block) => String(block.block_id || "") === "custom_tool_edit_summary");
  const editInteractions = editBlocks.filter((block) => String(block.block_id || "") !== "custom_tool_edit_summary");
  const selected = editBlocks.length
    ? [...editArtifacts, ...selectedWithoutEdit, ...editInteractions]
    : selectedWithoutEdit;
  const normalized = selected
    .filter((item): item is UnknownRecord => Boolean(item && typeof item === "object"))
    .map((item, index) => normalizeBlock({
      ...item,
      block_id: item.block_id || item.id || `history_${index}`,
      block_type: item.block_type || item.kind || item.type,
    } as StreamEvent));
  const displayBlocks = normalized.some((block) => block.block_id === "design_artifact")
    ? normalized.filter((block) => block.block_id !== "design_final_summary")
    : normalized;

  const taskState = asRecord(payload.task_state);
  const steps = Array.isArray(taskState.steps) ? taskState.steps : [];
  const processBlock = steps.length && !displayBlocks.some(isProcessBlock)
    ? normalizeBlock({
      event: "block",
      block_id: "runtime_task_progress",
      block_type: "workflow",
      title: "运行过程",
      data: {
        role: "process",
        summary: String(asRecord(taskState.job).result_summary || "本轮任务已完成"),
        items: steps,
      },
    })
    : null;

  if (displayBlocks.length) return processBlock ? [processBlock, ...displayBlocks] : displayBlocks;

  const message = String(payload.message || "").trim();
  const messageBlock = message
    ? normalizeBlock({
      event: "block",
      block_id: "legacy_result_summary",
      block_type: "narrative",
      content: message,
    })
    : null;
  const items = Array.isArray(payload.items)
    ? payload.items.filter((item): item is UnknownRecord => Boolean(item && typeof item === "object"))
    : [];
  if (!items.length) {
    return [
      ...(processBlock ? [processBlock] : []),
      ...(messageBlock ? [messageBlock] : []),
    ];
  }
  const workspace = asRecord(payload.workspace);
  const resources = items.map((item, index) => ({
    resource_id: String(item.catalog_id || item.tool_name || item.skill_name || item.agent_name || item.application_name || item.name || index),
    title: String(item.display_name || item.title || item.tool_name || item.skill_name || item.agent_name || item.application_name || `项目 ${index + 1}`),
    relation: String(item.description || item.status || item.version || ""),
    uri: String(item.workspace_url || workspace.url || ""),
  }));
  const legacyBlocks: SurfaceBlock[] = [];
  if (messageBlock) legacyBlocks.push(messageBlock);
  legacyBlocks.push(normalizeBlock({
    event: "block",
    block_id: "legacy_result_items",
    block_type: "resource",
    title: String(workspace.title || "结果列表"),
    data: { resources },
  }));
  return processBlock ? [processBlock, ...legacyBlocks] : legacyBlocks;
}
