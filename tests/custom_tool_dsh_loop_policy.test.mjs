import assert from 'node:assert/strict'
import test from 'node:test'

import {
  apply,
  resolveConfig,
  stageFromState,
} from '../src/scenarios/custom_tool/dsh_loop_policy.mjs'

const NAMES = {
  financeQuery: 'mcp__finance__finance_query',
  loadResult: 'mcp__finance__load_result',
  readAsset: 'mcp__finance__read_finance_asset',
  interaction: 'mcp__finance__request_user_interaction',
  saveArtifact: 'mcp__finance__save_finance_artifact',
  runTool: 'mcp__finance__run_dynamic_tool',
}

function fixture(config = {}) {
  const globalListeners = new Map()
  const agentListeners = new Map()
  const steered = []
  let guard
  const tools = {
    schemas: () => Object.values(NAMES).map(name => ({ name })),
    guard: value => { guard = value },
  }
  const agent = {
    steer: message => { steered.push(message) },
    ctx: {
      tools,
      on: (event, listener) => { agentListeners.set(event, listener) },
    },
  }
  const ctx = {
    on: (event, listener) => { globalListeners.set(event, listener) },
  }
  apply(ctx, config)
  globalListeners.get('agent/created')({ agent })
  return {
    guard: exec => guard(exec),
    event: event => agentListeners.get('session/event')({}, event),
    request: base => agentListeners.get('agent/request')({}, async () => base),
    preStep: ({ turn = 1, step = 1 } = {}) => agentListeners.get('agent/pre-step')(
      { turn, step },
      async () => ({ kind: 'enter', messages: [] }),
    ),
    execute: async exec => {
      let concluded = false
      await agentListeners.get('tools/execute')(
        { ...exec, concludeTurn: () => { concluded = true } },
        async () => ({ isError: false, content: [] }),
      )
      return concluded
    },
    stopping: (turn = 1) => agentListeners.get('agent/turn-stopping')({ agent, turn }),
    steered,
  }
}

test('derives only the semantic stage needed by the current assets', () => {
  assert.equal(stageFromState({}), 'requirement')
  assert.equal(stageFromState({ requirement_brief: '目标', questions: [] }), 'design')
  assert.equal(
    stageFromState({ requirement_brief: '目标', questions: [{ question: '口径?' }] }),
    'requirement',
  )
  assert.equal(
    stageFromState({ requirement_brief: '目标', design_contract: { document: '逻辑' } }),
    'flow',
  )
  assert.equal(
    stageFromState({
      requirement_brief: '目标',
      design_contract: { document: '逻辑', mermaid: 'flowchart TD' },
    }),
    'direct',
  )
})

test('uses optimizable stage budgets and validates unsafe values', () => {
  const config = resolveConfig({
    budgets: { design: { reasoningEffort: 'low', maxTokens: 5000 } },
  })
  assert.equal(config.budgets.requirement.reasoningEffort, 'low')
  assert.equal(config.budgets.design.reasoningEffort, 'low')
  assert.equal(config.budgets.design.maxTokens, 5000)
  assert.equal(config.budgets.final.reasoningEffort, 'off')
  assert.throws(
    () => resolveConfig({ budgets: { flow: { maxTokens: 0 } } }),
    /budgets\.flow\.maxTokens/,
  )
})

test('injects the complete requirement skill and its stage budget', async () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  const decision = await runtime.preStep()
  const prompt = decision.messages[0].content[0].text
  const request = await runtime.request({ temperature: 0 })

  assert.match(prompt, /CUSTOM_TOOL_LOOP stage=requirement/)
  assert.match(prompt, /金融工具需求理解与确认/)
  assert.match(prompt, /只有无法自行判断的核心业务分叉才提问/)
  assert.equal(request.reasoningEffort, 'low')
  assert.equal(request.maxTokens, 4096)
})

test('flow and real interaction are native terminal boundaries', async () => {
  const runtime = fixture()
  assert.equal(
    await runtime.execute({
      name: NAMES.saveArtifact,
      arguments: { artifact_type: 'flow', payload: { mermaid: 'flowchart TD' } },
    }),
    true,
  )
  assert.equal(
    await runtime.execute({
      name: NAMES.interaction,
      arguments: { questions: [] },
    }),
    false,
  )
  assert.equal(
    await runtime.execute({
      name: NAMES.interaction,
      arguments: JSON.stringify({ questions: [{ question: '口径?' }] }),
    }),
    true,
  )
})

test('duplicate calls are bounded without a business validator', () => {
  const runtime = fixture()
  const exec = {
    name: NAMES.readAsset,
    arguments: { asset_type: 'design' },
  }
  assert.equal(runtime.guard(exec), undefined)
  assert.match(runtime.guard(exec), /重复调用/)
})
