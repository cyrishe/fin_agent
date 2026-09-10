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
  skill: 'read_finance_skill',
  reference: 'read_finance_skill_reference',
  identity: 'resolve_security',
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
  // Accepted for old config files; explicit detail evidence is no longer cropped.
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
  emptyResultEarlyStop: true,
  maxCatalogAttempts: 6,
  maxQueryAttempts: 3,
  maxQueryRepairs: 1, // Legacy configuration accepted; total query attempts bound repairs.
  maxLoadAttempts: 2, // Failed detail reads; successful pages do not spend retries.
  duplicateCallLimit: 1,
  maxRequiredStageSteers: 1,
  businessHint: '',
  budgets: DEFAULT_BUDGETS,
  resultProjection: DEFAULT_RESULT_PROJECTION,
})

const EXECUTION_MODES = new Set(['standard', 'fast'])

const STAGE_PROMPTS = Object.freeze({
  catalog:
    '当前任务是目录定位。根据路由摘要与方法定义，一次提交明确的 subject + dataview + operation；需要的独立目录可并行读取。定位有歧义时读取对应概览。',
  query:
    '按已加载契约构造 finance_query；新增方法先加载对应执行包。将已确定的取数目标与依赖合并成一个最小 flow。',
  repair:
    '上一查询未成功。结合剩余查询额度，依据返回的 recovery 与已加载契约修正失败步骤，保留用户目标和已成功结果。',
  details:
    '按已有结果与本轮目标选择下一步；已保存结果可按 filter、order、columns 读取所需范围。',
  final:
    '依据已有结果与执行证据给出最终答案，可按需继续读取已存明细。数据不足时交代已查范围、实际缺口及尚待确定的结论；零行如实说明当前条件未匹配记录。',
})

const FAST_STAGE_PROMPTS = Object.freeze({
  catalog:
    '快速模式：本阶段提供一次目录定位。根据路由摘要直接提交明确的 subject + dataview + operation，需要多个独立目录时并行读取。',
  query:
    '快速模式：本阶段提供一次查询生成。按已加载契约将取数目标和已知依赖合并为最小 finance_query；独立请求可并行发出，随后依据结果完成。',
  final:
    '快速模式：工具阶段已经结束。依据已有结果与执行证据，用简洁中文直接回答；数据不足时说明已查范围与实际缺口，零行按当前条件未匹配记录说明。',
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
    emptyResultEarlyStop: supplied.emptyResultEarlyStop === undefined
      ? DEFAULT_CONFIG.emptyResultEarlyStop
      : supplied.emptyResultEarlyStop === true
        || String(supplied.emptyResultEarlyStop).toLowerCase() === 'true',
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
    skillMaxCatalogAttempts: positiveInteger(supplied.skillMaxCatalogAttempts, 16, 'skillMaxCatalogAttempts'),
    skillMaxQueryAttempts: positiveInteger(supplied.skillMaxQueryAttempts, 12, 'skillMaxQueryAttempts'),
    skillMaxLoadAttempts: positiveInteger(supplied.skillMaxLoadAttempts, 8, 'skillMaxLoadAttempts'),
    skillAnalysisMaxTokens: positiveInteger(supplied.skillAnalysisMaxTokens, 8192, 'skillAnalysisMaxTokens'),
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

function hasSkillCatalog(context) {
  return Boolean(String(context._finance_skill_catalog_prompt ?? '').trim())
}

function skillGuidedAnswer(state) {
  // Availability permits composition; it does not decide whether a Skill
  // matches the question. Uncovered questions retain generic analysis too.
  return state.skillCatalogAvailable && !state.dataOnlyRequested
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
      // A detail call already selects its columns and page at the tool boundary.
      // Preserve that evidence verbatim: a second crop can skip rows in parallel
      // pages or make the tail of a single cell impossible to retrieve.
      : payload
    const text = JSON.stringify(projected)
    if (text === String(block.text ?? '')) return block
    replaced = true
    return { ...block, text }
  })
  return replaced ? next : undefined
}

function catalogIsReady(payload) {
  return payload !== null && typeof payload === 'object' && payload.mode === 'dataview'
    && payload.dataview?.functions?.some(fn => typeof fn.api_name === 'string' && fn.api_name.length > 0) === true
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

function requestTargets(args) {
  if (typeof args === 'string') {
    try { args = JSON.parse(args) } catch { return [] }
  }
  const steps = Array.isArray(args?.steps) ? args.steps : [args]
  return steps.map(step => {
    // Read only the invocation target; the existing Python protocol parser and
    // validator remain authoritative for arguments, fields and method validity.
    const target = /^\s*(?:[A-Za-z_]\w*\s*=\s*)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\(/.exec(step?.request ?? '')?.[1]
    return target
  }).filter(Boolean)
}

function missingCatalogApis(state, args) {
  return requestTargets(args).filter(target => !state.reusableApis.some(api => {
    const candidate = api.endsWith('.query') && target.split('.').length === 2
      ? `${target}.query` : target
    const pattern = api.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      .replace(/<\w+>/g, '[A-Za-z_][A-Za-z_0-9]*')
    return new RegExp(`^${pattern}$`).test(candidate)
  }))
}

function querySucceeded(payload, failed) {
  return !failed && payload !== null && typeof payload === 'object' && payload.ok === true
}

function summaries(payload) {
  if (payload === null || typeof payload !== 'object') return []
  return Array.isArray(payload.steps) ? payload.steps : [payload]
}

function queryIsPreparatory(payload) {
  // Completion belongs to the question, not the selected API name. A basic
  // information query may itself be the entire user request.
  return payload?.data_request_complete === false
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

function hasDataOnlyResults(state) {
  return state.executionMode === 'standard' && [...state.results.values()].some(
    outcome => querySucceeded(outcome.payload, outcome.failed) && queryIsDataOnly(outcome.payload),
  )
}

function emptyResultHandoff(state, config) {
  if (!config.emptyResultEarlyStop || state.dataOnlyRequested
    || skillGuidedAnswer(state)
    || state.stage !== 'final' || state.requiredAction || state.finalAnswerAttempted) return
  const calls = [...state.calls.values()]
  const queries = calls.filter(call => call.kind === 'query')
  if (queries.length === 0) return
  // No failed/missing call, no partial flow, and no implicit completion. The
  // normal loop still owns routing, repair, dependencies and the final boundary.
  if (calls.some(call => {
    const outcome = state.results.get(call.callId)
    return !call.allowedAtCall || !outcome || outcome.failed
      || (call.kind === 'catalog' && !catalogIsReady(outcome.payload))
      || (call.kind === 'query' && !querySucceeded(outcome.payload, outcome.failed))
  })) return
  const lastStep = Math.max(...queries.map(call => call.step))
  if (queries.filter(call => call.step === lastStep).some(call =>
    state.results.get(call.callId).payload.data_request_complete !== true)) return
  if (queries.some(call => {
    const rows = summaries(state.results.get(call.callId).payload)
    return rows.length === 0 || rows.some(item => item?.row_count !== 0 || !item.result_ref)
  })) return
  let trace
  try {
    trace = JSON.parse(readFileSync(process.env.FIN_AGENT_DSH_TRACE_PATH, 'utf8'))
  } catch { return }
  const context = currentRuntimeContext()
  if (!context.revision || trace.revision !== context.revision
    || trace.finance_catalog_revision !== context.finance_catalog_revision) return
  const message = trace.empty_result_context
  if (message?.role !== 'user' || message.source?.kind !== 'plugin'
    || message.source.plugin !== 'fin-agent:empty-result'
    || typeof message.id !== 'string' || !message.id
    || message.content?.length !== 1 || message.content[0].type !== 'text'
    || typeof message.content[0].text !== 'string' || !message.content[0].text) return
  return message
}

function unsuccessfulDetailReads(state) {
  // Successful pages are progress, not retries. Derive failures from native
  // outcomes so calls issued together are not rejected before any has run.
  return [...state.calls.values()].filter(call => call.kind === 'details'
    && call.allowedAtCall && state.results.get(call.callId)?.failed).length
}

function remainingBudget(state, config) {
  const limits = executionLimits(state, config)
  const fast = state.executionMode === 'fast'
  return {
    catalog: Math.max(0, (fast ? 1 : limits.maxCatalogAttempts) - state.catalogAttempts),
    query: Math.max(0, (fast ? 1 : limits.maxQueryAttempts) - state.queryAttempts),
    details: Math.max(0, (fast ? 0 : limits.maxLoadAttempts) - unsuccessfulDetailReads(state)),
  }
}

function budgetPrompt(state, config) {
  if (state.executionMode === 'fast') {
    return '快速模式提供一次目录定位阶段和一次查询阶段，同阶段独立调用可并行。之后直接依据结果回答，缺失数据如实说明；仅数据模式由系统交付结果。'
  }
  const left = remainingBudget(state, config)
  const limits = executionLimits(state, config)
  return `本轮工具额度（剩余/上限）：目录 ${left.catalog}/${limits.maxCatalogAttempts}，查询 ${left.query}/${limits.maxQueryAttempts}，明细读取失败额度 ${left.details}/${limits.maxLoadAttempts}。成功分页不消耗失败额度，可按需继续读取已存结果。额度不是必须用满的轮数；证据已足够时立即综合回答。根据剩余额度安排必要取证与修复，优先复用结果并合并已知依赖。`
    + (left.query <= 1 ? '\n查询额度接近结束：聚焦最关键的缺口；额度用尽后依据已取得的证据收尾，明确未完成范围与结论限制。缺数据可以给有限结论，不把未验证条件说成已满足。' : '')
}

function promptFor(state, config) {
  if (!state.methodReady) {
    return `[FINANCE_LOOP stage=catalog reason=${state.reason}]\n[FINANCE_EXECUTION mode=${state.executionMode}]\n先按本轮方法选择规则判断：需要专业方法时用 read_finance_skill 加载；直接取数时用 read_finance_catalog 定位数据执行包。依据返回内容继续。\n${budgetPrompt(state, config)}`
  }
  if (state.executionMode === 'standard' && state.stage !== 'final') {
    return `[FINANCE_LOOP stage=${state.stage} reason=${state.reason}]\n[FINANCE_EXECUTION mode=standard]\n`
      + '依据本轮目标与执行结果选择下一步。已确定的查询及依赖合并到 finance_query.steps；新方法先加载执行包。'
      + (state.dataOnlyRequested ? '\n仅数据模式通过 data_request_complete 交付取数完成声明。' : '')
      + `\n${budgetPrompt(state, config)}`
      + (config.businessHint ? `\n${config.businessHint}` : '')
  }
  const prompts = state.executionMode === 'fast' ? FAST_STAGE_PROMPTS : STAGE_PROMPTS
  let base = state.stage === 'catalog' && state.reusableApis.length > 0
    ? '按本轮问题更新对象、指标、时间与结果粒度。仍可见且版本有效的目录可以复用，按本轮条件调用 finance_query；需要新视图或方法时用 read_finance_catalog 加载对应契约。'
    : prompts[state.stage] ?? prompts.final
  if (state.executionMode === 'standard' && state.reason === 'data_request_complete') {
    base = '本轮已声明取数完成，依据已有证据完成回答；需要补充证据时仍可使用已授权工具。'
  }
  if (skillGuidedAnswer(state)) {
    if (state.stage === 'catalog') {
      base += '\n按需读取适用 Skill 与参考，确定取证范围和分析方法。'
    } else if (['query', 'details'].includes(state.stage)) {
      base += '\n结合适用 Skill 与已有证据完成分析；需要新数据时加载对应执行包。'
    } else if (state.stage === 'final') {
      base = state.reason === 'data_request_complete'
        ? '本轮已声明取数完成，依据已有证据完成分析；需要补充证据时仍可使用已授权工具。'
        : '依据当前可用证据完成综合回答，明确尚未取得的数据和结论限制。'
    }
  }
  if (state.dataOnlyRequested || hasDataOnlyResults(state)) {
    if (state.stage === 'query' && hasDataOnlyResults(state)) {
      base = '本轮已有数据集。依赖这些结果的取数目标尚未完成时，读取必要明细、加载对应目录并继续；取数目标完成后结束本轮，由系统交付结果。'
    }
    base += '\n仅数据模式：按 finance_query 的 data_request_complete 定义声明完成，由系统交付结果。'
  }
  const marker = `[FINANCE_LOOP stage=${state.stage} reason=${state.reason}]`
  const mode = `[FINANCE_EXECUTION mode=${state.executionMode}]`
  return [marker, mode, base, budgetPrompt(state, config), config.businessHint].filter(Boolean).join('\n')
}

function stageTools(state, tools) {
  if (!state.methodReady) return [tools.skill, tools.catalog,
    ...(state.executionMode === 'standard' ? [tools.identity] : []),
  ].filter(Boolean)
  const methods = state.stage === 'final' && state.dataOnlyRequested
    ? [] : [tools.skill, tools.reference].filter(Boolean)
  // In standard mode, stages describe progress/budget profiles, not a forced
  // business workflow. The guard still checks each API's visible contract.
  if (state.executionMode === 'standard') {
    if (state.stage === 'final' && (state.reason !== 'data_request_complete' || state.dataOnlyRequested)) {
      // Exhausting provider queries does not revoke access to saved evidence.
      return state.dataOnlyRequested || state.dataOnlyComplete ? methods : [...methods, tools.details].filter(Boolean)
    }
    return [...methods, tools.catalog, tools.query, tools.details, tools.identity].filter(Boolean)
  }
  // Fast mode remains the explicit one-discovery/one-flow product contract.
  const allowed = state.stage === 'catalog' ? [tools.catalog]
    : state.stage === 'query' ? [tools.query] : []
  if (tools.identity && state.stage !== 'final') allowed.push(tools.identity)
  return [...methods, ...allowed]
}

function stageAllows(state, kind) {
  return stageTools(state, TOOL_SUFFIXES).includes(TOOL_SUFFIXES[kind])
}

function usesSkillAnalysis(state) {
  if (state.executionMode !== 'standard' || state.dataOnlyRequested) return false
  if (String(currentToolContext()._finance_explicit_skill_prompt ?? '').trim()) return true
  return [...state.calls.values()].some(call => {
    if (call.kind !== 'skill' || !call.allowedAtCall) return false
    const result = state.results.get(call.callId)
    if (!result || result.failed || result.payload?.error) return false
    const payload = result.payload
    return Boolean(typeof payload?.method === 'string' && payload.method.trim())
      || Boolean(payload?.skills?.some?.(skill => typeof skill.method === 'string' && skill.method.trim()))
  })
}

function executionLimits(state, config) {
  if (!usesSkillAnalysis(state)) return config
  return {
    ...config,
    maxCatalogAttempts: config.skillMaxCatalogAttempts,
    maxQueryAttempts: config.skillMaxQueryAttempts,
    maxLoadAttempts: config.skillMaxLoadAttempts,
  }
}

function resetTurn(state, turn, config, agent, tools) {
  state.turn = turn
  const toolContext = currentToolContext()
  state.executionMode = executionMode(toolContext._finance_execution_mode, config.executionMode)
  state.dataOnlyRequested = toolContext._finance_data_only === true
  state.skillCatalogAvailable = hasSkillCatalog(toolContext)
  state.methodReady = !state.skillCatalogAvailable
    || Boolean(String(toolContext._finance_explicit_skill_prompt ?? '').trim())
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
  config = executionLimits(state, config)
  const stepCalls = [...state.calls.values()].filter(call => call.step === step)
  const calls = stepCalls.filter(call => call.allowedAtCall)
  const methodLoaded = calls.some(call => {
    if (call.kind !== 'skill') return false
    const result = state.results.get(call.callId)
    const payload = result?.payload
    return !result?.failed && !payload?.error
      && (typeof payload?.method === 'string' || Array.isArray(payload?.skills))
  })
  // Choosing data discovery is itself the generic path. No empty Skill call
  // is required before it; normal catalog readiness still governs execution.
  if (methodLoaded || calls.some(call => call.kind === 'catalog' && state.results.has(call.callId))) state.methodReady = true
  const missing = stepCalls.flatMap(call => call.missingApis ?? [])
  if (missing.length > 0) {
    state.stage = state.stage === 'repair' ? 'repair' : 'catalog'
    state.reason = 'method_contract_needed'
    state.requiredAction = true
    // Missing context is a catalog action, not a failed provider query.
    // Process any successful catalog reads in this step below.
  }
  if (stepCalls.length > 0 && calls.length === 0) {
    if (missing.length === 0) state.reason = state.stage === 'final'
      ? 'disallowed_tool_after_completion'
      : 'disallowed_tool_for_stage'
    return
  }
  if (calls.length === 0) return

  const catalog = calls.filter(call => call.kind === 'catalog')
  const queries = calls.filter(call => call.kind === 'query')
  if (catalog.length > 0) {
    const repairing = state.stage === 'repair'
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
      state.stage = repairing ? 'repair' : 'query'
      state.reason = 'dataview_ready'
      state.requiredAction = true
    } else if (state.catalogAttempts < config.maxCatalogAttempts) {
      state.stage = repairing ? 'repair' : 'catalog'
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
      // Bound total work, not the number of unrelated mistakes across a task.
      if (state.queryAttempts < config.maxQueryAttempts) {
        state.stage = 'repair'
        state.reason = 'query_repair_allowed'
        state.requiredAction = true
      } else {
        state.stage = 'final'
        state.reason = 'query_attempt_limit'
        state.requiredAction = false
      }
    } else if (missing.length > 0) {
      state.stage = 'catalog'
      state.reason = 'method_contract_needed'
      state.requiredAction = true
    } else if (success !== undefined) {
      if (catalog.length > 0) {
        state.stage = 'query'
        state.reason = 'dataview_ready'
        state.requiredAction = true
      } else if (outcomes.length === queries.length && outcomes.every(outcome => queryCompletesDataOnly(outcome.payload))) {
        state.dataOnlyComplete = true
        state.stage = 'final'
        state.reason = 'data_only_complete'
        state.requiredAction = false
      } else if (queryIsPreparatory(success.payload) && state.queryAttempts < config.maxQueryAttempts) {
        state.stage = 'query'
        state.reason = 'identity_scope_ready'
        state.requiredAction = true
      } else if (queryIsDataOnly(success.payload)) {
        const exhausted = state.queryAttempts >= config.maxQueryAttempts
        state.stage = exhausted ? 'final' : 'query'
        state.reason = exhausted ? 'query_attempt_limit' : 'data_only_followup_allowed'
        // A successful result is not an omitted query. Do not force another
        // DB call merely because the model is done or declines more analysis.
        state.requiredAction = false
      } else {
        const complete = outcomes.length === queries.length
          && outcomes.every(outcome => outcome.payload?.data_request_complete === true)
        const exhausted = state.queryAttempts >= config.maxQueryAttempts
        state.stage = complete || exhausted ? 'final' : 'query'
        state.reason = complete ? 'data_request_complete'
          : exhausted ? 'query_attempt_limit' : 'query_succeeded'
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
    if (state.executionMode === 'standard') {
      // Reading a page changes visible evidence, not task completion. In
      // particular it cannot erase an earlier explicit unfinished flow.
      return
    }
    if (hasDataOnlyResults(state)) {
      state.stage = 'query'
      state.reason = 'data_only_followup_allowed'
      state.requiredAction = false
    } else if (state.loadAttempts < config.maxLoadAttempts) {
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

  if (calls.every(call => ['skill', 'reference', 'identity'].includes(call.kind))) {
    // Method reads do not consume query attempts or complete the data stage.
    // A method can also answer a supplied-evidence question without a DB call.
    if (!state.dataOnlyRequested) state.requiredAction = false
    return
  }

  state.stage = 'final'
  state.reason = 'unknown_tool_stopped'
  state.requiredAction = false
}

// The latest query batch owns the remaining execution facts. Deliver these
// before the next owed model step, instead of asking it to rewrite a finished
// answer. Real execution failures retain the terminal repair checkpoint.
function completionFacts(state) {
  const queries = [...state.calls.values()].filter(call => call.kind === 'query' && call.allowedAtCall)
  const lastStep = Math.max(0, ...queries.map(call => call.step))
  let failed = false
  const unfinished = queries.filter(call => call.step === lastStep).flatMap(call => {
    const outcome = state.results.get(call.callId)
    if (!outcome) return []
    const succeeded = querySucceeded(outcome.payload, outcome.failed)
    if (succeeded && outcome.payload?.data_request_complete !== false) return []
    failed ||= !succeeded
    return [{
      completed_steps: (outcome.payload?.completed_steps ?? summaries(outcome.payload)).filter(item => item?.result_ref).map(item => item.result_ref),
      failed_step: outcome.payload?.failed_step,
      data_request_complete: outcome.payload?.data_request_complete,
      remaining: outcome.payload?.failed_step
        ? (call.steps ?? []).slice(outcome.payload.failed_step - 1).map(item => item.goal) : undefined,
      error: outcome.payload?.validation?.errors ?? outcome.payload?.error,
    }]
  })
  return { unfinished, failed, key: `completion-facts:${lastStep}` }
}

function completionFactsPrompt(unfinished) {
  return `系统执行事实（内部参考）：${JSON.stringify(unfinished)}。结合原始用户目标和剩余额度继续必要取证，或依据已有证据直接交付完整最终回答。最终回答面向原始金融问题，综合已有结论并说明业务数据缺口；执行记录与协议细节留在过程通道。`
}

function visibleTexts(agent) {
  const texts = []
  const visit = blocks => {
    for (const block of blocks ?? []) {
      if (block.type === 'text') texts.push(block.text)
      if (Array.isArray(block.content)) visit(block.content)
    }
  }
  for (const message of agent.session.deriveMessages()) visit(message.content)
  return texts
}

function reuseVisibleSkill(content, agent) {
  const texts = visibleTexts(agent)
  const reuse = skill => {
    if (typeof skill?.method !== 'string' || !skill.method.trim()
      || !texts.some(text => text.includes(skill.method))) return skill
    const { method, ...identity } = skill
    return { ...identity, already_loaded: true,
      guidance: '该方法正文已完整包含在当前模型上下文中，请直接使用；必要参考仍可按原方法中的路径读取。' }
  }
  return (content ?? []).map(block => {
    if (block.type !== 'text') return block
    let payload
    try { payload = JSON.parse(block.text) } catch { return block }
    if (!payload || payload.error) return block
    const projected = Array.isArray(payload.skills)
      ? { ...payload, skills: payload.skills.map(reuse) } : reuse(payload)
    return { ...block, text: JSON.stringify(projected) }
  })
}

function requiredActionPrompt(state, tools) {
  if (!state.methodReady) return '按本轮目标加载所需专业方法，或直接通过 read_finance_catalog 定位数据执行包。'
  if (hasDataOnlyResults(state)) {
    return '本轮已有数据集。继续完成新加载目录对应的取数目标，或按 recovery 修复失败步骤；由系统交付原始结果。'
  }
  if (state.stage === 'catalog') {
    if (state.reusableApis.length > 0) {
      return `本轮尚未取得数据。已有有效目录可直接调用 ${tools.query}；需要新视图或 operation 时先调用 ${tools.catalog}。`
    }
    return `本阶段需要完成目录路由。调用 ${tools.catalog}，一次提交明确的 subject、dataview、operation。`
  }
  if (state.stage === 'query' || state.stage === 'repair') {
    return `本阶段需要取得数据。按已加载契约调用 ${tools.query}；所需目录可用 ${tools.catalog} 补齐。`
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
  if (dataview && !subject) {
    return '读取 dataview 时需要同时提供 subject。'
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
    if (matches.length === 0 && ['skill', 'reference', 'identity'].includes(kind)) continue
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
      skillCatalogAvailable: hasSkillCatalog(toolContext),
      methodReady: !hasSkillCatalog(toolContext) || Boolean(String(toolContext._finance_explicit_skill_prompt ?? '').trim()),
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
      historyTurn: 0,
      liftRestriction: undefined,
    }

    const applyRestriction = () => {
      if (config.preserveRequestPrefix && state.methodReady) {
        state.liftRestriction?.()
        state.liftRestriction = undefined
        state.visibleKey = ''
        return
      }
      const allow = stageTools(state, tools)
      const key = allow.join('\n')
      if (key === state.visibleKey && state.liftRestriction !== undefined) return
      state.liftRestriction?.()
      state.liftRestriction = agent.ctx.tools.restrict({ allow })
      state.visibleKey = key
    }

    // DSH's logged, agent-scoped assembly seam changes the model-facing
    // contract without mutating the cached/global MCP tool definitions.
    // Prewarmed workers may alternate between Chat and data-only API calls.
    agent.ctx.on('system-prompt/assemble', async (_assembly, _context, next) => {
      const assembly = await next()
      if (!state.dataOnlyRequested) return assembly
      return {
        ...assembly,
        tools: assembly.tools.map(schema => schema.name === tools.query ? {
          ...schema,
          parameters: {
            ...schema.parameters,
            required: [...new Set([...(schema.parameters.required ?? []), 'data_request_complete'])],
          },
        } : schema),
      }
    })

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
      const limits = executionLimits(state, config)
      const kind = toolKind(exec.name, tools)
      if (kind === 'unknown') return undefined
      if (state.methodReady && kind === 'query' && stageAllows(state, kind, exec.arguments)) {
        const missing = missingCatalogApis(state, exec.arguments)
        if (missing.length) return `当前阶段先用 read_finance_catalog 读取这些方法的执行包：${[...new Set(missing)].join('、')}。包内包含参数和字段；加载后提交原查询流。`
      }
      if (!stageAllows(state, kind, exec.arguments)) {
        if (!state.methodReady) return '本步先读取方法或数据执行包；依据返回内容继续所需工具。'
        return `金融查询策略拒绝当前阶段调用 ${exec.name}；请遵循上一工具结果末尾的阶段指引。`
      }
      // Harness emits tool/call before prepare/guard; these counters already
      // include this attempt. Admit the last budgeted call, reject the next.
      if ((kind === 'catalog' && state.catalogAttempts > limits.maxCatalogAttempts)
        || (kind === 'query' && state.queryAttempts > limits.maxQueryAttempts)
      ) return '本轮该类读取次数已达上限；请使用已有数据与当前可用工具完成剩余目标。'
      if (kind === 'details' && unsuccessfulDetailReads(state) >= limits.maxLoadAttempts) {
        return '本轮明细读取失败次数已达上限；请依据已取得的证据回答，明确尚未取得的内容。'
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

    // Native post-execute projection bounds initial query previews and compacts
    // JSON. Explicit detail pages and the canonical value stay lossless.
    agent.ctx.on('tools/post-execute', async (exec, result, next) => {
      const decision = await next()
      if (decision.kind !== 'accept'
        || Object.hasOwn(decision, 'value')) return decision
      const kind = toolKind(exec.name, tools)
      if (kind === 'skill' && !result.isError) {
        return { ...decision, content: reuseVisibleSkill(decision.content ?? result.content, agent) }
      }
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
      let historyChanged = false
      if (state.historyTurn !== turn) {
        state.historyTurn = turn
        // Native, replayable request selection leaves the session archive and
        // tool/result ownership intact. Older Harness builds keep full history.
        if (typeof agent.session.requestHistory === 'function') {
          const fromTurn = currentToolContext()._finance_history_independent === true ? turn : undefined
          if (agent.session.requestHistory().fromTurn !== fromTurn) {
            agent.session.append('request/history', fromTurn === undefined ? {} : { fromTurn })
            historyChanged = true
          }
        }
      }
      // Snapshot only contracts already visible before this model generation.
      // A parallel catalog result cannot authorize a call generated beside it.
      state.reusableApis = reusableCatalogApis(agent, tools)
      if (state.dataOnlyComplete || (state.dataOnlyRequested && state.stage === 'final')) return { kind: 'reject' }
      let downstream = await next()
      if (downstream.kind !== 'enter') return downstream
      if (historyChanged) downstream = { ...downstream, startsRequestSeries: true }
      if (downstream.messages.length === 0) {
        const message = emptyResultHandoff(state, config)
        if (message) {
          // Keep continuation history faithful without pretending this fixed
          // response was model-generated. Native pre-step rejection spends no
          // LLM call; the host recognizes only this logged, exact handoff.
          agent.session.append('user/message', message, { surfaceOp: 'append' })
          state.finalAnswerAttempted = true
          return { kind: 'reject' }
        }
      }
      const facts = completionFacts(state)
      if (state.executionMode === 'standard' && !state.dataOnlyRequested
        && !facts.failed && state.stage !== 'final'
        && facts.unfinished.length > 0 && !state.stageSteers.has(facts.key)) {
        state.stageSteers.set(facts.key, 1)
        downstream = { ...downstream, messages: [
          ...downstream.messages, steeringMessage(completionFactsPrompt(facts.unfinished)),
        ] }
      }
      if (!config.preserveRequestPrefix) return downstream
      const key = `${state.stage}:${state.reason}:${JSON.stringify(remainingBudget(state, config))}`
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
      state.reusableApis = reusableCatalogApis(agent, tools)
      const budgetKey = state.executionMode === 'fast' && state.stage === 'query'
        ? 'fast_query' : state.stage
      const selected = config.budgets[budgetKey] ?? config.budgets.final
      const skillAnalysis = usesSkillAnalysis(state) && !state.requiredAction
      const analyzing = (skillGuidedAnswer(state) || skillAnalysis) && !state.requiredAction
      return {
        ...proposed,
        reasoningEffort: analyzing && selected.reasoningEffort === 'off'
          ? config.budgets.catalog.reasoningEffort : selected.reasoningEffort,
        // A resumed routing step can now produce a complete query flow. Keep
        // routing reasoning, but don't truncate it at the smaller route budget.
        maxTokens: skillAnalysis ? Math.max(selected.maxTokens, config.skillAnalysisMaxTokens)
          : analyzing || (state.stage === 'catalog' && state.reusableApis.length > 0)
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
      const { unfinished, failed, key: factsKey } = completionFacts(state)
      const factsDelivered = !state.dataOnlyRequested && !failed
        && unfinished.length > 0 && state.stageSteers.has(factsKey)
      const exhausted = state.stage === 'final' && state.reason !== 'data_request_complete'
      const needsRequiredAction = !exhausted && (
        unfinished.length > 0 && !factsDelivered
        || state.requiredAction && !(factsDelivered && state.reason === 'identity_scope_ready')
      )
      const needsFinalAnswer = state.stage === 'final'
        && !state.dataOnlyRequested
        && !state.finalAnswerAttempted
        && !state.dataOnlyComplete
      if (!needsRequiredAction && !needsFinalAnswer) return
      const prompt = unfinished.length > 0 && !exhausted
        ? completionFactsPrompt(unfinished)
        : requiredActionPrompt(state, tools)
      if (!prompt) return
      const key = 'turn-completion-audit'
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
        let args = event.data.arguments
        if (typeof args === 'string') {
          try { args = JSON.parse(args) } catch { args = {} }
        }
        const missingApis = kind === 'query' && state.methodReady && stageAllows(state, kind, event.data.arguments)
          ? missingCatalogApis(state, event.data.arguments) : []
        const allowed = stageAllows(state, kind, event.data.arguments) && missingApis.length === 0
        state.calls.set(event.data.callId, {
          callId: event.data.callId,
          step: event.data.step,
          name: event.data.name,
          kind,
          allowedAtCall: allowed,
          missingApis,
          steps: kind === 'query' && Array.isArray(args?.steps) ? args.steps : [],
        })
        if (allowed) {
          if (!state.dataOnlyRequested || !['skill', 'reference'].includes(kind)) state.requiredAction = false
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
