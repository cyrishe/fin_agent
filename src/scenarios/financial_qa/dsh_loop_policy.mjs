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
  // Semantic routing and DSL construction keep low reasoning.  Once evidence
  // exists, DeepSeek's supported `off` mode reserves the budget for visible
  // detail selection and the final answer instead of another long thought.
  catalog: Object.freeze({ reasoningEffort: 'low', maxTokens: 1536 }),
  query: Object.freeze({ reasoningEffort: 'low', maxTokens: 3072 }),
  repair: Object.freeze({ reasoningEffort: 'low', maxTokens: 3072 }),
  details: Object.freeze({ reasoningEffort: 'off', maxTokens: 2048 }),
  final: Object.freeze({ reasoningEffort: 'off', maxTokens: 2048 }),
})

const DEFAULT_CONFIG = Object.freeze({
  enabled: true,
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
})

const STAGE_PROMPTS = Object.freeze({
  catalog:
    '只做目录定位。具体数据请求必须一次传入明确的 subject + dataview + operation；operation 严格按 read_finance_catalog 参数中的统一规则选择。仅当主体、视图或 operation 确实无法判断时，才读上层目录或完整视图。研报中的观点、布局、竞争格局、催化、技术储备、估值逻辑、机构差异或风险选 stock.report；只有 EPS、收入、归母净利润及增速、PE/PB/ROE 等标准年度预测值选 stock.report_metric；实际披露财务数值才选 financial_3_table。若答案明确需要两个 operation，在同一步并行读取，不要串行试探。不要在本阶段回答数据值。',
  query:
    '目录字段与口径已经就绪。把用户明确要求的事实以及至多一个确有解释价值的比较目标，合并到一次最小 finance_query flow；不要拆成多次试探查询。若已有结果只完成证券身份解析，必须引用其 rN.code/name 在本次查询实际业务事实。',
  repair:
    '上一查询未成功。这是唯一一次修复机会：只修正工具结果明确指出的失败步骤和口径，不改目标、不换 API 追值，也不要重复完全相同的参数。',
  details:
    '查询已经成功。仅当现有 sample 不足以写出用户要求的答案时调用 load_finance_result 读取最少的必要明细页，并优先只取当前结论需要的列；否则立即输出最终中文答案。不得输出思考过程、工具复盘或再次查询。',
  final:
    '工具阶段已经结束。不要再尝试调用工具；严格根据已有成功结果、空值、零行或错误事实，立即给出简洁中文最终答案。不得输出思考过程、工具复盘或回答草稿。',
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
        budget(rawBudgets[stage], fallback, stage),
      ]),
    ),
  }
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

function catalogIsReady(payload) {
  return payload !== null && typeof payload === 'object' && payload.mode === 'dataview'
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
  const base = STAGE_PROMPTS[state.stage] ?? STAGE_PROMPTS.final
  const marker = `[FINANCE_LOOP stage=${state.stage} reason=${state.reason}]`
  return [marker, base, config.businessHint].filter(Boolean).join('\n')
}

function stageTools(state, tools) {
  switch (state.stage) {
    case 'catalog': return [tools.catalog]
    case 'query':
    case 'repair': return [tools.query]
    case 'details': return [tools.details]
    default: return []
  }
}

function stageAllows(state, kind) {
  if (state.stage === 'catalog') return kind === 'catalog'
  if (state.stage === 'query' || state.stage === 'repair') return kind === 'query'
  if (state.stage === 'details') return kind === 'details'
  return false
}

function resetTurn(state, turn) {
  state.turn = turn
  state.stage = 'catalog'
  state.reason = 'turn_started'
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
  if (catalog.length > 0) {
    const outcomes = catalog.map(call => state.results.get(call.callId)).filter(Boolean)
    const allReady = outcomes.length === catalog.length
      && outcomes.every(outcome => !outcome.failed && catalogIsReady(outcome.payload))
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
    return
  }

  const queries = calls.filter(call => call.kind === 'query')
  if (queries.length > 0) {
    const outcomes = queries.map(call => state.results.get(call.callId)).filter(Boolean)
    const failures = outcomes.filter(outcome => !querySucceeded(outcome.payload, outcome.failed))
    const success = outcomes.find(outcome => querySucceeded(outcome.payload, outcome.failed))
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
      if (queryCompletesDataOnly(success.payload)) {
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
    const state = {
      turn: 0,
      stage: 'catalog',
      reason: 'agent_created',
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
      if (config.preserveRequestPrefix && !stageAllows(state, kind)) {
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

    agent.ctx.on('agent/pre-step', async ({ turn }, next) => {
      if (state.turn !== turn) {
        resetTurn(state, turn)
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
      const selected = config.budgets[state.stage] ?? config.budgets.final
      return {
        ...proposed,
        reasoningEffort: selected.reasoningEffort,
        maxTokens: selected.maxTokens,
      }
    })

    // Harness has no tool_choice control.  Its native terminal checkpoint can
    // nevertheless keep a turn alive when an objectively required stage action
    // was omitted.  Bound this per stage/reason so it cannot create a free loop.
    agent.ctx.on('agent/turn-stopping', ({ agent: subject, turn }) => {
      if (turn !== state.turn) return
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
        resetTurn(state, event.data.turn)
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
          allowedAtCall: stageAllows(state, kind),
        })
        if (stageAllows(state, kind)) {
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
