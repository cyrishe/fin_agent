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

export function initialRun(summary = "正在连接 Agent"): AgentRun {
  return { status: "running", summary, artifacts: [], process: [] };
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
        summary: block.block_type === "status" && block.content ? block.content : run.summary,
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
    return { ...run, status: "done", summary: "本轮处理完成" };
  }
  if (["error", "stream.error"].includes(type)) {
    const message = String(event.message || "任务失败");
    return {
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
  }
  return run;
}

export function blocksFromPayload(payload: UnknownRecord): SurfaceBlock[] {
  const direct = Array.isArray(payload.surface_blocks)
    ? payload.surface_blocks
    : Array.isArray(payload.render_blocks)
      ? payload.render_blocks
      : [];
  const taskResult = asRecord(payload.task_result);
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
  const selected = direct.length
    ? hasInvocationPreview ? [...direct, ...resultBlocks] : direct
    : resultBlocks;
  const normalized = selected
    .filter((item): item is UnknownRecord => Boolean(item && typeof item === "object"))
    .map((item, index) => normalizeBlock({
      ...item,
      block_id: item.block_id || item.id || `history_${index}`,
      block_type: item.block_type || item.kind || item.type,
    } as StreamEvent));

  const taskState = asRecord(payload.task_state);
  const steps = Array.isArray(taskState.steps) ? taskState.steps : [];
  const processBlock = steps.length && !normalized.some(isProcessBlock)
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

  if (normalized.length) return processBlock ? [processBlock, ...normalized] : normalized;

  const items = Array.isArray(payload.items)
    ? payload.items.filter((item): item is UnknownRecord => Boolean(item && typeof item === "object"))
    : [];
  if (!items.length) return processBlock ? [processBlock] : [];
  const message = String(payload.message || "").trim();
  const workspace = asRecord(payload.workspace);
  const resources = items.map((item, index) => ({
    resource_id: String(item.tool_name || item.skill_name || item.agent_name || item.application_name || item.name || index),
    title: String(item.display_name || item.title || item.tool_name || item.skill_name || item.agent_name || item.application_name || `项目 ${index + 1}`),
    relation: String(item.description || item.status || item.version || ""),
    uri: String(item.workspace_url || workspace.url || ""),
  }));
  const legacyBlocks: SurfaceBlock[] = [];
  if (message) legacyBlocks.push(normalizeBlock({
    event: "block",
    block_id: "legacy_result_summary",
    block_type: "narrative",
    content: message,
  }));
  legacyBlocks.push(normalizeBlock({
    event: "block",
    block_id: "legacy_result_items",
    block_type: "resource",
    title: String(workspace.title || "结果列表"),
    data: { resources },
  }));
  return processBlock ? [processBlock, ...legacyBlocks] : legacyBlocks;
}
