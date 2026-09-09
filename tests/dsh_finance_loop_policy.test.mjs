import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  apply,
  resolveConfig,
} from '../src/scenarios/financial_qa/dsh_loop_policy.mjs'

const NAMES = {
  catalog: 'mcp__finance__read_finance_catalog',
  query: 'mcp__finance__finance_query',
  details: 'mcp__finance__load_finance_result',
}

test('sample visibility does not control completion or available evidence tools', () => {
  const observations = []
  for (const sample_complete of [true, false]) {
    const runtime = fixture({ maxQueryAttempts: 8 })
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    completeCall(runtime, 1, 'catalog', NAMES.catalog, { mode: 'dataview' })
    completeCall(runtime, 2, 'query', NAMES.query, { ok: true, sample_complete, result_ref: 'session://rows' })
    runtime.stopping()
    observations.push([runtime.restrictions.at(-1).allow, runtime.prompt(), runtime.steered.length])
  }
  assert.deepEqual(observations[0], observations[1])
  assert.equal(observations[0][2], 0)
})

test('unrelated query failures do not consume a global one-repair allowance', () => {
  const runtime = fixture({ maxQueryAttempts: 8, maxQueryRepairs: 1 })
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  completeCall(runtime, 1, 'catalog', NAMES.catalog, { mode: 'dataview' })
  for (const [step, payload] of [[2, { validation: { ok: false } }], [3, { ok: true }], [4, { validation: { ok: false } }]]) {
    completeCall(runtime, step, `q${step}`, NAMES.query, payload)
  }
  assert.match(runtime.prompt(), /stage=repair/)
  assert.equal(runtime.guard({ name: NAMES.query, arguments: { steps: [] } }), undefined)
})

test('completion audit retains unfinished declaration across detail reads and is bounded per turn', () => {
  const runtime = fixture({ maxQueryAttempts: 8 })
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  completeCall(runtime, 1, 'catalog', NAMES.catalog, { mode: 'dataview' })
  completeCall(runtime, 2, 'query', NAMES.query, { ok: true, data_request_complete: false, result_ref: 'session://r1' }, { steps: [{ goal: '已完成的查询' }] })
  completeCall(runtime, 3, 'page', NAMES.details, { rows: [{ code: 'a' }] })
  runtime.stopping()
  assert.equal(runtime.steered.length, 1)
  assert.match(runtime.steered[0].content[0].text, /data_request_complete.*false/)
  assert.doesNotMatch(runtime.steered[0].content[0].text, /已完成的查询/)
  completeCall(runtime, 4, 'catalog2', NAMES.catalog, { mode: 'dataview' })
  runtime.stopping()
  assert.equal(runtime.steered.length, 1)
})

test('native string call arguments retain failed flow suffix and successful references', () => {
  const runtime = fixture({ maxQueryAttempts: 8 })
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  completeCall(runtime, 1, 'catalog', NAMES.catalog, { mode: 'dataview' })
  completeCall(runtime, 2, 'query', NAMES.query, {
    failed_step: 2, validation: { ok: false, errors: ['invalid field'] },
    completed_steps: [{ result_ref: 'session://r11', row_count: 600 }],
  }, JSON.stringify({ steps: [{ goal: '已完成' }, { goal: '失败步骤' }, { goal: '依赖后续' }] }))
  runtime.stopping()
  const text = runtime.steered[0].content[0].text
  assert.match(text, /session:\/\/r11/)
  assert.match(text, /失败步骤/)
  assert.match(text, /依赖后续/)
  assert.doesNotMatch(text, /已完成/)
})

function resultEvent({ turn = 1, step, callId, payload, isError = false }) {
  // Older stage-only fixtures omitted the method body. Give them a real leaf
  // shape; tests of incomplete catalogs supply their own dataview explicitly.
  if (payload?.mode === 'dataview' && !payload.dataview?.functions) {
    const view = payload.dataview?.name ?? 'basic_info'
    payload = { ...payload, dataview: { ...payload.dataview, name: view,
      functions: [{ api_name: `stock.${view}.query`, operation: 'query' }] } }
  }
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

function fixture(config = {}, history = [], toolNames = NAMES) {
  const globalListeners = new Map()
  const agentListeners = new Map()
  const restrictions = []
  let guard
  let prompt
  const steered = []
  const appended = []

  const tools = {
    schemas: () => Object.values(toolNames).map(name => ({ name })),
    restrict: ({ allow }) => {
      const record = { allow: [...allow], lifted: false }
      restrictions.push(record)
      return () => { record.lifted = true }
    },
    guard: value => { guard = value },
  }
  const agent = {
    session: {
      deriveMessages: () => history,
      append: (type, data, options) => {
        appended.push({ type, data, ...options })
        if (type === 'user/message') history.push(data)
      },
    },
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
    execute: async (exec, result) => {
      let concluded = false
      const output = await agentListeners.get('tools/execute')(
        { ...exec, concludeTurn: () => { concluded = true } },
        async () => result,
      )
      return concluded && output.isError !== true
        ? { ...output, concludesTurn: true }
        : output
    },
    preStep: ({ turn = 1, step, messages = [] }) => agentListeners.get('agent/pre-step')(
      { turn, step },
      async () => ({ kind: 'enter', messages }),
    ),
    post: (exec, result) => agentListeners.get('tools/post-execute')(
      exec,
      result,
      async () => ({ kind: 'accept', content: result.content }),
    ),
    assemble: assembly => agentListeners.get('system-prompt/assemble')(
      assembly, {}, async () => assembly,
    ),
    stopping: (turn = 1) => agentListeners.get('agent/turn-stopping')({ agent, turn }),
    steered,
    appended,
  }
}

function catalogHistory(id, view, functions = [{ api_name: `stock.${view}.query`, operation: 'query' }]) {
  return [
    { role: 'assistant', content: [{ type: 'tool-call', id, name: NAMES.catalog }] },
    { role: 'user', source: { kind: 'tool', callId: id }, content: [{ type: 'tool-result',
      toolCallId: id, content: [{ type: 'text', text: JSON.stringify({
        mode: 'dataview', catalog_revision: 'test-contract', dataview: { name: view, functions },
      }) }] }] },
  ]
}

function loadedBasicInfo(t) {
  const dir = mkdtempSync(join(tmpdir(), 'finance-contract-'))
  const previous = process.env.FIN_AGENT_DSH_CONTEXT_PATH
  process.env.FIN_AGENT_DSH_CONTEXT_PATH = join(dir, 'context.json')
  writeFileSync(process.env.FIN_AGENT_DSH_CONTEXT_PATH, JSON.stringify({ finance_catalog_revision: 'test-contract' }))
  t.after(() => {
    if (previous === undefined) delete process.env.FIN_AGENT_DSH_CONTEXT_PATH
    else process.env.FIN_AGENT_DSH_CONTEXT_PATH = previous
    rmSync(dir, { recursive: true, force: true })
  })
  return catalogHistory('identity-pack', 'basic_info')
}

test('every flow target needs a visible contract; missing packs cost no query repair', async t => {
  loadedBasicInfo(t)
  for (const preserveRequestPrefix of [true, false]) {
    const history = catalogHistory('financial-pack', 'financial_3_table')
    const runtime = fixture({ preserveRequestPrefix, maxQueryAttempts: 1 }, history)
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    completeCall(runtime, 1, 'financial-pack', NAMES.catalog, { mode: 'dataview' })
    await runtime.preStep({ step: 2 })
    const args = { steps: [{ request: 'r1 = stock.basic_info.query(limit=1) -> code' },
      { request: 'r2 = stock.financial_3_table.query(filter="code in r1.code") -> code' }] }
    runtime.event({ type: 'tool/call', data: { turn: 1, step: 2, callId: 'missing', name: NAMES.query, arguments: args } })
    assert.match(runtime.guard({ name: NAMES.query, arguments: args }), /stock.basic_info.query/)
    runtime.event(resultEvent({ step: 2, callId: 'missing', isError: true, payload: { error: 'load contract' } }))
    runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })
    history.push(...catalogHistory('identity-pack', 'basic_info'))
    completeCall(runtime, 3, 'identity-pack', NAMES.catalog, { mode: 'dataview' })
    await runtime.preStep({ step: 4 })
    runtime.event({ type: 'tool/call', data: { turn: 1, step: 4, callId: 'repaired', name: NAMES.query, arguments: args } })
    assert.equal(runtime.guard({ name: NAMES.query, arguments: args }), undefined)
    runtime.event(resultEvent({ step: 4, callId: 'repaired', payload: { ok: true, row_count: 0, sample_complete: true } }))
    runtime.event({ type: 'step/end', data: { turn: 1, step: 4 } })
    assert.match((await runtime.preStep({ step: 5 })).messages.map(m => m.content?.[0]?.text).join(''), preserveRequestPrefix ? /stage=final/ : /^$/)
  }
})

test('parallel catalog results cannot authorize their sibling query generation', async t => {
  const history = loadedBasicInfo(t)
  const runtime = fixture({}, history)
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  await runtime.preStep({ step: 1 })
  const args = { request: 'r1 = stock.report.query() -> title' }
  history.push(...catalogHistory('report-pack', 'report'))
  completeCall(runtime, 1, 'report-pack', NAMES.catalog, { mode: 'dataview' })
  assert.match(runtime.guard({ name: NAMES.query, arguments: args }), /stock.report.query/)
  await runtime.preStep({ step: 2 })
  assert.equal(runtime.guard({ name: NAMES.query, arguments: args }), undefined)
  history.splice(0) // Compacted-away contracts cannot authorize the next step.
  await runtime.preStep({ step: 3 })
  assert.match(runtime.guard({ name: NAMES.query, arguments: { request: 'r2 = stock.report.query() -> title' } }), /执行包/)
})

test('operation omission loads complete methods but another operation is not implied', async t => {
  loadedBasicInfo(t)
  const history = catalogHistory('report-pack', 'report', [{ api_name: 'stock.report.agg', operation: 'aggregate' }])
  const runtime = fixture({}, history)
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  assert.equal(runtime.guard({ name: NAMES.query, arguments: { request: 'r1 = stock.report.agg() -> n' } }), undefined)
  assert.match(runtime.guard({ name: NAMES.query, arguments: { request: 'r1 = stock.report.query() -> title' } }), /执行包/)
  history.push(...catalogHistory('full-pack', 'report'))
  await runtime.preStep({ step: 2 })
  assert.equal(runtime.guard({ name: NAMES.query, arguments: { request: 'r2 = stock.report() -> title' } }), undefined)
})

test('a dataview label without executable methods never opens execution', async t => {
  loadedBasicInfo(t)
  const history = catalogHistory('navigation-only', 'report', [])
  const runtime = fixture({}, history)
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  completeCall(runtime, 1, 'navigation-only', NAMES.catalog, {
    mode: 'dataview', dataview: { name: 'report', functions: [] },
  })
  assert.match(runtime.prompt(), /stage=catalog/)
  await runtime.preStep({ step: 2 })
  assert.match(runtime.guard({ name: NAMES.query, arguments: {
    request: 'r1 = stock.report.query() -> title',
  } }), /stock.report.query/)
})

test('discovery offers methods and data; empty method selection remains compatible', async () => {
  await withSkillContext({}, async () => {
    for (const payload of [{ skills: [] }, { skills: [{ skill_id: 'equity-report-analysis', method: '按证据分析' }] }]) {
      const runtime = fixture({ preserveRequestPrefix: true }, [], SKILL_NAMES)
      runtime.event({ type: 'turn/start', data: { turn: 1 } })
      assert.deepEqual(runtime.restrictions.at(-1).allow, [SKILL_NAMES.skill, NAMES.catalog])
      const opening = await runtime.preStep({ step: 1 })
      assert.match(opening.messages[0].content[0].text, /stage=catalog reason=turn_started/)
      assert.equal(runtime.guard({ name: NAMES.catalog, arguments: {} }), undefined)
      assert.match(runtime.guard({ name: SKILL_NAMES.reference, arguments: {} }), /先读取方法或数据执行包/)
      runtime.event({ type: 'tool/call', data: { turn: 1, step: 1, callId: 's', name: SKILL_NAMES.skill, arguments: { skill_ids: [] } } })
      runtime.event(resultEvent({ step: 1, callId: 's', payload }))
      assert.match(runtime.guard({ name: NAMES.query, arguments: {} }), /先读取方法或数据执行包/)
      runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })
      assert.equal(runtime.restrictions.at(-1).lifted, true)
      assert.equal(runtime.guard({ name: NAMES.catalog, arguments: { subject: 'stock' } }), undefined)
      runtime.stopping()
      assert.equal(runtime.steered.length, 0)
    }
  })
})

test('generic discovery needs no empty Skill handshake and still waits for a leaf contract', async () => {
  await withSkillContext({}, async () => {
    const runtime = fixture({ preserveRequestPrefix: true }, [], SKILL_NAMES)
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    completeCall(runtime, 1, 'overview', NAMES.catalog, { mode: 'subject' })
    assert.equal(runtime.restrictions.at(-1).lifted, true)
    assert.match((await runtime.preStep({ step: 2 })).messages[0].content[0].text, /stage=catalog/)
    assert.match(runtime.guard({ name: NAMES.query, arguments: { request: 'stock.quote.query() -> close' } }), /执行包/)
    // Professional methods remain available after choosing the generic path.
    assert.equal(runtime.guard({ name: SKILL_NAMES.skill, arguments: { skill_id: 'equity-report-analysis' } }), undefined)
  })
})

test('explicitly loaded Skill and identity tool reuse the existing lifecycle', async () => {
  await withSkillContext({ _finance_explicit_skill_prompt: '已授权并加载的研究方法' }, async () => {
    const names = { ...SKILL_NAMES, identity: 'mcp__finance__resolve_security' }
    const runtime = fixture({}, [], names)
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    assert.equal(runtime.guard({ name: names.identity, arguments: { identifiers: ['阳光电源'] } }), undefined)
    completeCall(runtime, 1, 'id', names.identity, { ok: true, items: [] })
    assert.match(runtime.prompt(), /stage=catalog/)
  })
})

test('missing contract cannot reopen an exhausted turn or be hidden by a parallel success', t => {
  const history = loadedBasicInfo(t)
  const known = { request: 'r1 = stock.basic_info.query() -> code' }
  const unknown = { request: 'r2 = stock.report.query() -> title' }
  const runtime = fixture({ maxQueryAttempts: 1 }, history)
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  completeCall(runtime, 1, 'known', NAMES.query, { ok: true, sample_complete: true }, known)
  assert.match(runtime.prompt(), /stage=final/)
  runtime.event({ type: 'tool/call', data: { turn: 1, step: 2, callId: 'unknown', name: NAMES.query, arguments: unknown } })
  assert.match(runtime.guard({ name: NAMES.query, arguments: unknown }), /当前阶段/)
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })
  assert.match(runtime.prompt(), /stage=final/)

  const parallel = fixture({}, history)
  parallel.event({ type: 'turn/start', data: { turn: 1 } })
  for (const [id, args] of [['known', known], ['unknown', unknown]]) {
    parallel.event({ type: 'tool/call', data: { turn: 1, step: 1, callId: id, name: NAMES.query, arguments: args } })
    parallel.event(resultEvent({ step: 1, callId: id, isError: id === 'unknown', payload: { ok: id === 'known', sample_complete: true } }))
  }
  parallel.event({ type: 'step/end', data: { turn: 1, step: 1 } })
  assert.match(parallel.prompt(), /stage=catalog reason=method_contract_needed/)
})

test('stage prompts use catalog routing and preserve the current action boundary', () => {
  const runtime = fixture()
  assert.match(runtime.prompt(), /依据本轮目标与执行结果选择下一步/)
  assert.match(runtime.prompt(), /新方法先加载执行包/)
  assert.doesNotMatch(runtime.prompt(), /stock\.report|financial_3_table|EPS|竞争格局/)

  const fast = fixture({ executionMode: 'fast' })
  assert.match(fast.prompt(), /一次目录定位/)
  assert.doesNotMatch(fast.prompt(), /stock\.report|financial_3_table/)
})

test('empty final response uses the exact host handoff only after explicit complete success', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'finance-empty-final-'))
  const contextPath = join(dir, 'context.json')
  const tracePath = join(dir, 'trace.json')
  const oldContext = process.env.FIN_AGENT_DSH_CONTEXT_PATH
  const oldTrace = process.env.FIN_AGENT_DSH_TRACE_PATH
  process.env.FIN_AGENT_DSH_CONTEXT_PATH = contextPath
  process.env.FIN_AGENT_DSH_TRACE_PATH = tracePath
  const message = {
    id: 'host-answer-1', role: 'user',
    source: { kind: 'plugin', plugin: 'fin-agent:empty-result' },
    content: [{ type: 'text', text: '本次查询已完成。当前条件下返回 0 条记录。' }],
  }
  try {
    for (const scenario of [
      'complete', 'multiple-empty', 'fast', 'non-prefix', 'disabled', 'legacy',
      'unfinished', 'mixed-rows', 'missing-count', 'failed', 'stale-trace',
      'stale-catalog', 'missing-trace', 'pending-input', 'data-only',
    ]) {
      writeFileSync(contextPath, JSON.stringify({
        revision: 'turn-a', finance_catalog_revision: 'catalog-a',
        tool_context: { _finance_data_only: scenario === 'data-only' },
      }))
      writeFileSync(tracePath, JSON.stringify({
        revision: scenario === 'stale-trace' ? 'turn-old' : 'turn-a',
        finance_catalog_revision: scenario === 'stale-catalog' ? 'catalog-old' : 'catalog-a',
        empty_result_context: scenario === 'missing-trace' ? null : message,
      }))
      const history = []
      const runtime = fixture({
        preserveRequestPrefix: scenario !== 'non-prefix',
        executionMode: scenario === 'fast' ? 'fast' : 'standard',
        emptyResultEarlyStop: scenario !== 'disabled',
      }, history)
      const call = (step, id, name, payload) => {
        runtime.event({ type: 'tool/call', data: { turn: 1, step, callId: id, name } })
        runtime.event(resultEvent({ step, callId: id, payload }))
        runtime.event({ type: 'step/end', data: { turn: 1, step } })
      }
      runtime.event({ type: 'turn/start', data: { turn: 1 } })
      call(1, 'catalog', NAMES.catalog, { mode: 'dataview' })
      const row = { row_count: 0, result_ref: 'session://test/vars/v1', sample_complete: true }
      const payload = {
        ok: scenario !== 'failed', ...row,
        ...(scenario === 'legacy' ? {} : { data_request_complete: scenario !== 'unfinished' }),
      }
      if (scenario === 'multiple-empty') payload.steps = [row, { ...row, result_ref: 'r2' }]
      if (scenario === 'mixed-rows') payload.steps = [row, { ...row, row_count: 1 }]
      if (scenario === 'missing-count') delete payload.row_count
      call(2, 'query', NAMES.query, payload)
      const stopped = ['complete', 'multiple-empty', 'fast', 'non-prefix'].includes(scenario)
      const decision = await runtime.preStep({
        step: 3, messages: scenario === 'pending-input' ? [{ role: 'user', content: [] }] : [],
      })
      // Existing data-only termination is preserved, without a narrative.
      assert.equal(decision.kind, stopped || scenario === 'data-only' ? 'reject' : 'enter', scenario)
      assert.equal(runtime.appended.length, stopped ? 1 : 0, scenario)
      if (stopped) {
        assert.deepEqual(runtime.appended[0], { type: 'user/message', data: message, surfaceOp: 'append' })
        assert.deepEqual(history.at(-1), message)
        runtime.stopping()
        assert.equal(runtime.steered.length, 0)
        assert.equal((await runtime.preStep({ step: 3 })).kind, 'enter')
        assert.equal(runtime.appended.length, 1)
        // A following turn does not inherit completion from the prior turn.
        runtime.event({ type: 'turn/start', data: { turn: 2 } })
        assert.equal((await runtime.preStep({ turn: 2, step: 1 })).kind, 'enter')
      }
    }
  } finally {
    if (oldContext === undefined) delete process.env.FIN_AGENT_DSH_CONTEXT_PATH
    else process.env.FIN_AGENT_DSH_CONTEXT_PATH = oldContext
    if (oldTrace === undefined) delete process.env.FIN_AGENT_DSH_TRACE_PATH
    else process.env.FIN_AGENT_DSH_TRACE_PATH = oldTrace
    rmSync(dir, { recursive: true, force: true })
  }
})

test('standard composite flows may complete catalog loading and repair without resetting the retry budget', () => {
  const runtime = fixture({ maxCatalogAttempts: 3 })
  const call = (step, id, name, payload) => {
    runtime.event({ type: 'tool/call', data: { turn: 1, step, callId: id, name } })
    runtime.event(resultEvent({ step, callId: id, payload }))
    runtime.event({ type: 'step/end', data: { turn: 1, step } })
  }
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  call(1, 'first-view', NAMES.catalog, { mode: 'dataview' })
  const catalog = view => ({ name: NAMES.catalog, arguments: { subject: 'plate', dataview: view, operation: 'query' } })
  assert.equal(runtime.guard(catalog('constitution')), undefined)
  call(2, 'dependent-view', NAMES.catalog, { mode: 'dataview' })
  call(3, 'query-failed', NAMES.query, { ok: false })
  assert.match(runtime.prompt(), /stage=repair/)
  assert.equal(runtime.guard(catalog('basic_info')), undefined)
  call(4, 'repair-view', NAMES.catalog, { mode: 'dataview' })
  assert.match(runtime.prompt(), /stage=repair/)
  // The native scheduler publishes this attempted call before running guards.
  runtime.event({ type: 'tool/call', data: { turn: 1, step: 5, callId: 'over-budget-view', name: NAMES.catalog } })
  assert.match(runtime.guard(catalog('quote')), /上限/)
  call(5, 'repair-failed', NAMES.query, { ok: false })
  assert.match(runtime.prompt(), /stage=final/)
  assert.match(runtime.guard(catalog('quote')), /当前阶段/)
})

test('fast mode retains the explicit one-catalog-stage contract', () => {
  const runtime = fixture({ executionMode: 'fast' })
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({ type: 'tool/call', data: { turn: 1, step: 1, callId: 'c1', name: NAMES.catalog } })
  runtime.event(resultEvent({ step: 1, callId: 'c1', payload: { mode: 'dataview' } }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })
  assert.match(runtime.guard({ name: NAMES.catalog, arguments: { subject: 'stock', dataview: 'quote', operation: 'query' } }), /当前阶段/)
})

test('catalog reuse follows visible history, exact operation and revision across resumed turns', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'finance-catalog-reuse-'))
  const contextPath = join(dir, 'context.json')
  const previous = process.env.FIN_AGENT_DSH_CONTEXT_PATH
  process.env.FIN_AGENT_DSH_CONTEXT_PATH = contextPath
  const writeRevision = revision => writeFileSync(contextPath, JSON.stringify({ finance_catalog_revision: revision }))
  try {
    writeRevision('catalog-v1')
    const history = []
    const runtime = fixture({ preserveRequestPrefix: true }, history)
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    const query = { name: NAMES.query, arguments: { steps: [{ request: 'r1 = stock.quote(codes=["600519.SH"],count=1) -> open' }] } }
    assert.match(runtime.guard(query), /执行包/)
    const original = { mode: 'dataview', dataview: { functions: [{ api_name: 'stock.quote.query' }] } }
    const projected = await runtime.post({ name: NAMES.catalog }, {
      content: [{ type: 'text', text: JSON.stringify(original) }], isError: false,
    })
    assert.equal(JSON.parse(projected.content[0].text).catalog_revision, 'catalog-v1')
    assert.equal(original.catalog_revision, undefined)
    history.push(
      { role: 'assistant', content: [{ type: 'tool-call', id: 'c1', name: NAMES.catalog }] },
      { role: 'user', source: { kind: 'tool', callId: 'c1' }, content: [
        { type: 'tool-result', toolCallId: 'c1', content: projected.content, isError: false },
      ] },
    )
    runtime.event({ type: 'turn/start', data: { turn: 2 } })
    assert.equal((await runtime.request({})).maxTokens, 3072)
    assert.equal(runtime.guard(query), undefined)
    assert.equal(runtime.guard({ name: NAMES.query, arguments: { steps: [{
      request: 'r1 = stock.quote.query(codes=["600519.SH"],count=1) -> open',
    }] } }), undefined)
    assert.match((await runtime.preStep({ turn: 2, step: 1 })).messages[0].content[0].text, /finance_query.steps/)
    runtime.event({ type: 'tool/call', data: { turn: 2, step: 1, callId: 'q2', name: NAMES.query, arguments: JSON.stringify(query.arguments) } })
    runtime.event(resultEvent({ turn: 2, step: 1, callId: 'q2', payload: { ok: true, sample_complete: true } }))
    runtime.event({ type: 'step/end', data: { turn: 2, step: 1 } })
    assert.equal((await runtime.request({})).reasoningEffort, 'off')
    assert.match(runtime.guard(query), /重复调用/)
    runtime.event({ type: 'turn/start', data: { turn: 3 } })
    for (const request of [
      'r1 = stock.margin() -> financing_balance',
      'r1 = stock.quote.agg(agg="avg(close)") -> value',
      'r1 = stock.quote.kd_close_max(k=5) -> value',
    ]) {
      assert.match(runtime.guard({ name: NAMES.query, arguments: { request } }), /执行包/)
    }
    assert.match(runtime.guard({ name: NAMES.query, arguments: { steps: [query.arguments.steps[0], { request: 'r2 = stock.margin() -> code' }] } }), /执行包/)
    // A new Agent (cold session resume) derives the same eligibility, including
    // native tool-restriction mode. No in-memory cache is required.
    const cold = fixture({}, history)
    cold.event({ type: 'turn/start', data: { turn: 4 } })
    assert.deepEqual(cold.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
    assert.equal(cold.guard(query), undefined)
    assert.match(cold.guard({ name: NAMES.query, arguments: { request: 'stock.margin() -> code' } }), /执行包/)
    const fast = fixture({ executionMode: 'fast', preserveRequestPrefix: true }, history)
    fast.event({ type: 'turn/start', data: { turn: 4 } })
    assert.match(fast.guard(query), /当前阶段/)
    // Mixed steps keep the new route pending, and don't hide a failed reused query.
    for (const failed of [false, true]) {
      const mixed = fixture({}, history)
      mixed.event({ type: 'turn/start', data: { turn: 4 } })
      mixed.event({ type: 'tool/call', data: { turn: 4, step: 1, callId: 'q', name: NAMES.query, arguments: query.arguments } })
      mixed.event({ type: 'tool/call', data: { turn: 4, step: 1, callId: 'c', name: NAMES.catalog, arguments: { subject: 'stock', dataview: 'margin', operation: 'query' } } })
      mixed.event(resultEvent({ turn: 4, step: 1, callId: 'c', payload: { mode: 'dataview' } }))
      mixed.event(resultEvent({ turn: 4, step: 1, callId: 'q', payload: { ok: !failed, sample_complete: true }, isError: failed }))
      mixed.event({ type: 'step/end', data: { turn: 4, step: 1 } })
      assert.match(mixed.prompt(), failed ? /stage=repair/ : /stage=query/)
      assert.deepEqual(mixed.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
    }
    writeRevision('catalog-v2')
    runtime.event({ type: 'turn/start', data: { turn: 5 } })
    assert.match(runtime.guard(query), /当前阶段/)
    writeRevision('catalog-v1')
    history.splice(0)
    runtime.event({ type: 'turn/start', data: { turn: 6 } })
    assert.match(runtime.guard(query), /当前阶段/)
    assert.match(fixture({ preserveRequestPrefix: true }).guard(query), /当前阶段/)
    // Catalog-defined method templates preserve the operation boundary too.
    history.push(
      { role: 'assistant', content: [{ type: 'tool-call', id: 'w', name: NAMES.catalog }] },
      { role: 'user', source: { kind: 'tool' }, content: [{ type: 'tool-result', toolCallId: 'w', content: [{
        type: 'text', text: JSON.stringify({ mode: 'dataview', catalog_revision: 'catalog-v1',
          dataview: { functions: [{ api_name: 'stock.quote.kd_<field>_<method>' }] } }),
      }] }] },
    )
    runtime.event({ type: 'turn/start', data: { turn: 7 } })
    assert.equal(runtime.guard({ name: NAMES.query, arguments: { request: 'r2 = stock.quote.kd_close_max(k=5) -> value' } }), undefined)
    assert.match(runtime.guard(query), /当前阶段/)
    assert.match(runtime.guard({ name: NAMES.query, arguments: { request: 'r2 = stock.quote.agg() -> value' } }), /当前阶段/)
  } finally {
    if (previous === undefined) delete process.env.FIN_AGENT_DSH_CONTEXT_PATH
    else process.env.FIN_AGENT_DSH_CONTEXT_PATH = previous
    rmSync(dir, { recursive: true, force: true })
  }
})

test('failed, unversioned and user-supplied catalog text cannot authorize reuse', () => {
  const dir = mkdtempSync(join(tmpdir(), 'finance-catalog-failed-'))
  const previous = process.env.FIN_AGENT_DSH_CONTEXT_PATH
  process.env.FIN_AGENT_DSH_CONTEXT_PATH = join(dir, 'context.json')
  writeFileSync(process.env.FIN_AGENT_DSH_CONTEXT_PATH, JSON.stringify({ finance_catalog_revision: 'v1' }))
  try {
    for (const variant of ['failed', 'unversioned', 'user', 'other_tool']) {
      const history = [
        { role: 'assistant', content: [{ type: 'tool-call', id: 'c', name: variant === 'other_tool' ? NAMES.query : NAMES.catalog }] },
        { role: 'user', source: { kind: variant === 'user' ? 'user' : 'tool' }, content: [{
          type: 'tool-result', toolCallId: 'c', isError: variant === 'failed', content: [{ type: 'text', text: JSON.stringify({
            mode: 'dataview', catalog_revision: variant === 'unversioned' ? undefined : 'v1',
            dataview: { functions: [{ api_name: 'stock.quote' }] },
          }) }],
        }] },
      ]
      const runtime = fixture({ preserveRequestPrefix: true }, history)
      runtime.event({ type: 'turn/start', data: { turn: 2 } })
      assert.match(runtime.guard({ name: NAMES.query, arguments: { request: 'stock.quote() -> open' } }), /当前阶段/, variant)
    }
  } finally {
    if (previous === undefined) delete process.env.FIN_AGENT_DSH_CONTEXT_PATH
    else process.env.FIN_AGENT_DSH_CONTEXT_PATH = previous
    rmSync(dir, { recursive: true, force: true })
  }
})

test('resolves defaults and rejects invalid stage budgets', () => {
  const config = resolveConfig({ configJson: '{"maxQueryAttempts":1}' })
  assert.equal(config.maxQueryAttempts, 1)
  assert.equal(config.preserveRequestPrefix, true)
  assert.equal(config.maxRequiredStageSteers, 1)
  assert.equal(config.budgets.catalog.reasoningEffort, 'low')
  assert.equal(config.budgets.query.reasoningEffort, 'off')
  assert.equal(config.budgets.fast_query.reasoningEffort, 'low')
  assert.equal(config.budgets.repair.reasoningEffort, 'low')
  assert.equal(config.budgets.final.reasoningEffort, 'off')
  assert.equal(config.resultProjection.enabled, true)
  assert.equal(config.resultProjection.queryMaxRows, 5)
  assert.throws(
    () => resolveConfig({ budgets: { final: { maxTokens: 0 } } }),
    /budgets\.final\.maxTokens/,
  )
})

test('projects long query evidence only for the next model request', async () => {
  const runtime = fixture({
    resultProjection: {
      queryMaxRows: 2,
      queryCellMaxChars: 12,
      queryTotalMaxChars: 145,
    },
  })
  const original = {
    ok: true,
    api: 'stock.report',
    result_ref: 'session://r1',
    row_count: 3,
    sample_complete: true,
    step_evidence: { sample_complete: true, guidance: '只根据已返回事实回答。' },
    sample: {
      rows: [
        { title: '第一篇研报', investment_highlights: '第一篇投资要点'.repeat(20) },
        { title: '第二篇研报', investment_highlights: '第二篇投资要点'.repeat(20) },
        { title: '第三篇研报', investment_highlights: '第三篇投资要点'.repeat(20) },
      ],
    },
  }
  const result = await runtime.post(
    { name: NAMES.query, arguments: {} },
    { isError: false, content: [{ type: 'text', text: JSON.stringify(original) }] },
  )
  const projected = JSON.parse(result.content[0].text)

  assert.equal(projected.sample.rows.length, 2)
  assert.equal(projected.sample.rows[0].title, '第一篇研报')
  assert.equal(projected.sample.rows[1].title, '第二篇研报')
  assert.equal(projected.sample_complete, false)
  assert.equal(projected.step_evidence.sample_complete, false)
  assert.ok(JSON.stringify(projected.sample.rows).length <= 145)
  assert.equal(projected.result_ref, original.result_ref)
  assert.equal(projected.row_count, 3)
  assert.equal(projected.result_projection.complete, false)
  assert.ok(projected.result_projection.shortened_fields.includes('investment_highlights'))
  assert.equal(original.sample.rows.length, 3)
  assert.equal(original.sample_complete, true)
})

test('small detail tables remain complete despite the preview row limit', async () => {
  const runtime = fixture({
    resultProjection: {
      detailMaxRows: 4,
      detailCellMaxChars: 100,
      detailTotalMaxChars: 1000,
    },
  })
  const result = await runtime.post(
    { name: NAMES.details, arguments: {} },
    {
      isError: false,
      content: [{
        type: 'text',
        text: JSON.stringify({ rows: Array.from({ length: 11 }, (_, index) => ({ index })) }),
      }],
    },
  )
  const projected = JSON.parse(result.content[0].text)

  assert.deepEqual(projected.rows.map(row => row.index), Array.from({ length: 11 }, (_, i) => i))
  assert.equal(projected.result_projection, undefined)
})

test('large detail pages are contiguous and page metadata matches visible rows', async () => {
  const runtime = fixture({ resultProjection: { detailMaxRows: 4, detailTotalMaxChars: 200 } })
  const original = {
    page: { offset: 7, returned: 20, total: 27, has_more: false },
    rows: Array.from({ length: 20 }, (_, index) => ({ index: index + 7, title: '研报'.repeat(10) })),
  }
  const result = await runtime.post({ name: NAMES.details }, {
    content: [{ type: 'text', text: JSON.stringify(original) }],
  })
  const projected = JSON.parse(result.content[0].text)
  assert.deepEqual(projected.rows.map(row => row.index), [7, 8, 9, 10])
  assert.equal(projected.page.returned, 4)
  assert.equal(projected.page.has_more, true)
  assert.equal(original.page.returned, 20)
})

test('leaves compact numeric query evidence byte-for-byte unchanged', async () => {
  const runtime = fixture()
  const text = JSON.stringify({
    ok: true,
    row_count: 2,
    sample_complete: true,
    sample: { rows: [{ close: 10.2 }, { close: 10.5 }] },
  })
  const result = await runtime.post(
    { name: NAMES.query, arguments: {} },
    { isError: false, content: [{ type: 'text', text }] },
  )
  assert.equal(result.content[0].text, text)
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
  assert.equal(legacy.budgets.query.reasoningEffort, 'low')
  assert.equal(legacy.budgets.fast_query.reasoningEffort, 'low')
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

test('successful query without a completion declaration does not force a final-answer step', () => {
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
  assert.equal(runtime.steered.length, 0)

  runtime.event({ type: 'step/end', data: { turn: 1, step: 3 } })
  runtime.stopping()
  assert.equal(runtime.steered.length, 0)
})

test('catalog guard accepts optional operation and requires its subject/view scope', () => {
  for (const executionMode of ['standard', 'fast']) {
    const runtime = fixture({ executionMode })
    for (const args of [
      {},
      { subject: 'stock' },
      { subject: 'stock', dataview: 'report_metric' },
      { subject: 'stock', dataview: 'report_metric', operation: 'query' },
      { subject: 'plate', dataview: 'constitution', operation: 'query' },
    ]) {
      assert.equal(runtime.guard({ name: NAMES.catalog, arguments: args }), undefined)
    }
    for (const args of [
      { dataview: 'report_metric' },
      { dataview: 'report_metric', operation: 'query' },
      { operation: 'query' },
      { subject: 'stock', operation: 'query' },
    ]) {
      assert.match(runtime.guard({ name: NAMES.catalog, arguments: args }), /subject/)
    }
  }
})

test('three parallel full-view reads reach query without catalog retries', () => {
  const runtime = fixture()
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  for (const [index, dataview] of ['quote', 'moneyflow', 'report'].entries()) {
    const args = { subject: 'stock', dataview }
    const callId = `full-view-${index}`
    assert.equal(runtime.guard({ name: NAMES.catalog, arguments: args }), undefined)
    runtime.event({ type: 'tool/call', data: {
      turn: 1, step: 1, callId, name: NAMES.catalog, arguments: JSON.stringify(args),
    } })
    runtime.event(resultEvent({ step: 1, callId, payload: {
      mode: 'dataview', subject: 'stock', dataview: {
        name: dataview, functions: [
          { api_name: `stock.${dataview}`, operation: 'query' },
          { api_name: `stock.${dataview}.agg`, operation: 'aggregate' },
        ],
      },
    } }))
  }
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })
  assert.match(runtime.prompt(), /stage=query reason=dataview_ready/)
  assert.equal(runtime.guard({ name: NAMES.query, arguments: { steps: [] } }), undefined)
  assert.equal(runtime.steered.length, 0)
})

test('stable-prefix mode keeps all schemas visible, guards the stage, and uses stage budgets', async () => {
  const runtime = fixture({ preserveRequestPrefix: true })
  assert.equal(runtime.restrictions.length, 0)
  assert.match(
    runtime.guard({ name: NAMES.query, arguments: { steps: [{ request: 'result = stock.quote.query() -> code' }] } }),
    /执行包/,
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

test('standard tools remain available after a successful query with bounded request budgets', async () => {
  const runtime = fixture()
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
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
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
  assert.match(runtime.prompt(), /stage=query/)
  assert.deepEqual(await runtime.request({ provider: 'p', model: 'm' }), {
    provider: 'p',
    model: 'm',
    reasoningEffort: 'off',
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
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
  assert.match(runtime.prompt(), /stage=query/)
  assert.equal((await runtime.request({})).reasoningEffort, 'off')
})

test('fast mode performs one catalog stage, one query stage, then returns without repair or paging', async () => {
  const runtime = fixture({ executionMode: 'fast' })
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  assert.match(runtime.prompt(), /FINANCE_EXECUTION mode=fast/)

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
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.query])
  assert.match(runtime.prompt(), /reason=fast_dataview_ready/)
  assert.equal((await runtime.request({})).reasoningEffort, 'low')

  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 2, callId: 'query-1', name: NAMES.query },
  })
  runtime.event(resultEvent({
    step: 2,
    callId: 'query-1',
    payload: {
      ok: true,
      api: 'stock.basic_info',
      result_ref: 'session://r1',
      sample_complete: false,
    },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })

  assert.deepEqual(runtime.restrictions.at(-1).allow, [])
  assert.match(runtime.prompt(), /reason=fast_query_complete/)
  runtime.stopping()
  assert.equal(runtime.steered.length, 0)
})

test('fast mode returns after a failed catalog stage instead of narrowing or steering', () => {
  const runtime = fixture({ executionMode: 'fast' })
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  runtime.event({
    type: 'tool/call',
    data: { turn: 1, step: 1, callId: 'catalog-1', name: NAMES.catalog },
  })
  runtime.event(resultEvent({
    step: 1,
    callId: 'catalog-1',
    payload: { error: 'route unavailable' },
    isError: true,
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })

  assert.deepEqual(runtime.restrictions.at(-1).allow, [])
  assert.match(runtime.prompt(), /reason=fast_catalog_failed/)
  runtime.stopping()
  assert.equal(runtime.steered.length, 0)
})

test('fast data-only mode concludes natively at the successful query result', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'finance-policy-'))
  const previous = process.env.FIN_AGENT_DSH_CONTEXT_PATH
  process.env.FIN_AGENT_DSH_CONTEXT_PATH = join(dir, 'context.json')
  writeFileSync(process.env.FIN_AGENT_DSH_CONTEXT_PATH, JSON.stringify({ tool_context: { _finance_data_only: true } }))
  t.after(() => {
    if (previous === undefined) delete process.env.FIN_AGENT_DSH_CONTEXT_PATH
    else process.env.FIN_AGENT_DSH_CONTEXT_PATH = previous
    rmSync(dir, { recursive: true })
  })
  const runtime = fixture({ executionMode: 'fast' })
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

  const result = await runtime.execute(
    { name: NAMES.query, arguments: {} },
    {
      isError: false,
      value: { ok: true, data_only_mode: true, data_only_complete: true },
      content: [],
    },
  )
  assert.equal(result.concludesTurn, true)
})

test('fast mode with summary must retain its final model step', async () => {
  const runtime = fixture({ executionMode: 'fast' })
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  const result = await runtime.execute({ name: NAMES.query }, { isError: false, content: [] })
  assert.equal(result.concludesTurn, undefined)
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

  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
  assert.match(runtime.prompt(), /reason=data_only_followup_allowed/)
  assert.match(runtime.prompt(), /依据本轮目标与执行结果/)
  runtime.stopping()
  assert.equal(runtime.steered.length, 0)
})

function dataOnlyFixture(t, config = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'finance-data-mode-'))
  const previous = process.env.FIN_AGENT_DSH_CONTEXT_PATH
  const contextPath = join(dir, 'context.json')
  process.env.FIN_AGENT_DSH_CONTEXT_PATH = contextPath
  writeFileSync(contextPath, JSON.stringify({ tool_context: { _finance_data_only: true } }))
  t.after(() => {
    if (previous === undefined) delete process.env.FIN_AGENT_DSH_CONTEXT_PATH
    else process.env.FIN_AGENT_DSH_CONTEXT_PATH = previous
    rmSync(dir, { recursive: true })
  })
  return { runtime: fixture(config), contextPath }
}

function toolStep(runtime, step, entries) {
  for (const [i, { kind, payload, isError }] of entries.entries()) {
    const callId = `${step}-${i}`
    runtime.event({ type: 'tool/call', data: { turn: 1, step, callId, name: NAMES[kind] } })
    runtime.event(resultEvent({ step, callId, payload, isError }))
  }
  runtime.event({ type: 'step/end', data: { turn: 1, step } })
}

test('native call-before-guard order admits every budgeted call and rejects excess calls', t => {
  const history = loadedBasicInfo(t)
  for (const preserveRequestPrefix of [false, true]) {
    for (const kind of ['catalog', 'query', 'details']) {
      const runtime = fixture({ preserveRequestPrefix, maxCatalogAttempts: 2, maxQueryAttempts: 2, maxLoadAttempts: 2 }, history)
      runtime.event({ type: 'turn/start', data: { turn: 1 } })
      if (kind !== 'catalog') toolStep(runtime, 1, [{ kind: 'catalog', payload: { mode: 'dataview' } }])
      if (kind === 'details') toolStep(runtime, 2, [{ kind: 'query', payload: {
        ok: true, data_only_mode: true, data_only_complete: false, sample_complete: false, result_ref: 'r1',
      } }])
      // Multiple calls may occur before step/end. Exercise the budget guard
      // itself, not only the final-stage transition after a completed step.
      for (const attempt of [1, 2, 3]) {
        const args = kind === 'catalog'
          ? { subject: 'stock', dataview: ['report', 'report_metric', 'basic_info'][attempt - 1], operation: 'query' }
          : kind === 'details' ? { result_ref: 'r1', offset: attempt * 5 }
            : { steps: [{ goal: '查询身份信息', request: `result = stock.basic_info(limit=${attempt}) -> code` }] }
        runtime.event({ type: 'tool/call', data: {
          turn: 1, step: 3, callId: `${kind}-${attempt}`, name: NAMES[kind], arguments: JSON.stringify(args),
        } })
        const denied = runtime.guard({ name: NAMES[kind], arguments: args })
        if (attempt <= 2) assert.equal(denied, undefined, `${kind} attempt ${attempt}`)
        else assert.match(denied, /上限/, kind)
      }
    }
  }
})

test('native repair on the third query executes and accepts a valid empty result', t => {
  const runtime = fixture({ maxQueryAttempts: 3, maxQueryRepairs: 1 }, loadedBasicInfo(t))
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  toolStep(runtime, 1, [{ kind: 'catalog', payload: { mode: 'dataview' } }])
  const payloads = [
    { ok: true, data_request_complete: false, sample_complete: true },
    { validation: { ok: false }, recovery: { retryable: true, max_retries: 1 } },
    { ok: true, row_count: 0, sample_complete: true },
  ]
  for (const [i, payload] of payloads.entries()) {
    const step = i + 2, callId = `query-${i}`
    const args = { steps: [{ goal: '查询身份信息', request: `result = stock.basic_info(limit=${i + 1}) -> code` }] }
    runtime.event({ type: 'tool/call', data: { turn: 1, step, callId, name: NAMES.query, arguments: JSON.stringify(args) } })
    assert.equal(runtime.guard({ name: NAMES.query, arguments: args }), undefined)
    runtime.event(resultEvent({ step, callId, payload }))
    runtime.event({ type: 'step/end', data: { turn: 1, step } })
    if (i === 1) assert.match(runtime.prompt(), /query_repair_allowed/)
  }
  assert.match(runtime.prompt(), /stage=final/)
  assert.doesNotMatch(runtime.prompt(), /query_repair_limit/)
})

test('native assembly requires completion only for data mode without mutating cached schemas', async t => {
  const { runtime, contextPath } = dataOnlyFixture(t)
  const original = { sections: [], contexts: [], tools: [{ name: NAMES.query,
    parameters: { type: 'object', properties: { steps: {}, data_request_complete: { type: 'boolean' } }, required: ['steps'] },
  }] }
  const assembled = await runtime.assemble(original)
  assert.deepEqual(assembled.tools[0].parameters.required, ['steps', 'data_request_complete'])
  assert.deepEqual(original.tools[0].parameters.required, ['steps'])
  writeFileSync(contextPath, JSON.stringify({ tool_context: { _finance_data_only: false } }))
  runtime.event({ type: 'turn/start', data: { turn: 2 } })
  assert.equal(await runtime.assemble(original), original)
})

test('data-only adaptive flow can read results and load another view before completing', async t => {
  const { runtime } = dataOnlyFixture(t)
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  toolStep(runtime, 1, [{ kind: 'catalog', payload: { mode: 'dataview', dataview: { name: 'constitution' } } }])
  toolStep(runtime, 2, [{ kind: 'query', payload: { ok: true, api: 'plate.constitution', data_only_mode: true,
    data_only_complete: false, result_ref: 'r1', sample_complete: false } }])
  assert.equal(runtime.guard({ name: NAMES.details, arguments: { result_ref: 'r1' } }), undefined)
  runtime.stopping()
  assert.equal(runtime.steered.length, 0)
  toolStep(runtime, 3, [{ kind: 'details', payload: { rows: [{ code: '000001.SZ' }] } }])
  assert.equal(runtime.guard({ name: NAMES.catalog, arguments: { subject: 'stock', dataview: 'quote', operation: 'query' } }), undefined)
  toolStep(runtime, 4, [{ kind: 'catalog', payload: { mode: 'dataview', dataview: { name: 'quote' } } }])
  runtime.stopping()
  assert.match(runtime.steered[0].content[0].text, /已有数据集/)
  assert.doesNotMatch(runtime.steered[0].content[0].text, /尚未取得数据/)
  toolStep(runtime, 5, [{ kind: 'query', payload: { ok: true, data_only_mode: true, data_only_complete: true, row_count: 0 } }])
  assert.deepEqual(await runtime.preStep({ step: 6 }), { kind: 'reject' })
  const count = runtime.steered.length
  runtime.stopping()
  assert.equal(runtime.steered.length, count)
})

test('one complete parallel result cannot hide another incomplete data flow', async t => {
  const { runtime } = dataOnlyFixture(t)
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  toolStep(runtime, 1, [{ kind: 'catalog', payload: { mode: 'dataview', dataview: { name: 'report' } } }])
  toolStep(runtime, 2, [true, false].map(complete => ({ kind: 'query', payload: {
    ok: true, data_only_mode: true, data_only_complete: complete,
  } })))
  assert.equal((await runtime.preStep({ step: 3 })).kind, 'enter')
  toolStep(runtime, 3, [{ kind: 'query', payload: { ok: true, data_only_mode: true, data_only_complete: true } }])
  assert.deepEqual(await runtime.preStep({ step: 4 }), { kind: 'reject' })
})

test('unfinished data-only flow respects the query budget and never forces an answer', async t => {
  const { runtime } = dataOnlyFixture(t, { maxQueryAttempts: 2 })
  runtime.event({ type: 'turn/start', data: { turn: 1 } })
  toolStep(runtime, 1, [{ kind: 'catalog', payload: { mode: 'dataview', dataview: { name: 'report' } } }])
  for (const step of [2, 3]) toolStep(runtime, step, [{ kind: 'query', payload: {
    ok: true, data_only_mode: true, data_only_complete: false,
  } }])
  assert.match(runtime.prompt(), /query_attempt_limit/)
  assert.deepEqual(await runtime.preStep({ step: 4 }), { kind: 'reject' })
  runtime.stopping()
  assert.equal(runtime.steered.length, 0)
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
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
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

  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
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

  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
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
    payload: { ok: true, api: 'stock.basic_info', sample_complete: true, data_request_complete: false },
  }))
  runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
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
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])

  for (const [step, callId] of [[4, 'load-1'], [5, 'load-2']]) {
    runtime.event({
      type: 'tool/call',
      data: { turn: 1, step, callId, name: NAMES.details },
    })
    runtime.event(resultEvent({ step, callId, payload: { rows: [{}] } }))
    runtime.event({ type: 'step/end', data: { turn: 1, step } })
  }
  assert.deepEqual(runtime.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query, NAMES.details])
  runtime.event({ type: 'tool/call', data: { turn: 1, step: 6, callId: 'load-3', name: NAMES.details } })
  assert.match(runtime.guard({ name: NAMES.details, arguments: { offset: 100 } }), /上限/)
  assert.match(runtime.prompt(), /stage=query/)
})

test('basic information can be the final data goal, including empty results', () => {
  for (const complete of [undefined, true]) {
    const runtime = fixture()
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    runtime.event({ type: 'tool/call', data: { turn: 1, step: 1, callId: 'c1', name: NAMES.catalog } })
    runtime.event(resultEvent({ step: 1, callId: 'c1', payload: { mode: 'dataview' } }))
    runtime.event({ type: 'step/end', data: { turn: 1, step: 1 } })
    runtime.event({ type: 'tool/call', data: { turn: 1, step: 2, callId: 'q1', name: NAMES.query } })
    runtime.event(resultEvent({ step: 2, callId: 'q1', payload: {
      ok: true, api: 'fund.basic_info', row_count: 0, sample_complete: true,
      ...(complete === undefined ? {} : { data_request_complete: complete }),
    } }))
    runtime.event({ type: 'step/end', data: { turn: 1, step: 2 } })
    assert.match(runtime.prompt(), complete ? /stage=final/ : /stage=query/)
    runtime.stopping()
    assert.equal(runtime.steered.length, complete ? 1 : 0)
    if (complete) assert.match(runtime.steered[0].content[0].text, /最终答案/)
  }
})

const SKILL_NAMES = {
  ...NAMES,
  skill: 'mcp__finance__read_finance_skill',
  reference: 'mcp__finance__read_finance_skill_reference',
}

async function withSkillContext(context, body) {
  const dir = mkdtempSync(join(tmpdir(), 'finance-skill-loop-'))
  const contextPath = join(dir, 'context.json')
  const previous = process.env.FIN_AGENT_DSH_CONTEXT_PATH
  process.env.FIN_AGENT_DSH_CONTEXT_PATH = contextPath
  writeFileSync(contextPath, JSON.stringify({
    revision: 'skill-turn',
    tool_context: {
      _finance_skill_catalog_prompt: 'equity-report-analysis: 个股研报观点与证据分析',
      ...context,
    },
  }))
  try {
    return await body(contextPath)
  } finally {
    if (previous === undefined) delete process.env.FIN_AGENT_DSH_CONTEXT_PATH
    else process.env.FIN_AGENT_DSH_CONTEXT_PATH = previous
    rmSync(dir, { recursive: true, force: true })
  }
}

function completeCall(runtime, step, id, name, payload, args = {}) {
  runtime.event({ type: 'tool/call', data: { turn: 1, step, callId: id, name, arguments: args } })
  runtime.event(resultEvent({ step, callId: id, payload }))
  runtime.event({ type: 'step/end', data: { turn: 1, step } })
}

test('actual Skill loading expands synthesis budget and resets between turns', async () => {
  for (const payload of [{ skills: [] }, { error: 'missing' }, { method: '分析方法' }, { skills: [{ method: '分析方法' }] }]) {
    await withSkillContext({}, async contextPath => {
      const runtime = fixture({}, [], SKILL_NAMES)
      runtime.event({ type: 'turn/start', data: { turn: 1 } })
      completeCall(runtime, 1, 's', SKILL_NAMES.skill, payload)
      completeCall(runtime, 2, 'c', NAMES.catalog, { mode: 'dataview' })
      completeCall(runtime, 3, 'q', NAMES.query, { ok: true, result_ref: 'session://r', sample_complete: false })
      completeCall(runtime, 4, 'd', NAMES.details, { rows: [{}] })
      const request = await runtime.request({})
      assert.equal(request.maxTokens, payload.method || payload.skills?.length ? 8192 : 3072)
      assert.equal(request.reasoningEffort, 'low')
      writeFileSync(contextPath, JSON.stringify({ tool_context: {} }))
      runtime.event({ type: 'turn/start', data: { turn: 2 } })
      assert.equal((await runtime.request({})).maxTokens, 1536)
    })
  }
})

test('Skill budgets retain useful followups through query twelve and detail eight', async () => {
  await withSkillContext({}, async () => {
    for (const kind of ['query', 'details']) {
      const runtime = fixture({}, [], SKILL_NAMES)
      runtime.event({ type: 'turn/start', data: { turn: 1 } })
      completeCall(runtime, 1, 's', SKILL_NAMES.skill, { method: '分析方法' })
      completeCall(runtime, 2, 'c', NAMES.catalog, { mode: 'dataview' })
      if (kind === 'details') completeCall(runtime, 3, 'q', NAMES.query, { ok: true, result_ref: 'session://r', sample_complete: false })
      const cap = kind === 'query' ? 12 : 8
      for (let n = 1; n <= cap; n++) {
        completeCall(runtime, n + 3, `${kind}${n}`, NAMES[kind], kind === 'query'
          ? { ok: true, result_ref: `session://r${n}`, sample_complete: true } : { rows: [{}] }, { offset: n })
        assert.match(runtime.prompt(), kind === 'query' && n === cap ? /stage=final/ : /stage=query/)
      }
    }
  })
})

test('larger Skill ceilings neither change guidance nor force another model round after success', async () => {
  await withSkillContext({}, async () => {
    const prompts = []
    for (const skillMaxQueryAttempts of [8, 12, 24]) {
      const runtime = fixture({ skillMaxQueryAttempts }, [], SKILL_NAMES)
      runtime.event({ type: 'turn/start', data: { turn: 1 } })
      completeCall(runtime, 1, 's', SKILL_NAMES.skill, { method: '分析方法' })
      completeCall(runtime, 2, 'c', NAMES.catalog, { mode: 'dataview' })
      completeCall(runtime, 3, 'q', NAMES.query, { ok: true, result_ref: 'session://r', sample_complete: false })
      prompts.push(runtime.prompt())
      runtime.stopping()
      assert.equal(runtime.steered.length, 0)
    }
    assert.equal(new Set(prompts).size, 1)
  })
})

test('Skill budgets are configurable and exclude fast and data-only requests', async () => {
  for (const key of ['skillMaxCatalogAttempts', 'skillMaxQueryAttempts', 'skillMaxLoadAttempts', 'skillAnalysisMaxTokens']) {
    assert.throws(() => resolveConfig({ [key]: -1 }))
  }
  for (const context of [{ _finance_data_only: true }, { _finance_execution_mode: 'fast' }, {}]) {
    await withSkillContext(context, async () => {
      const runtime = fixture({ skillAnalysisMaxTokens: 9000 }, [], SKILL_NAMES)
      runtime.event({ type: 'turn/start', data: { turn: 1 } })
      completeCall(runtime, 1, 's', SKILL_NAMES.skill, { method: '分析方法' })
      completeCall(runtime, 2, 'c', NAMES.catalog, { mode: 'dataview' })
      completeCall(runtime, 3, 'q', NAMES.query, { ok: true, sample_complete: true, result_ref: 'session://r' })
      const request = await runtime.request({})
      if (Object.keys(context).length) assert.ok(request.maxTokens <= 3072)
      else assert.equal(request.maxTokens, 9000)
    })
  }
})

test('Skill-first permits optional methods and evidence-led followup without a forced match', async () => {
  await withSkillContext({}, async () => {
    const runtime = fixture({}, [], SKILL_NAMES)
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    assert.match(runtime.prompt(), /方法选择规则/)
    assert.equal(runtime.guard({ name: SKILL_NAMES.skill, arguments: { skill_id: 'equity-report-analysis' } }), undefined)
    assert.equal(runtime.guard({ name: NAMES.catalog, arguments: { subject: 'stock', dataview: 'report' } }), undefined)
    completeCall(runtime, 1, 'method', SKILL_NAMES.skill, { skill_id: 'equity-report-analysis', method: '使用证据对比' })
    assert.match(runtime.prompt(), /stage=catalog/)
    runtime.stopping()
    assert.equal(runtime.steered.length, 0) // Supplied-evidence questions may already be answerable.
    completeCall(runtime, 2, 'catalog', NAMES.catalog, { mode: 'dataview' })
    assert.match(runtime.prompt(), /finance_query.steps/)
    completeCall(runtime, 3, 'data', NAMES.query, { ok: true, sample_complete: true, data_request_complete: true })
    assert.match(runtime.prompt(), /reason=data_request_complete/)
    assert.equal((await runtime.preStep({ step: 4 })).kind, 'enter')
    assert.equal(runtime.guard({ name: SKILL_NAMES.reference, arguments: { skill_id: 'equity-report-analysis', reference: 'references/consensus.md' } }), undefined)
    completeCall(runtime, 4, 'reference', SKILL_NAMES.reference, { content: '对齐样本' })
    assert.match(runtime.prompt(), /reason=data_request_complete/)
    assert.equal(runtime.guard({ name: NAMES.catalog, arguments: { subject: 'stock', dataview: 'report_metric' } }), undefined)
    assert.equal((await runtime.request({})).reasoningEffort, 'low')
    runtime.stopping()
    assert.equal(runtime.steered.length, 1) // Only an answer is requested; evidence tools stay available.
  })
})

test('Skill methods remain readable after data budget exhaustion without reopening queries', async () => {
  await withSkillContext({}, async () => {
    const runtime = fixture({ maxQueryAttempts: 1, skillMaxQueryAttempts: 1 }, [], SKILL_NAMES)
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    completeCall(runtime, 0, 'method', SKILL_NAMES.skill, { method: '使用证据' })
    completeCall(runtime, 1, 'catalog', NAMES.catalog, { mode: 'dataview' })
    completeCall(runtime, 2, 'query', NAMES.query, { ok: true, sample_complete: true })
    assert.match(runtime.prompt(), /stage=final/)
    assert.equal(runtime.guard({ name: SKILL_NAMES.reference, arguments: { skill_id: 'equity-report-analysis', reference: 'references/event.md' } }), undefined)
    assert.match(runtime.guard({ name: NAMES.query, arguments: {} }), /当前阶段/)
    completeCall(runtime, 3, 'reference', SKILL_NAMES.reference, { content: '对照假设' })
    assert.match(runtime.prompt(), /stage=final/)
    assert.equal((await runtime.preStep({ step: 4 })).kind, 'enter')
  })
})

test('an unmatched complex question can extend evidence without activating a Skill', async () => {
  await withSkillContext({}, async () => {
    const runtime = fixture({}, [], SKILL_NAMES)
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    completeCall(runtime, 0, 'method', SKILL_NAMES.skill, { skills: [] }, { skill_ids: [] })
    completeCall(runtime, 1, 'catalog-1', NAMES.catalog, { mode: 'dataview' })
    completeCall(runtime, 2, 'query-1', NAMES.query, { ok: true, sample_complete: true, data_request_complete: true })
    assert.match(runtime.prompt(), /reason=data_request_complete/)
    assert.equal(runtime.guard({ name: NAMES.catalog, arguments: { subject: 'stock', dataview: 'quote', operation: 'query' } }), undefined)
    completeCall(runtime, 3, 'catalog-2', NAMES.catalog, { mode: 'dataview' })
    completeCall(runtime, 4, 'query-2', NAMES.query, { ok: true, sample_complete: true })
    assert.equal((await runtime.preStep({ step: 5 })).kind, 'enter')
    runtime.stopping()
    assert.equal(runtime.steered.length, 0) // Answer from sufficient evidence; no forced method or query.
  })
})

test('Skill data-only calls preserve required retrieval and terminal raw-data delivery', async () => {
  await withSkillContext({ _finance_data_only: true }, async () => {
    const runtime = fixture({}, [], SKILL_NAMES)
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    completeCall(runtime, 1, 'method', SKILL_NAMES.skill, { method: '预测值口径' })
    assert.match(runtime.prompt(), /stage=catalog/)
    runtime.stopping()
    assert.equal(runtime.steered.length, 1)
    completeCall(runtime, 2, 'catalog', NAMES.catalog, { mode: 'dataview' })
    completeCall(runtime, 3, 'query', NAMES.query, { ok: true, data_only_mode: true, data_only_complete: true })
    assert.match(runtime.prompt(), /stage=final/)
    assert.equal((await runtime.preStep({ step: 4 })).kind, 'reject')
    assert.match(runtime.guard({ name: SKILL_NAMES.reference, arguments: {} }), /当前阶段/)
  })
})

test('reused Skill workers reset scope and fast mode keeps its one-query budget', async () => {
  await withSkillContext({}, async contextPath => {
    const runtime = fixture({ executionMode: 'fast' }, [], SKILL_NAMES)
    runtime.event({ type: 'turn/start', data: { turn: 1 } })
    completeCall(runtime, 0, 'method', SKILL_NAMES.skill, { skills: [] }, { skill_ids: [] })
    completeCall(runtime, 1, 'catalog', NAMES.catalog, { mode: 'dataview' })
    assert.match(runtime.guard({ name: NAMES.catalog, arguments: { subject: 'stock', dataview: 'report_metric' } }), /当前阶段/)
    completeCall(runtime, 2, 'query', NAMES.query, { ok: true, sample_complete: true })
    assert.match(runtime.prompt(), /stage=final/)
    assert.equal(runtime.guard({ name: SKILL_NAMES.reference, arguments: { reference: 'references/topic.md' } }), undefined)
    assert.match(runtime.guard({ name: NAMES.query, arguments: {} }), /当前阶段/)
    writeFileSync(contextPath, JSON.stringify({ tool_context: { _finance_data_only: true } }))
    runtime.event({ type: 'turn/start', data: { turn: 2 } })
    assert.match(runtime.prompt(), /一次目录定位/)
    assert.doesNotMatch(runtime.prompt(), /命中或用户显式指定/)
  })
})
