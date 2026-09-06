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

function fixture(config = {}, history = []) {
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
    session: { deriveMessages: () => history },
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
    assert.match(runtime.guard(query), /当前阶段/)
    const original = { mode: 'dataview', dataview: { functions: [{ api_name: 'stock.quote' }] } }
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
    assert.match((await runtime.preStep({ turn: 2, step: 1 })).messages[0].content[0].text, /不是旧查询结果/)
    runtime.event({ type: 'tool/call', data: { turn: 2, step: 1, callId: 'q2', name: NAMES.query, arguments: JSON.stringify(query.arguments) } })
    runtime.event(resultEvent({ turn: 2, step: 1, callId: 'q2', payload: { ok: true, sample_complete: true } }))
    runtime.event({ type: 'step/end', data: { turn: 2, step: 1 } })
    assert.equal((await runtime.request({})).reasoningEffort, 'off')
    assert.match(runtime.guard(query), /当前阶段/)
    runtime.event({ type: 'turn/start', data: { turn: 3 } })
    for (const request of [
      'r1 = stock.margin() -> financing_balance',
      'r1 = stock.quote.agg(agg="avg(close)") -> value',
      'r1 = stock.quote.kd_close_max(k=5) -> value',
    ]) {
      assert.match(runtime.guard({ name: NAMES.query, arguments: { request } }), /当前阶段/)
    }
    assert.match(runtime.guard({ name: NAMES.query, arguments: { steps: [query.arguments.steps[0], { request: 'r2 = stock.margin() -> code' }] } }), /当前阶段/)
    // A new Agent (cold session resume) derives the same eligibility, including
    // native tool-restriction mode. No in-memory cache is required.
    const cold = fixture({}, history)
    cold.event({ type: 'turn/start', data: { turn: 4 } })
    assert.deepEqual(cold.restrictions.at(-1).allow, [NAMES.catalog, NAMES.query])
    assert.equal(cold.guard(query), undefined)
    assert.match(cold.guard({ name: NAMES.query, arguments: { request: 'stock.margin() -> code' } }), /当前阶段/)
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
      assert.deepEqual(mixed.restrictions.at(-1).allow, [NAMES.query])
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
  assert.deepEqual(runtime.restrictions.at(-1).allow, [])
  assert.match(runtime.prompt(), /stage=final/)
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
