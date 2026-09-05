import assert from 'node:assert/strict'
import test from 'node:test'

import {
  apply,
  resolveConfig,
} from '../src/scenarios/financial_qa/dsh_loop_policy.mjs'

const NAMES = {
  catalog: 'mcp__finance__read_finance_catalog',
  query: 'mcp__finance__finance_query',
  details: 'mcp__finance__load_finance_result',
}

function resultEvent({ turn = 1, step, callId, payload, isError = false }) {
  return {
    type: 'tool/result',
    data: {
      turn,
      step,
      message: {
        source: { kind: 'tool', callId },
        content: [{
          type: 'tool-result',
          isError,
          content: [
            { type: 'text', text: JSON.stringify(payload) },
            { type: 'text', text: '\n[金融循环策略] next stage' },
          ],
        }],
      },
    },
  }
}

function fixture(config = {}) {
  const globalListeners = new Map()
  const agentListeners = new Map()
  const restrictions = []
  let guard
  let prompt
  const steered = []

  const tools = {
    schemas: () => Object.values(NAMES).map(name => ({ name })),
    restrict: ({ allow }) => {
      const record = { allow: [...allow], lifted: false }
      restrictions.push(record)
      return () => { record.lifted = true }
    },
    guard: value => { guard = value },
  }
  const agent = {
    steer: message => { steered.push(message) },
    ctx: {
      tools,
      systemPrompt: { section: value => { prompt = value } },
      on: (name, listener) => { agentListeners.set(name, listener) },
    },
  }
  const ctx = {
    on: (name, listener) => { globalListeners.set(name, listener) },
  }
  apply(ctx, { preserveRequestPrefix: false, ...config })
  globalListeners.get('agent/created')({ agent })

  return {
    agent,
    restrictions,
    prompt: () => prompt.text(),
    guard: exec => guard(exec),
    event: event => agentListeners.get('session/event')({}, event),
    request: base => agentListeners.get('agent/request')({}, async () => base),
    preStep: ({ turn = 1, step }) => agentListeners.get('agent/pre-step')(
      { turn, step },
      async () => ({ kind: 'enter', messages: [] }),
    ),
    post: (exec, result) => agentListeners.get('tools/post-execute')(
      exec,
      result,
      async () => ({ kind: 'accept', content: result.content }),
    ),
    stopping: (turn = 1) => agentListeners.get('agent/turn-stopping')({ agent, turn }),
    steered,
  }
}

test('resolves defaults and rejects invalid stage budgets', () => {
  const config = resolveConfig({ configJson: '{"maxQueryAttempts":1}' })
  assert.equal(config.maxQueryAttempts, 1)
  assert.equal(config.preserveRequestPrefix, true)
  assert.equal(config.maxRequiredStageSteers, 1)
  assert.equal(config.budgets.catalog.reasoningEffort, 'low')
  assert.equal(config.budgets.final.reasoningEffort, 'off')
  assert.throws(
    () => resolveConfig({ budgets: { final: { maxTokens: 0 } } }),
    /budgets\.final\.maxTokens/,
  )
})

test('keeps the legacy opt JSON surface backward compatible', () => {
  const legacy = resolveConfig({
    configJson: JSON.stringify({
      enabled: true,
      preserveRequestPrefix: true,
      maxCatalogAttempts: 3,
      maxQueryAttempts: 3,
      maxQueryRepairs: 1,
      maxLoadAttempts: 2,
      duplicateCallLimit: 1,
      businessHint: 'legacy business hint',
      budgets: {
        catalog: { reasoningEffort: 'low', maxTokens: 3072 },
        query: { reasoningEffort: 'low', maxTokens: 3072 },
        repair: { reasoningEffort: 'low', maxTokens: 3072 },
        details: { reasoningEffort: 'low', maxTokens: 3072 },
        final: { reasoningEffort: 'low', maxTokens: 3072 },
      },
    }),
  })

  assert.equal(legacy.maxCatalogAttempts, 3)
  assert.equal(legacy.businessHint, 'legacy business hint')
  assert.equal(legacy.budgets.details.reasoningEffort, 'low')
  assert.equal(legacy.budgets.final.maxTokens, 3072)
  assert.equal(legacy.maxRequiredStageSteers, 1)
})

test('uses one native turn-stopping steer when a required query call is omitted', () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({
    step: 1,
    callId: 'catalog-1',
    payload: { mode: 'dataview', dataview: { name: 'report_metric' } },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })

  runtime.stopping()
  runtime.stopping()

  assert.equal(runtime.steered.length, 1)
  assert.equal(runtime.steered[0].source.plugin, 'fin-agent-finance-loop-policy')
  assert.match(runtime.steered[0].content[0].text, /finance_query/)
})

test('does not steer an optional detail or completed stage', () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({ step: 1, callId: 'catalog-1', payload: { mode: 'dataview' } }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 2, callId: 'query-1', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 2,
    callId: 'query-1',
    payload: { ok: true, result_ref: 'session://r1', sample_complete: false },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })

  runtime.stopping()
  assert.equal(runtime.steered.length, 0)
})

test('steers one final-answer step only when query evidence has no answer step yet', () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({ step: 1, callId: 'catalog-1', payload: { mode: 'dataview' } }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 2, callId: 'query-1', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 2,
    callId: 'query-1',
    payload: { ok: true, result_ref: 'session://r1', sample_complete: true },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })

  runtime.stopping()
  assert.equal(runtime.steered.length, 1)
  assert.match(runtime.steered[0].content[0].text, /最终答案/)

  runtime.event({ type: 'step/end', data: { turn: 1, step: 3 } })
  runtime.stopping()
  assert.equal(runtime.steered.length, 1)
})

test('requires operation for a concrete catalog route while retaining ambiguity reads', () => {
  const runtime = fixture()
  assert.match(
    runtime.guard({
      name: NAMES.catalog,
      arguments: { subject: 'stock', dataview: 'report_metric' },
    }),
    /subject、dataview 和 operation/,
  )
  assert.equal(
    runtime.guard({
      name: NAMES.catalog,
      arguments: { subject: 'stock', dataview: 'report_metric', operation: 'query' },
    }),
    undefined,
  )
  assert.equal(
    runtime.guard({ name: NAMES.catalog, arguments: { subject: 'stock' } }),
    undefined,
  )
})

test('stable-prefix mode keeps all schemas visible, guards the stage, and uses stage budgets', async () => {
  const runtime = fixture({ preserveRequestPrefix: true })
  assert.equal(runtime.restrictions.length, 0)
  assert.match(
    runtime.guard({ name: NAMES.query, arguments: { steps: [] } }),
    /当前阶段/,
  )
  assert.equal(
    runtime.guard({ name: NAMES.catalog, arguments: { subject: 'stock' } }),
    undefined,
  )
  assert.deepEqual(await runtime.request({ provider: 'p', model: 'm' }), {
    provider: 'p',
    model: 'm',
    reasoningEffort: 'low',
    maxTokens: 1536,
  })
  const entered = await runtime.preStep({ step: 1 })
  assert.equal(entered.kind, 'enter')
  assert.match(entered.messages.at(-1).content[0].text, /stage=catalog/)
})

test('narrows catalog to query to final and applies per-stage request budgets', async () => {
  const runtime = fixture()
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog])
  assert.match(runtime.prompt(), /stage=catalog/)

  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({
    step: 1,
    callId: 'catalog-1',
    payload: { mode: 'dataview', dataview: { name: 'quote' } },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.query])
  assert.match(runtime.prompt(), /stage=query/)
  assert.deepEqual(await runtime.request({ provider: 'p', model: 'm' }), {
    provider: 'p',
    model: 'm',
    reasoningEffort: 'low',
    maxTokens: 3072,
  })

  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 2, callId: 'query-1', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 2,
    callId: 'query-1',
    payload: { ok: true, result_ref: 'session://r1', sample_complete: true },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })
  assert.deepEqual(runtime.restrictions.at(-1).allow, [])
  assert.match(runtime.prompt(), /stage=final/)
  assert.equal((await runtime.request({})).reasoningEffort, 'off')
})

test('data-only query completes without a narrative model step', async () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({
    step: 1,
    callId: 'catalog-1',
    payload: { mode: 'dataview', dataview: { name: 'report_metric' } },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 2, callId: 'query-1', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 2,
    callId: 'query-1',
    payload: {
      ok: true,
      api: 'stock.report_metric',
      result_ref: 'session://r1',
      sample_complete: false,
      data_only_complete: true,
    },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })

  assert.deepEqual(runtime.restrictions.at(-1).allow, [])
  assert.match(runtime.prompt(), /reason=data_only_complete/)
  assert.deepEqual(await runtime.preStep({ step: 3 }), { kind: 'reject' })
})

test('data-only intermediate query keeps one follow-up query available', () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({
    step: 1,
    callId: 'catalog-1',
    payload: { mode: 'dataview', dataview: { name: 'constitution' } },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 2, callId: 'query-1', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 2,
    callId: 'query-1',
    payload: {
      ok: true,
      api: 'plate.constitution',
      result_ref: 'session://r1',
      sample_complete: false,
      data_only_mode: true,
      data_only_complete: false,
    },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })

  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.query])
  assert.match(runtime.prompt(), /reason=data_only_followup_allowed/)
})

test('allows one query repair, then stops, and guards exact duplicates', () => {
  const runtime = fixture({ maxQueryAttempts: 2 })
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({
    step: 1,
    callId: 'catalog-1',
    payload: { mode: 'dataview' },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })

  const exec = { name: NAMES.query, arguments: { steps: [{ request: 'x' }] } }
  assert.equal(runtime.guard(exec), undefined)
  assert.match(runtime.guard(exec), /重复调用/)

  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 2, callId: 'query-1', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 2,
    callId: 'query-1',
    payload: { error: 'invalid request' },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.query])
  assert.match(runtime.prompt(), /stage=repair/)

  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 3, callId: 'query-2', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 3,
    callId: 'query-2',
    payload: { validation: { ok: false } },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 3 } })
  assert.deepEqual(runtime.restrictions.at(-1).allow, [])
  assert.match(runtime.prompt(), /reason=query_attempt_limit/)
})

test('a parallel success does not hide a repairable query failure', () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({ step: 1, callId: 'catalog-1', payload: { mode: 'dataview' } }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })

  for (const callId of ['query-failed', 'query-succeeded']) {
    runtime.event({
      type: 'tool/call',
      data: { turn: 1, step: 2, callId, name: NAMES.query },
    })
  }
  runtime.event(resultEvent({
    step: 2,
    callId: 'query-failed',
    payload: { ok: false, validation: { ok: false } },
  }))
  runtime.event(resultEvent({
    step: 2,
    callId: 'query-succeeded',
    payload: { ok: true, result_ref: 'session://r1', sample_complete: false },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })

  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.query])
  assert.match(runtime.prompt(), /stage=repair/)
})

test('a ready catalog route does not hide another incomplete parallel route', () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  for (const callId of ['catalog-ready', 'catalog-incomplete']) {
    runtime.event({
      type: 'tool/call',
      data: { turn: 1, step: 1, callId, name: NAMES.catalog },
    })
  }
  runtime.event(resultEvent({
    step: 1,
    callId: 'catalog-ready',
    payload: { mode: 'dataview', dataview: { name: 'financial_3_table' } },
  }))
  runtime.event(resultEvent({
    step: 1,
    callId: 'catalog-incomplete',
    payload: { subject: 'stock', error: 'operation is required' },
    isError: true,
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })

  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog])
  assert.match(runtime.prompt(), /reason=catalog_needs_narrowing/)
})

test('keeps querying after identity-only preparation and permits at most two detail pages', () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({ step: 1, callId: 'catalog-1', payload: { mode: 'dataview' } }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })

  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 2, callId: 'identity-1', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 2,
    callId: 'identity-1',
    payload: { ok: true, api: 'stock.basic_info', sample_complete: true },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.query])
  assert.match(runtime.prompt(), /reason=identity_scope_ready/)

  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 3, callId: 'facts-1', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 3,
    callId: 'facts-1',
    payload: { ok: true, api: 'stock.report', result_ref: 'session://r2', sample_complete: false },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 3 } })
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.details])

  for (const [step, callId] of [[4, 'load-1'], [5, 'load-2']]) {
    runtime.event({
      type: 'tool/call',
      data: { turn: 1, step, callId, name: NAMES.details },
    })
    runtime.event(resultEvent({ step, callId, payload: { rows: [{}] } }))
    runtime.event({ type: 'step/end', data: { turn: 1, step } })
  }
  assert.deepEqual(runtime.restrictions.at(-2).allow, [NAMES.details])
  assert.deepEqual(runtime.restrictions.at(-1).allow, [])
  assert.match(runtime.prompt(), /reason=detail_attempt_limit/)
})
