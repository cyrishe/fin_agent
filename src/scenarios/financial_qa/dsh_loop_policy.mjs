import { readFileSync } from 'node:fs'

/**
 * DSH-only loop policy for Fin Agent's bounded financial-data query scenario.
 *
 * The shared MCP tools and their schemas stay unchanged.  This plugin uses
 * DeepSeek Harness' per-agent lifecycle seams to enforce the capability
 * allowed by the current step, cap non-progressing retries, and vary the model
 * budget and business guidance by step without changing shared tool schemas.
 */

export const name = 'fin-agent-finance-loop-policy'
export const inject = ['tools', 'systemPrompt']

const TOOL_SUFFIXES = Object.freeze({
  catalog: 'read_finance_catalog',
  query: 'finance_query',
  details: 'load_finance_result',
})

const DEFAULT_BUDGETS = Object.freeze({
  // Keep reasoning for semantic routing and repair. Once the exact catalog
  // contract is loaded, query construction uses the supported `off` mode;
  // the normal validation/repair lifecycle is unchanged.
  catalog: Object.freeze({ reasoningEffort: 'low', maxTokens: 1536 }),
  query: Object.freeze({ reasoningEffort: 'off', maxTokens: 3072 }),
  // Fast mode has no repair opportunity; retain its previous query budget.
  // This is a budget profile, not an additional lifecycle stage.
  fast_query: Object.freeze({ reasoningEffort: 'low', maxTokens: 3072 }),
  repair: Object.freeze({ reasoningEffort: 'low', maxTokens: 3072 }),
  details: Object.freeze({ reasoningEffort: 'off', maxTokens: 2048 }),
  final: Object.freeze({ reasoningEffort: 'off', maxTokens: 2048 }),
})

const DEFAULT_RESULT_PROJECTION = Object.freeze({
  enabled: true,
  queryMaxRows: 5,
  queryCellMaxChars: 480,
  queryTotalMaxChars: 6000,
  detailMaxRows: 10,
  detailCellMaxChars: 2400,
  detailTotalMaxChars: 16000,
})

const DEFAULT_CONFIG = Object.freeze({
  enabled: true,
  executionMode: 'standard',
  // Keep the three generic tool schemas stable for DeepSeek KV-cache reuse and
  // enforce the active business stage with Harness' monotonic tool guard.  API
  // catalog chapters remain progressively disclosed by the catalog tool.
  preserveRequestPrefix: true,
  maxCatalogAttempts: 6,
  maxQueryAttempts: 3,
  maxQueryRepairs: 1,
  maxLoadAttempts: 2,
  duplicateCallLimit: 1,
  maxRequiredStageSteers: 1,
  businessHint: '',
  budgets: DEFAULT_BUDGETS,
  resultProjection: DEFAULT_RESULT_PROJECTION,
})

const EXECUTION_MODES = new Set(['standard', 'fast'])

const STAGE_PROMPTS = Object.freeze({
  catalog:
    '只做目录定位。具体数据请求必须一次传入明确的 subject + dataview + operation；operation 严格按 read_finance_catalog 参数中的统一规则选择。仅当主体、视图或 operation 确实无法判断时，才读上层目录或完整视图。研报中的观点、布局、竞争格局、催化、技术储备、估值逻辑、机构差异或风险选 stock.report；只有 EPS、收入、归母净利润及增速、PE/PB/ROE 等标准年度预测值选 stock.report_metric；实际披露财务数值才选 financial_3_table。若答案明确需要两个 operation，在同一步并行读取，不要串行试探。不要在本阶段回答数据值。',
  query:
    '目录字段与口径已经就绪。只把用户明确要求的事实和完成其计算不可缺少的依赖合并到一次最小 finance_query flow；不要为了丰富回答额外添加比较目标，也不要拆成多次试探查询。调用前逐条对照已加载 dataview 的 rules 自检每个 request，重点核对默认查询范围、时间口径以及 order/limit 是否真的会取得目标数据；不满足时先修正再调用。若已有结果只完成证券身份解析，必须引用其 rN.code/name 在本次查询实际业务事实。',
  repair:
    '上一查询未成功。这是唯一一次修复机会：只修正工具结果明确指出的失败步骤和口径，不改目标、不换 API 追值，也不要重复完全相同的参数。',
  details:
    '查询已经成功。仅当现有 sample 不足以写出用户要求的答案时调用 load_finance_result 读取最少的必要明细页，并优先只取当前结论需要的列；否则立即输出最终中文答案。不得输出思考过程、工具复盘或再次查询。',
  final:
    '工具阶段已经结束。不要再尝试调用工具；严格根据已有成功结果、空值、零行或错误事实，立即给出简洁且全文中文的最终答案。任何数据口径必须沿用已加载目录和结果中的明确表述，未返回的口径不可自行补写。若现有证据只证明目标字段未提供，只陈述该数据边界、已查范围和不能给出数值的结论；不得搬运结果中与问题无关的数值，也不得推荐本轮未执行的数据来源。零行只表示当前查询条件没有匹配记录；不得用模型记忆补写公司背景、披露习惯、可能原因、替代来源或任何未查询事实。不得输出思考过程、工具复盘或回答草稿。',
})

const FAST_STAGE_PROMPTS = Object.freeze({
  catalog:
    '快速模式：这是唯一一次目录定位。直接选择完成用户取数所需的明确 subject + dataview + operation；若需要多个彼此独立的数据视图，在本阶段并行读取。不要读取上层目录、不要试探、不要回答数据值。',
  query:
    '快速模式：目录已经就绪，这是唯一一次调用生成阶段。直接生成完成用户明确取数目标所需的最小 finance_query；多个独立数据请求可在本阶段并行发出。不要先做身份预查询、不要解释、不要预留后续修复。',
  final:
    '快速模式：工具阶段已经结束。不得检查、重试、翻页或再调用工具；直接根据已有成功结果、空值、零行或错误事实返回简洁且全文中文的答案。目标字段未提供时只陈述数据边界，不引用无关数值或未查询来源；零行时不得用模型记忆补写原因、背景或替代事实。',
})

const REASONING_EFFORTS = new Set(['off', 'low', 'high', 'max'])

function positiveInteger(value, fallback, label) {
  const parsed = Number(value ?? fallback)
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`finance-loop-policy: ${label} must be an integer >= 1`)
  }
  return parsed
}

function budget(raw, fallback, label) {
  const value = raw !== null && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
  const reasoningEffort = String(value.reasoningEffort ?? fallback.reasoningEffort)
  if (!REASONING_EFFORTS.has(reasoningEffort)) {
    throw new Error(`finance-loop-policy: budgets.${label}.reasoningEffort is invalid`)
  }
  return {
    reasoningEffort,
    maxTokens: positiveInteger(value.maxTokens, fallback.maxTokens, `budgets.${label}.maxTokens`),
  }
}

function resultProjection(raw) {
  const value = raw !== null && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
  return {
    enabled: value.enabled === undefined
      ? DEFAULT_RESULT_PROJECTION.enabled
      : value.enabled === true || String(value.enabled).toLowerCase() === 'true',
    queryMaxRows: positiveInteger(value.queryMaxRows, DEFAULT_RESULT_PROJECTION.queryMaxRows, 'resultProjection.queryMaxRows'),
    queryCellMaxChars: positiveInteger(value.queryCellMaxChars, DEFAULT_RESULT_PROJECTION.queryCellMaxChars, 'resultProjection.queryCellMaxChars'),
    queryTotalMaxChars: positiveInteger(value.queryTotalMaxChars, DEFAULT_RESULT_PROJECTION.queryTotalMaxChars, 'resultProjection.queryTotalMaxChars'),
    detailMaxRows: positiveInteger(value.detailMaxRows, DEFAULT_RESULT_PROJECTION.detailMaxRows, 'resultProjection.detailMaxRows'),
    detailCellMaxChars: positiveInteger(value.detailCellMaxChars, DEFAULT_RESULT_PROJECTION.detailCellMaxChars, 'resultProjection.detailCellMaxChars'),
    detailTotalMaxChars: positiveInteger(value.detailTotalMaxChars, DEFAULT_RESULT_PROJECTION.detailTotalMaxChars, 'resultProjection.detailTotalMaxChars'),
  }
}

function executionMode(value, fallback = DEFAULT_CONFIG.executionMode) {
  const normalized = String(value ?? fallback).trim().toLowerCase()
  if (!EXECUTION_MODES.has(normalized)) {
    throw new Error('finance-loop-policy: executionMode must be standard or fast')
  }
  return normalized
}

export function resolveConfig(input = {}) {
  let supplied = input
  if (typeof input.configJson === 'string' && input.configJson.trim()) {
    try {
      supplied = { ...input, ...JSON.parse(input.configJson) }
    } catch (error) {
      throw new Error(`finance-loop-policy: configJson is invalid JSON: ${String(error)}`)
    }
  }
  const rawBudgets = supplied.budgets !== null
    && typeof supplied.budgets === 'object'
    && !Array.isArray(supplied.budgets)
    ? supplied.budgets
    : {}
  return {
    enabled: supplied.enabled === undefined
      ? DEFAULT_CONFIG.enabled
      : supplied.enabled === true || String(supplied.enabled).toLowerCase() === 'true',
    executionMode: executionMode(supplied.executionMode),
    preserveRequestPrefix: supplied.preserveRequestPrefix === undefined
      ? DEFAULT_CONFIG.preserveRequestPrefix
      : supplied.preserveRequestPrefix === true
        || String(supplied.preserveRequestPrefix).toLowerCase() === 'true',
    maxCatalogAttempts: positiveInteger(
      supplied.maxCatalogAttempts,
      DEFAULT_CONFIG.maxCatalogAttempts,
      'maxCatalogAttempts',
    ),
    maxQueryAttempts: positiveInteger(
      supplied.maxQueryAttempts,
      DEFAULT_CONFIG.maxQueryAttempts,
      'maxQueryAttempts',
    ),
    maxQueryRepairs: positiveInteger(
      supplied.maxQueryRepairs,
      DEFAULT_CONFIG.maxQueryRepairs,
      'maxQueryRepairs',
    ),
    maxLoadAttempts: positiveInteger(
      supplied.maxLoadAttempts,
      DEFAULT_CONFIG.maxLoadAttempts,
      'maxLoadAttempts',
    ),
    duplicateCallLimit: positiveInteger(
      supplied.duplicateCallLimit,
      DEFAULT_CONFIG.duplicateCallLimit,
      'duplicateCallLimit',
    ),
    maxRequiredStageSteers: positiveInteger(
      supplied.maxRequiredStageSteers,
      DEFAULT_CONFIG.maxRequiredStageSteers,
      'maxRequiredStageSteers',
    ),
    businessHint: String(supplied.businessHint ?? DEFAULT_CONFIG.businessHint).trim(),
    budgets: Object.fromEntries(
      Object.entries(DEFAULT_BUDGETS).map(([stage, fallback]) => [
        stage,
        budget(rawBudgets[stage] ?? (stage === 'fast_query' ? rawBudgets.query : undefined), fallback, stage),
      ]),
    ),
    resultProjection: resultProjection(supplied.resultProjection),
  }
}

function currentRuntimeContext() {
  const contextPath = String(process.env.FIN_AGENT_DSH_CONTEXT_PATH ?? '').trim()
  if (!contextPath) return {}
  let payload
  try {
    payload = JSON.parse(readFileSync(contextPath, 'utf8'))
  } catch {
    return {}
  }
  return payload !== null && typeof payload === 'object' ? payload : {}
}

function currentToolContext() {
  const payload = currentRuntimeContext()
  const toolContext = payload !== null
    && typeof payload === 'object'
    && payload.tool_context !== null
    && typeof payload.tool_context === 'object'
    ? payload.tool_context
    : {}
  return toolContext
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map(key => [key, stableValue(value[key])]),
    )
  }
  return value
}

function callKey(name, args) {
  return `${name}\n${JSON.stringify(stableValue(args))}`
}

function parseToolResult(event) {
  const outer = event?.data?.message?.content
  const resultBlock = Array.isArray(outer)
    ? outer.find(block => block?.type === 'tool-result')
    : undefined
  const inner = resultBlock?.content
  const texts = Array.isArray(inner)
    ? inner.filter(block => block?.type === 'text').map(block => block.text ?? '')
    : []
  const text = texts.join('')
  let payload
  for (const candidate of texts) {
    try {
      payload = candidate ? JSON.parse(candidate) : undefined
    } catch {
      continue
    }
    if (payload !== undefined) break
  }
  const failed = resultBlock?.isError === true
    || event?.data?.error !== undefined
    || (payload !== null && typeof payload === 'object' && (
      payload.error !== undefined
      || payload.ok === false
      || payload.validation?.ok === false
      || payload.execution?.ok === false
    ))
  return { failed, payload, text }
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value))
}

function projectRows(rows, { maxRows, maxCellChars, totalMaxChars }) {
  if (!Array.isArray(rows)) return { rows, changed: false, shortenedFields: [] }
  // Row count alone is not a context budget. Keep small complete tables,
  // including all selected string fields, without another detail round-trip.
  if (rows.length <= 50 && JSON.stringify(rows).length <= totalMaxChars) {
    return { rows, changed: false, shortenedFields: [] }
  }
  const shortenedFields = new Set()
  const projected = []
  // A visible page is contiguous: head+tail sampling cannot be paged reliably.
  for (const row of rows.slice(0, maxRows)) {
    if (row === null || typeof row !== 'object') {
      if (JSON.stringify([...projected, row]).length > totalMaxChars) break
      projected.push(row)
      continue
    }
    const entries = Array.isArray(row)
      ? row.map((value, index) => [String(index), value])
      : Object.entries(row)
    const next = Array.isArray(row) ? [] : {}
    for (const [key, raw] of entries) {
      let value = raw
      if (typeof raw === 'string') {
        if (raw.length > maxCellChars) {
          value = `${raw.slice(0, maxCellChars)}…`
          shortenedFields.add(key)
        }
      }
      if (Array.isArray(next)) next.push(value)
      else next[key] = value
    }
    // Count keys, numbers and structure too, not just string cell values.
    if (JSON.stringify([...projected, next]).length > totalMaxChars) break
    projected.push(next)
  }
  return { rows: projected, changed: true, shortenedFields: [...shortenedFields] }
}

function projectQueryPayload(payload, config) {
  const projected = cloneJson(payload)
  const items = Array.isArray(projected.steps) ? projected.steps : [projected]
  let remaining = config.queryTotalMaxChars
  for (const item of items) {
    if (item === null || typeof item !== 'object' || Array.isArray(item)) continue
    const sample = item.sample
    if (sample === null || typeof sample !== 'object' || !Array.isArray(sample.rows)) continue
    const result = projectRows(sample.rows, {
      maxRows: config.queryMaxRows,
      maxCellChars: config.queryCellMaxChars,
      totalMaxChars: remaining,
    })
    remaining = Math.max(0, remaining - JSON.stringify(result.rows).length)
    if (!result.changed) continue
    sample.rows = result.rows
    item.sample_complete = false
    if (item.step_evidence && typeof item.step_evidence === 'object') {
      item.step_evidence.sample_complete = false
    }
    item.result_projection = {
      model_rows: result.rows.length,
      source_rows: Number(item.row_count ?? sample.rows.length),
      shortened_fields: result.shortenedFields,
      complete: false,
      guidance: '完整结果仍保存在 result_ref；仅在回答确有需要时按列读取必要明细。',
    }
  }
  return projected
}

function projectDetailPayload(payload, config) {
  const projected = cloneJson(payload)
  if (Array.isArray(projected.rows)) {
    const result = projectRows(projected.rows, {
      maxRows: config.detailMaxRows,
      maxCellChars: config.detailCellMaxChars,
      totalMaxChars: config.detailTotalMaxChars,
    })
    if (result.changed) {
      projected.rows = result.rows
      if (projected.page && typeof projected.page === 'object') {
        const offset = Number(projected.page.offset ?? 0)
        projected.page.returned = result.rows.length
        projected.page.has_more = offset + result.rows.length < Number(projected.page.total ?? 0)
      }
      projected.result_projection = {
        model_rows: result.rows.length,
        shortened_fields: result.shortenedFields,
        complete: false,
        guidance: '这是连续的有界明细；page 描述实际可见行，shortened_fields 中的字段仅为摘要，不代表全文。原始行保存在 result_ref。',
      }
    }
  } else if (typeof projected.text === 'string' && projected.text.length > config.detailTotalMaxChars) {
    projected.text = `${projected.text.slice(0, config.detailTotalMaxChars)}…`
    projected.result_projection = {
      complete: false,
      guidance: '这是面向模型的有界文本；原始内容仍保存在 result_ref。',
    }
  }
  return projected
}

function projectedContent(content, kind, config) {
  if (!Array.isArray(content)) return undefined
  let replaced = false
  const next = content.map(block => {
    if (replaced || block?.type !== 'text') return block
    let payload
    try {
      payload = JSON.parse(String(block.text ?? ''))
    } catch {
      return block
    }
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) return block
    const projected = kind === 'query'
      ? projectQueryPayload(payload, config)
      : projectDetailPayload(payload, config)
    const text = JSON.stringify(projected)
    if (text === String(block.text ?? '')) return block
    replaced = true
    return { ...block, text }
  })
  return replaced ? next : undefined
}

function catalogIsReady(payload) {
  return payload !== null && typeof payload === 'object' && payload.mode === 'dataview'
}

// Derive reuse eligibility from the actual model-visible tool history, not a
// second catalog or a worker-global cache. Compacted-away or old-revision packs
// cannot authorize reuse. This also works when Harness resumes a cold session.
function reusableCatalogApis(agent, tools) {
  const revision = currentRuntimeContext().finance_catalog_revision
  if (!revision) return []
  const catalogCalls = new Set()
  const apis = new Set()
  for (const message of agent.session?.deriveMessages() ?? []) {
    for (const block of message.content ?? []) {
      if (message.role === 'assistant' && block.type === 'tool-call' && block.name === tools.catalog) {
        catalogCalls.add(block.id)
      }
      if (message.source?.kind !== 'tool' || block.type !== 'tool-result'
        || block.isError || !catalogCalls.has(block.toolCallId)) continue
      for (const content of block.content ?? []) {
        if (content.type !== 'text') continue
        let payload
        try { payload = JSON.parse(content.text) } catch { continue }
        if (!catalogIsReady(payload) || payload.catalog_revision !== revision) continue
        for (const fn of payload.dataview?.functions ?? []) {
          if (typeof fn.api_name === 'string') apis.add(fn.api_name)
        }
      }
    }
  }
  return [...apis]
}

function canReuseCatalog(state, args) {
  if (state.stage !== 'catalog' || state.reusableApis.length === 0) return false
  if (typeof args === 'string') {
    try { args = JSON.parse(args) } catch { return false }
  }
  const steps = Array.isArray(args?.steps) ? args.steps : [args]
  return steps.length > 0 && steps.every(step => {
    // Read only the invocation target; the existing Python protocol parser and
    // validator remain authoritative for arguments, fields and method validity.
    const target = /^\s*(?:[A-Za-z_]\w*\s*=\s*)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\(/.exec(step?.request ?? '')?.[1]
    return target && state.reusableApis.some(api => {
      const pattern = api.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        .replace(/<\w+>/g, '[A-Za-z_][A-Za-z_0-9]*')
      return new RegExp(`^${pattern}$`).test(target)
    })
  })
}

function querySucceeded(payload, failed) {
  return !failed && payload !== null && typeof payload === 'object' && payload.ok === true
}

function summaries(payload) {
  if (payload === null || typeof payload !== 'object') return []
  return Array.isArray(payload.steps) ? payload.steps : [payload]
}

function queryNeedsDetails(payload) {
  return summaries(payload).some(item => item !== null
    && typeof item === 'object'
    && item.sample_complete === false
    && typeof item.result_ref === 'string'
    && item.result_ref.length > 0)
}

function queryIsPreparatory(payload) {
  const completed = summaries(payload).filter(
    item => item !== null && typeof item === 'object',
  )
  return completed.length > 0 && completed.every(
    item => typeof item.api === 'string' && item.api.endsWith('.basic_info'),
  )
}

function queryCompletesDataOnly(payload) {
  return payload !== null
    && typeof payload === 'object'
    && payload.data_only_complete === true
}

function queryIsDataOnly(payload) {
  return payload !== null
    && typeof payload === 'object'
    && payload.data_only_mode === true
}

function promptFor(state, config) {
  const prompts = state.executionMode === 'fast' ? FAST_STAGE_PROMPTS : STAGE_PROMPTS
  const base = state.stage === 'catalog' && state.reusableApis.length > 0
    ? '按本轮问题继承或更新对象、指标、时间和输出粒度。本会话仍可见且版本有效的目录可直接复用并调用 finance_query；需要未加载的视图或 operation 时，先用 read_finance_catalog 精确加载。复用的是目录，不是旧查询结果；不要把上一轮日期、标的或条件强加给本轮。'
    : prompts[state.stage] ?? prompts.final
  const marker = `[FINANCE_LOOP stage=${state.stage} reason=${state.reason}]`
  const mode = `[FINANCE_EXECUTION mode=${state.executionMode}]`
  return [marker, mode, base, config.businessHint].filter(Boolean).join('\n')
}

function stageTools(state, tools) {
  switch (state.stage) {
    case 'catalog': return state.reusableApis.length > 0 ? [tools.catalog, tools.query] : [tools.catalog]
    case 'query':
    case 'repair': return [tools.query]
    case 'details': return [tools.details]
    default: return []
  }
}

function stageAllows(state, kind, args) {
  if (state.stage === 'catalog') return kind === 'catalog' || (kind === 'query' && canReuseCatalog(state, args))
  if (state.stage === 'query' || state.stage === 'repair') return kind === 'query'
  if (state.stage === 'details') return kind === 'details'
  return false
}

function resetTurn(state, turn, config, agent, tools) {
  state.turn = turn
  const toolContext = currentToolContext()
  state.executionMode = executionMode(toolContext._finance_execution_mode, config.executionMode)
  state.dataOnlyRequested = toolContext._finance_data_only === true
  state.stage = 'catalog'
  state.reason = 'turn_started'
  // Fast mode's explicit one-catalog/one-query contract remains unchanged.
  state.reusableApis = state.executionMode === 'standard' ? reusableCatalogApis(agent, tools) : []
  state.catalogAttempts = 0
  state.queryAttempts = 0
  state.queryFailures = 0
  state.loadAttempts = 0
  state.dataOnlyComplete = false
  state.requiredAction = true
  state.finalAnswerAttempted = false
  state.stageSteers.clear()
  state.lastInjectedStage = ''
  state.calls.clear()
  state.results.clear()
  state.seenCalls.clear()
}

function updateAfterStep(state, step, config) {
  const stepCalls = [...state.calls.values()].filter(call => call.step === step)
  const calls = stepCalls.filter(call => call.allowedAtCall)
  if (stepCalls.length > 0 && calls.length === 0) {
    state.reason = state.stage === 'final'
      ? 'disallowed_tool_after_completion'
      : 'disallowed_tool_for_stage'
    return
  }
  if (calls.length === 0) return

  const catalog = calls.filter(call => call.kind === 'catalog')
  const queries = calls.filter(call => call.kind === 'query')
  if (catalog.length > 0) {
    const outcomes = catalog.map(call => state.results.get(call.callId)).filter(Boolean)
    const allReady = outcomes.length === catalog.length
      && outcomes.every(outcome => !outcome.failed && catalogIsReady(outcome.payload))
    if (state.executionMode === 'fast') {
      state.stage = allReady ? 'query' : 'final'
      state.reason = allReady ? 'fast_dataview_ready' : 'fast_catalog_failed'
      state.requiredAction = allReady
      return
    }
    if (allReady) {
      state.stage = 'query'
      state.reason = 'dataview_ready'
      state.requiredAction = true
    } else if (state.catalogAttempts < config.maxCatalogAttempts) {
      state.stage = 'catalog'
      state.reason = 'catalog_needs_narrowing'
      state.requiredAction = true
    } else {
      state.stage = 'final'
      state.reason = 'catalog_attempt_limit'
      state.requiredAction = false
    }
    // A resumed turn may fetch a known view while loading another independent
    // view in parallel. Preserve query failures and don't conclude before the
    // newly loaded view has been queried.
    if (!allReady || queries.length === 0) return
  }

  if (queries.length > 0) {
    const outcomes = queries.map(call => state.results.get(call.callId)).filter(Boolean)
    const failures = outcomes.filter(outcome => !querySucceeded(outcome.payload, outcome.failed))
    const success = outcomes.find(outcome => querySucceeded(outcome.payload, outcome.failed))
    if (state.executionMode === 'fast') {
      state.dataOnlyComplete = outcomes.some(
        outcome => querySucceeded(outcome.payload, outcome.failed)
          && (queryCompletesDataOnly(outcome.payload) || queryIsDataOnly(outcome.payload)),
      )
      state.stage = 'final'
      state.reason = failures.length > 0 || success === undefined
        ? 'fast_query_failed'
        : 'fast_query_complete'
      state.requiredAction = false
      return
    }
    // Parallel query calls may contain both useful evidence and one invalid
    // request.  A success must not hide that repairable failure.
    if (failures.length > 0) {
      state.queryFailures += 1
      if (state.queryFailures <= config.maxQueryRepairs
        && state.queryAttempts < config.maxQueryAttempts) {
        state.stage = 'repair'
        state.reason = 'query_repair_allowed'
        state.requiredAction = true
      } else {
        state.stage = 'final'
        state.reason = state.queryAttempts >= config.maxQueryAttempts
          ? 'query_attempt_limit'
          : 'query_repair_limit'
        state.requiredAction = false
      }
    } else if (success !== undefined) {
      if (catalog.length > 0) {
        state.stage = 'query'
        state.reason = 'dataview_ready'
        state.requiredAction = true
      } else if (queryCompletesDataOnly(success.payload)) {
        state.dataOnlyComplete = true
        state.stage = 'final'
        state.reason = 'data_only_complete'
        state.requiredAction = false
      } else if (queryIsPreparatory(success.payload) && state.queryAttempts < config.maxQueryAttempts) {
        state.stage = 'query'
        state.reason = 'identity_scope_ready'
        state.requiredAction = true
      } else if (queryIsDataOnly(success.payload)) {
        state.stage = 'query'
        state.reason = 'data_only_followup_allowed'
        state.requiredAction = true
      } else {
        state.stage = queryNeedsDetails(success.payload) ? 'details' : 'final'
        state.reason = state.stage === 'details'
          ? 'query_success_sample_incomplete'
          : 'query_success_sample_complete'
        state.requiredAction = false
      }
    } else {
      state.stage = 'final'
      state.reason = 'query_result_missing'
      state.requiredAction = false
    }
    return
  }

  const loads = calls.filter(call => call.kind === 'details')
  if (loads.length > 0) {
    if (state.loadAttempts < config.maxLoadAttempts) {
      state.stage = 'details'
      state.reason = 'detail_page_loaded'
      state.requiredAction = false
    } else {
      state.stage = 'final'
      state.reason = 'detail_attempt_limit'
      state.requiredAction = false
    }
    return
  }

  state.stage = 'final'
  state.reason = 'unknown_tool_stopped'
  state.requiredAction = false
}

function requiredActionPrompt(state, tools) {
  if (state.stage === 'catalog') {
    if (state.reusableApis.length > 0) {
      return `本轮尚未取得数据。已有有效目录可直接调用 ${tools.query}；需要新视图或 operation 时先调用 ${tools.catalog}。`
    }
    return `本阶段尚未完成目录路由。请立即调用当前唯一可见的 ${tools.catalog}；具体请求一次提交 subject、dataview、operation，不要输出文字答案。`
  }
  if (state.stage === 'query' || state.stage === 'repair') {
    return `目录已经确定，但本阶段尚未取得数据。请立即调用当前唯一可见的 ${tools.query}；不要在查询前分析或回答。`
  }
  if (state.stage === 'final') return STAGE_PROMPTS.final
  return ''
}

function steeringMessage(text) {
  return {
    id: globalThis.crypto.randomUUID(),
    role: 'user',
    content: [{ type: 'text', text }],
    source: { kind: 'plugin', plugin: name },
  }
}

function toolKind(name, tools) {
  return Object.entries(tools).find(([, toolName]) => toolName === name)?.[0] ?? 'unknown'
}

function concreteCatalogRouteError(args) {
  if (args === null || typeof args !== 'object' || Array.isArray(args)) return undefined
  const subject = String(args.subject ?? '').trim()
  const dataview = String(args.dataview ?? '').trim()
  const operation = String(args.operation ?? '').trim()
  if (dataview && (!subject || !operation)) {
    return '具体金融目录读取必须同时提交 subject、dataview 和 operation；请先按当前路由摘要补全三元组。'
  }
  if (operation && (!subject || !dataview)) {
    return 'operation 只能与明确的 subject 和 dataview 一起提交。'
  }
  return undefined
}

function resolveToolNames(agent) {
  const names = agent.ctx.tools.schemas().map(schema => schema.name)
  const resolved = {}
  for (const [kind, suffix] of Object.entries(TOOL_SUFFIXES)) {
    const matches = names.filter(candidate => candidate === suffix || candidate.endsWith(`__${suffix}`))
    if (matches.length !== 1) {
      throw new Error(
        `finance-loop-policy: expected exactly one visible tool ending in ${suffix}; found ${matches.join(', ') || '(none)'}`,
      )
    }
    resolved[kind] = matches[0]
  }
  return resolved
}

/** Install the financial query loop policy into each live DSH Agent scope. */
export function apply(ctx, input = {}) {
  const config = resolveConfig(input)
  if (!config.enabled) return

  ctx.on('agent/created', ({ agent }) => {
    const tools = resolveToolNames(agent)
    const toolContext = currentToolContext()
    const state = {
      turn: 0,
      executionMode: executionMode(toolContext._finance_execution_mode, config.executionMode),
      dataOnlyRequested: toolContext._finance_data_only === true,
      stage: 'catalog',
      reason: 'agent_created',
      reusableApis: [],
      catalogAttempts: 0,
      queryAttempts: 0,
      queryFailures: 0,
      loadAttempts: 0,
      dataOnlyComplete: false,
      requiredAction: true,
      finalAnswerAttempted: false,
      calls: new Map(),
      results: new Map(),
      seenCalls: new Map(),
      stageSteers: new Map(),
      lastInjectedStage: '',
      visibleKey: '',
      liftRestriction: undefined,
    }

    const applyRestriction = () => {
      if (config.preserveRequestPrefix) return
      const allow = stageTools(state, tools)
      const key = allow.join('\n')
      if (key === state.visibleKey && state.liftRestriction !== undefined) return
      state.liftRestriction?.()
      state.liftRestriction = agent.ctx.tools.restrict({ allow })
      state.visibleKey = key
    }

    if (!config.preserveRequestPrefix) {
      applyRestriction()
      agent.ctx.systemPrompt.section({
        name: 'fin-agent:finance-loop-policy',
        order: 480,
        text: () => promptFor(state, config),
      })
    } else if (config.businessHint) {
      agent.ctx.systemPrompt.section({
        name: 'fin-agent:finance-business-hint',
        order: 480,
        text: config.businessHint,
      })
    }

    agent.ctx.tools.guard(exec => {
      const kind = toolKind(exec.name, tools)
      if (kind === 'unknown') return undefined
      if (!stageAllows(state, kind, exec.arguments)) {
        return `金融查询策略拒绝当前阶段调用 ${exec.name}；请遵循上一工具结果末尾的阶段指引。`
      }
      if (kind === 'catalog') {
        const routeError = concreteCatalogRouteError(exec.arguments)
        if (routeError) return routeError
      }
      const key = callKey(exec.name, exec.arguments)
      const count = state.seenCalls.get(key) ?? 0
      if (count >= config.duplicateCallLimit) {
        return '金融查询策略已阻止完全相同的重复调用；请根据上一结果修正参数或直接回答。'
      }
      state.seenCalls.set(key, count + 1)
      return undefined
    })

    // In fast data-only requests, the successful query result is itself the
    // response.  Harness' native terminal-result marker closes the turn at the
    // tool boundary, so no empty narrative step is proposed merely to reject it.
    agent.ctx.on('tools/execute', async (exec, next) => {
      if (state.executionMode === 'fast' && state.dataOnlyRequested
        && toolKind(exec.name, tools) === 'query') {
        // The marker must be set through ToolRunContext before the body creates
        // its canonical success result.  Adding a field to a wrapper result is
        // intentionally discarded by Harness normalization.
        exec.concludeTurn()
      }
      return next()
    })

    // Keep the durable result_ref and tracker untouched while bounding only
    // the text copied into the next model request.  This is a DSH-native
    // post-execute projection, so Chat/API callers still receive the full rows.
    agent.ctx.on('tools/post-execute', async (exec, result, next) => {
      const decision = await next()
      if (decision.kind !== 'accept'
        || Object.hasOwn(decision, 'value')) return decision
      const kind = toolKind(exec.name, tools)
      if (kind === 'catalog' && !result.isError) {
        const revision = currentRuntimeContext().finance_catalog_revision
        const content = (decision.content ?? result.content ?? []).map(block => {
          if (block.type !== 'text' || !revision) return block
          let payload
          try { payload = JSON.parse(block.text) } catch { return block }
          return catalogIsReady(payload)
            ? { ...block, text: JSON.stringify({ ...payload, catalog_revision: revision }) }
            : block
        })
        return { ...decision, content }
      }
      if (!config.resultProjection.enabled) return decision
      if (kind !== 'query' && kind !== 'details') return decision
      const content = decision.content ?? result.content
      const projected = projectedContent(content, kind, config.resultProjection)
      if (projected === undefined) return decision
      return {
        kind: 'accept',
        content: projected,
        ...(decision.additionalContexts ? { additionalContexts: decision.additionalContexts } : {}),
      }
    })

    agent.ctx.on('agent/pre-step', async ({ turn }, next) => {
      if (state.turn !== turn) {
        resetTurn(state, turn, config, agent, tools)
        applyRestriction()
      }
      if (state.dataOnlyComplete) return { kind: 'reject' }
      const downstream = await next()
      if (downstream.kind !== 'enter' || !config.preserveRequestPrefix) return downstream
      const key = `${state.stage}:${state.reason}`
      if (state.lastInjectedStage === key) return downstream
      state.lastInjectedStage = key
      return {
        ...downstream,
        messages: [
          ...downstream.messages,
          steeringMessage(promptFor(state, config)),
        ],
      }
    })

    agent.ctx.on('agent/request', async (_payload, next) => {
      const proposed = await next()
      const budgetKey = state.executionMode === 'fast' && state.stage === 'query'
        ? 'fast_query' : state.stage
      const selected = config.budgets[budgetKey] ?? config.budgets.final
      return {
        ...proposed,
        reasoningEffort: selected.reasoningEffort,
        // A resumed routing step can now produce a complete query flow. Keep
        // routing reasoning, but don't truncate it at the smaller route budget.
        maxTokens: state.stage === 'catalog' && state.reusableApis.length > 0
          ? Math.max(selected.maxTokens, config.budgets.query.maxTokens)
          : selected.maxTokens,
      }
    })

    // Harness has no tool_choice control.  Its native terminal checkpoint can
    // nevertheless keep a turn alive when an objectively required stage action
    // was omitted.  Bound this per stage/reason so it cannot create a free loop.
    agent.ctx.on('agent/turn-stopping', ({ agent: subject, turn }) => {
      if (turn !== state.turn) return
      if (state.executionMode === 'fast') return
      const needsRequiredAction = state.requiredAction
      const needsFinalAnswer = state.stage === 'final'
        && !state.finalAnswerAttempted
        && !state.dataOnlyComplete
      if (!needsRequiredAction && !needsFinalAnswer) return
      const prompt = requiredActionPrompt(state, tools)
      if (!prompt) return
      const key = `${state.stage}:${state.reason}`
      const count = state.stageSteers.get(key) ?? 0
      if (count >= config.maxRequiredStageSteers) return
      state.stageSteers.set(key, count + 1)
      subject.steer(steeringMessage(prompt))
    })

    agent.ctx.on('session/event', (_session, event) => {
      if (event.type === 'turn/start') {
        resetTurn(state, event.data.turn, config, agent, tools)
        applyRestriction()
        return
      }
      if (event.type === 'tool/call' && event.data.turn === state.turn) {
        const kind = toolKind(event.data.name, tools)
        state.calls.set(event.data.callId, {
          callId: event.data.callId,
          step: event.data.step,
          name: event.data.name,
          kind,
          allowedAtCall: stageAllows(state, kind, event.data.arguments),
        })
        if (stageAllows(state, kind, event.data.arguments)) {
          state.requiredAction = false
          if (kind === 'catalog') state.catalogAttempts += 1
          if (kind === 'query') state.queryAttempts += 1
          if (kind === 'details') state.loadAttempts += 1
        }
        return
      }
      if (event.type === 'tool/result' && event.data.turn === state.turn) {
        state.results.set(event.data.message.source.callId, parseToolResult(event))
        return
      }
      if (event.type === 'step/end' && event.data.turn === state.turn) {
        const hasCalls = [...state.calls.values()].some(call => call.step === event.data.step)
        if (state.stage === 'final' && !hasCalls) state.finalAnswerAttempted = true
        updateAfterStep(state, event.data.step, config)
        applyRestriction()
      }
    })
  })
}
