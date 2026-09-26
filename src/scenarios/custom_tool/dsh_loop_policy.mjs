import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/** DSH-only orchestration policy for personalized financial-tool work. */

export const name = 'fin-agent-custom-tool-loop-policy'
export const inject = ['tools']

const TOOL_SUFFIXES = Object.freeze({
  financeQuery: 'finance_query',
  loadResult: 'load_result',
  readAsset: 'read_finance_asset',
  interaction: 'request_user_interaction',
  saveArtifact: 'save_finance_artifact',
  runTool: 'run_dynamic_tool',
})

const DEFAULT_BUDGETS = Object.freeze({
  requirement: Object.freeze({ reasoningEffort: 'low', maxTokens: 4096 }),
  design: Object.freeze({ reasoningEffort: 'low', maxTokens: 6144 }),
  flow: Object.freeze({ reasoningEffort: 'off', maxTokens: 3072 }),
  direct: Object.freeze({ reasoningEffort: 'low', maxTokens: 3072 }),
  final: Object.freeze({ reasoningEffort: 'off', maxTokens: 2048 }),
})

const DEFAULT_CONFIG = Object.freeze({
  enabled: true,
  duplicateCallLimit: 1,
  maxRequiredStageSteers: 1,
  businessHint: '',
  budgets: DEFAULT_BUDGETS,
})

const REASONING_EFFORTS = new Set(['off', 'low', 'high', 'max'])
const STAGE_SKILLS = Object.freeze({
  requirement: 'src/skills/financial-tool-development/skills/financial-tool-requirement/SKILL.md',
  design: 'src/skills/financial-tool-development/skills/financial-tool-design/SKILL.md',
  flow: 'src/skills/financial-tool-development/skills/financial-tool-flowchart/SKILL.md',
})

const STAGE_GUIDANCE = Object.freeze({
  requirement:
    '先根据用户原话和已有资产自我收敛需求。按 Skill 生成 requirement_brief、notice、questions，并调用 save_finance_artifact 保存 requirement。questions 为空时不要停顿；有真正阻断问题时，保存后再调用 request_user_interaction。',
  design:
    '当前需求已经收敛。按 Skill 形成完整设计并调用 save_finance_artifact 保存 design；不要请求用户确认，不要查询具体数据 API。',
  flow:
    '当前设计已经保存。按 Skill 生成 Mermaid，并调用 save_finance_artifact 保存 flow；不要修改设计内容。保存成功后系统将直接交给 Codex 实现和验证。',
  direct:
    '当前会话已有完整工具资产。根据用户本轮意图直接读取资产、运行工具或形成必要修改；查看时不重新生成，运行前只在需要时读取 tool_contract。若用户改变整体目标，从新的 requirement 资产开始。',
  final:
    '本轮需要的系统操作已经完成。不要再调用工具，只用简短中文说明真实结果或下一项必要操作；不得声称未发生的实现、测试或启用。',
})

function positiveInteger(value, fallback, label) {
  const parsed = Number(value ?? fallback)
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`custom-tool-loop-policy: ${label} must be an integer >= 1`)
  }
  return parsed
}

function budget(raw, fallback, label) {
  const value = raw !== null && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
  const reasoningEffort = String(value.reasoningEffort ?? fallback.reasoningEffort)
  if (!REASONING_EFFORTS.has(reasoningEffort)) {
    throw new Error(`custom-tool-loop-policy: budgets.${label}.reasoningEffort is invalid`)
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
      throw new Error(`custom-tool-loop-policy: configJson is invalid JSON: ${String(error)}`)
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

function readContext() {
  const contextPath = String(process.env.FIN_AGENT_DSH_CONTEXT_PATH ?? '').trim()
  if (!contextPath) return {}
  try {
    const payload = JSON.parse(readFileSync(contextPath, 'utf8'))
    return payload !== null && typeof payload === 'object' ? payload : {}
  } catch {
    return {}
  }
}

function currentState() {
  const payload = readContext()
  const toolContext = payload.tool_context !== null && typeof payload.tool_context === 'object'
    ? payload.tool_context
    : {}
  return toolContext.custom_tool_state !== null && typeof toolContext.custom_tool_state === 'object'
    ? toolContext.custom_tool_state
    : {}
}

export function stageFromState(state) {
  const questions = Array.isArray(state.questions) ? state.questions.filter(Boolean) : []
  if (questions.length > 0) return 'requirement'
  const requirement = String(state.requirement_brief ?? state.requirement_text ?? '').trim()
  if (!requirement) return 'requirement'
  const design = state.design_contract !== null && typeof state.design_contract === 'object'
    ? state.design_contract
    : {}
  if (Object.keys(design).length === 0) return 'design'
  if (!String(design.mermaid ?? '').trim()) return 'flow'
  return 'direct'
}

function skillText(stage) {
  const relative = STAGE_SKILLS[stage]
  if (!relative) return ''
  const root = String(process.env.FIN_AGENT_ROOT ?? process.cwd()).trim()
  try {
    return readFileSync(join(root, relative), 'utf8')
  } catch (error) {
    throw new Error(`custom-tool-loop-policy: cannot read ${relative}: ${String(error)}`)
  }
}

function stagePrompt(state, config) {
  const marker = `[CUSTOM_TOOL_LOOP stage=${state.stage} reason=${state.reason}]`
  if (state.reason === 'requirement_questions_saved') {
    return [
      marker,
      '带阻断问题的需求资产已经保存。现在只调用 request_user_interaction 提交这些问题并停止；不要重复保存需求，不要进入设计。',
    ].join('\n\n')
  }
  const skill = skillText(state.stage)
  return [
    marker,
    STAGE_GUIDANCE[state.stage] ?? STAGE_GUIDANCE.final,
    skill ? `[当前阶段的权威 Skill]\n${skill}` : '',
    config.businessHint,
  ].filter(Boolean).join('\n\n')
}

function steeringMessage(text) {
  return {
    id: globalThis.crypto.randomUUID(),
    role: 'user',
    content: [{ type: 'text', text }],
    source: { kind: 'plugin', plugin: name },
  }
}

function resolveToolNames(agent) {
  const names = agent.ctx.tools.schemas().map(schema => schema.name)
  const resolved = {}
  for (const [kind, suffix] of Object.entries(TOOL_SUFFIXES)) {
    const matches = names.filter(candidate => candidate === suffix || candidate.endsWith(`__${suffix}`))
    if (matches.length !== 1) {
      throw new Error(
        `custom-tool-loop-policy: expected exactly one visible tool ending in ${suffix}; found ${matches.join(', ') || '(none)'}`,
      )
    }
    resolved[kind] = matches[0]
  }
  return resolved
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, stableValue(value[key])]))
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
  for (const candidate of texts) {
    try {
      const payload = candidate ? JSON.parse(candidate) : undefined
      if (payload !== undefined) return payload
    } catch {
      continue
    }
  }
  return undefined
}

function toolArguments(value) {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) return value
  if (typeof value !== 'string' || !value.trim()) return {}
  try {
    const parsed = JSON.parse(value)
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

function artifactStage(argumentsValue, resultPayload) {
  if (resultPayload === null || typeof resultPayload !== 'object' || resultPayload.error) return undefined
  const args = toolArguments(argumentsValue)
  const artifactType = String(args.artifact_type ?? resultPayload.artifact_type ?? '')
  const payload = args.payload !== null && typeof args.payload === 'object' ? args.payload : {}
  if (artifactType === 'requirement') {
    const questions = Array.isArray(payload.questions) ? payload.questions : []
    return questions.length > 0
      ? { stage: 'requirement', reason: 'requirement_questions_saved' }
      : { stage: 'design', reason: 'requirement_saved' }
  }
  if (artifactType === 'design') return { stage: 'flow', reason: 'design_saved' }
  if (artifactType === 'flow') return { stage: 'final', reason: 'flow_saved' }
  return undefined
}

function resetTurn(state, turn) {
  state.turn = turn
  state.stage = stageFromState(currentState())
  state.reason = 'turn_started'
  state.lastInjected = ''
  state.finalAnswerAttempted = false
  state.seenCalls.clear()
  state.stageSteers.clear()
  state.calls.clear()
}

function requiredPrompt(state, tools) {
  if (state.stage === 'requirement') {
    return `当前需求尚未形成权威资产。请按当前 Skill 调用 ${tools.saveArtifact} 保存 requirement；只有存在真正阻断问题时才随后调用 ${tools.interaction}。`
  }
  if (state.stage === 'design') {
    return `需求已经收敛。请按当前 Skill 调用 ${tools.saveArtifact} 保存 design，不要请求用户确认。`
  }
  if (state.stage === 'flow') {
    return `设计已经保存。请按当前 Skill 调用 ${tools.saveArtifact} 保存 flow，随后系统会进入 Codex 实现。`
  }
  return ''
}

export function apply(ctx, input = {}) {
  const config = resolveConfig(input)
  if (!config.enabled) return

  ctx.on('agent/created', ({ agent }) => {
    const tools = resolveToolNames(agent)
    const state = {
      turn: 0,
      stage: stageFromState(currentState()),
      reason: 'agent_created',
      lastInjected: '',
      finalAnswerAttempted: false,
      seenCalls: new Map(),
      stageSteers: new Map(),
      calls: new Map(),
    }

    agent.ctx.tools.guard(exec => {
      const key = callKey(exec.name, exec.arguments)
      const count = state.seenCalls.get(key) ?? 0
      if (count >= config.duplicateCallLimit) {
        return '自定义工具编排已阻止完全相同的重复调用；请使用上一结果继续当前阶段。'
      }
      state.seenCalls.set(key, count + 1)
      return undefined
    })

    agent.ctx.on('tools/execute', async (exec, next) => {
      const args = toolArguments(exec.arguments)
      const concludesForInteraction = exec.name === tools.interaction
        && Array.isArray(args.questions)
        && args.questions.length > 0
      const concludesForFlow = exec.name === tools.saveArtifact
        && args.artifact_type === 'flow'
      if (concludesForInteraction || concludesForFlow) exec.concludeTurn()
      return next()
    })

    agent.ctx.on('agent/pre-step', async ({ turn }, next) => {
      if (state.turn !== turn) resetTurn(state, turn)
      const downstream = await next()
      if (downstream.kind !== 'enter') return downstream
      const key = `${state.stage}:${state.reason}`
      if (state.lastInjected === key) return downstream
      state.lastInjected = key
      return {
        ...downstream,
        messages: [...downstream.messages, steeringMessage(stagePrompt(state, config))],
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

    agent.ctx.on('agent/turn-stopping', ({ agent: subject, turn }) => {
      if (turn !== state.turn || !['requirement', 'design', 'flow'].includes(state.stage)) return
      const key = `${state.stage}:${state.reason}`
      const count = state.stageSteers.get(key) ?? 0
      if (count >= config.maxRequiredStageSteers) return
      const prompt = requiredPrompt(state, tools)
      if (!prompt) return
      state.stageSteers.set(key, count + 1)
      subject.steer(steeringMessage(prompt))
    })

    agent.ctx.on('session/event', (_session, event) => {
      if (event.type === 'turn/start') {
        resetTurn(state, event.data.turn)
        return
      }
      if (event.type === 'tool/call' && event.data.turn === state.turn) {
        state.calls.set(event.data.callId, {
          name: event.data.name,
          arguments: event.data.arguments,
        })
        return
      }
      if (event.type === 'tool/result' && event.data.turn === state.turn) {
        const callId = event.data.message?.source?.callId
        const call = state.calls.get(callId)
        if (call?.name !== tools.saveArtifact) return
        const transition = artifactStage(call.arguments, parseToolResult(event))
        if (!transition) return
        state.stage = transition.stage
        state.reason = transition.reason
        state.lastInjected = ''
        return
      }
      if (event.type === 'step/end' && event.data.turn === state.turn) {
        const hasCalls = [...state.calls.values()].length > 0
        if (state.stage === 'final' && !hasCalls) state.finalAnswerAttempted = true
      }
    })
  })
}
