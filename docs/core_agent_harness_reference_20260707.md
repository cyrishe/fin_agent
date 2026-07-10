# Core Agent Harness Reference

Date: 2026-07-07

This note records the latest reference-repo review after refreshing `../ref_repos`.
It is a design reference for the stock_agent core agent architecture, not an implementation plan.

## Reference Scope

- `openai-agents-python` updated to `078a28f1`
- `pi-mono` updated to `2b00dade`
- `hermes-agent` updated to `043e71f1f`
- `OpenHands` updated to `ef3323afd`
- `claude-code-sourcemap` stayed at `a8a678c`
- `openclaw` pull was stopped at `348b094fe8` because object unpacking took too long

## Main Conclusion

The useful pattern is not a large prompt-driven workflow.
The useful pattern is:

```text
small prompts + deterministic tool schemas + explicit runtime state + validation/check hooks + bounded retry
```

The outer system should be a harness/runtime, not another business planner.
Domain tools such as finance data query should keep their own internal planning and execution protocol.

## OpenAI Agents SDK

OpenAI Agents SDK is the cleanest reference for explicit run-state modeling.

Relevant files:

- `../ref_repos/openai-agents-python/src/agents/run_state.py`
- `../ref_repos/openai-agents-python/src/agents/run_internal/run_steps.py`
- `../ref_repos/openai-agents-python/src/agents/prompts.py`

Key observations:

- `RunState` is a durable pause/resume snapshot.
- It stores current turn, current agent, starting agent, original input, model responses, generated/session items, max turns, conversation ids, guardrail results, interruptions, trace state, tool tracker state, and sandbox resume payload.
- The next action is typed, not free-form text:
  - `NextStepFinalOutput`
  - `NextStepHandoff`
  - `NextStepRunAgain`
  - `NextStepInterruption`
- Tool problems are modeled explicitly, for example missing function tools and custom tool calls.
- Prompt support is intentionally thin: prompt id, version, variables, or a dynamic prompt function.

Design implication:

The core runtime should own loop state and transitions in code.
Prompts should only produce constrained outputs inside a narrow contract.

## pi-mono

`pi-mono` is the closest reference for our desired harness/runtime split.

Relevant files:

- `../ref_repos/pi-mono/packages/agent/docs/agent-harness.md`
- `../ref_repos/pi-mono/packages/agent/src/agent-loop.ts`
- `../ref_repos/pi-mono/packages/agent/src/agent.ts`
- `../ref_repos/pi-mono/packages/agent/src/types.ts`

Key observations:

- It separates low-level agent loop, stateful `Agent`, and higher-level `AgentHarness`.
- `AgentHarness` owns session persistence, runtime config, resource resolution, operation locking, events, and extension mutation semantics.
- It separates state into:
  - harness config
  - turn snapshot
  - persisted session
  - pending session writes
- It defines explicit phases:

```ts
type AgentHarnessPhase = "idle" | "turn" | "compaction" | "branch_summary" | "retry";
```

- A turn snapshot freezes model, tools, resources, system prompt, messages, stream options, and session id for one provider request.
- Config changes during a turn affect the next turn, not the in-flight request.
- Save points flush pending writes and prepare fresh state before the next provider request.
- The low-level loop supports:
  - `prepareNextTurn`
  - `shouldStopAfterTurn`
  - `getSteeringMessages`
  - `getFollowUpMessages`
  - `beforeToolCall`
  - `afterToolCall`
- Tool execution can be sequential or parallel. Validation and before/after hooks are code-level boundaries.

Design implication:

For stock_agent, this is the best model:

```text
dialog/context builder
-> top-level tool router
-> harness executes tool runtime
-> register tool result as session variable
-> check
-> retry / continue / final response
```

Finance data query remains a domain-owned tool:

```text
finance phase1
-> finance phase2
-> api execution
-> internal check/repair
```

The outer harness should not split finance-domain steps again.

## Hermes Agent

Hermes is useful as a single-agent engineering reference, but its structure is heavier.

Relevant files:

- `../ref_repos/hermes-agent/run_agent.py`
- `../ref_repos/hermes-agent/agent/prompt_builder.py`

Key observations:

- `AIAgent` centralizes a lot of runtime behavior.
- It has max-iteration and iteration-budget controls.
- It explicitly marks ephemeral recovery/verification scaffolding so these messages are not persisted.
- It builds prompts through a separate prompt builder module.
- It scans context files for prompt-injection patterns before injecting them.
- Tool calls can be sequential or concurrent, with logic to avoid unsafe parallel execution.
- There are hooks/callbacks for status, tool progress, streaming, reasoning, events, and notices.

Design implication:

Borrow the engineering guardrails:

- context-file scanning before prompt injection
- iteration limits
- non-persistent internal recovery messages
- tool execution safety boundaries
- clear status/progress callbacks

Do not borrow the full monolithic structure.

## OpenHands

The refreshed OpenHands repo has shifted.
Its README says the OpenHands Agent and Agent Server source now lives in `OpenHands/software-agent-sdk`, while this repo is mainly Agent Canvas / app server / backend orchestration.

Design implication:

The refreshed OpenHands repo is not the best current source for core loop implementation.
It remains useful for product-level concepts:

- multi-backend agent control center
- app conversation lifecycle
- pending messages while a conversation is not ready
- agent server separation

It should not be used as the primary loop reference unless `software-agent-sdk` is added and reviewed separately.

## Claude Code Sourcemap

This repo is useful mainly for tool schema and permission-mode ideas.

Relevant file:

- `../ref_repos/claude-code-sourcemap/package/sdk-tools.d.ts`

Useful observations:

- tools have narrow typed input schemas
- spawned agents can run in modes such as `plan`, `default`, `acceptEdits`, `bypassPermissions`
- shell commands include an explicit human-readable `description`
- plan exit can include prompt-based permission categories

Design implication:

Borrow schema-level ideas for permissions and tool-call clarity.
Do not use it as the main harness-loop reference.

## Recommended Stock Agent Direction

### Core Principle

The outer layer should be a generic harness/runtime.
It should not become a second finance planner.

### Minimal Runtime Responsibilities

- Build dialog/session context.
- Route to a top-level tool.
- Execute tool runtime.
- Register result as a session variable.
- Expose schema and small samples to later steps.
- Keep full data addressable by variable id.
- Run validation/check hooks.
- Retry only through bounded, typed states.
- Produce final response.

### Minimal State Set

Run-level:

```text
idle
running
checking
retrying
waiting_user
done
failed
```

Step/tool-level:

```text
pending
running
succeeded
skipped
failed
need_repair
```

Variable-level:

```text
registered
materialized
provider_missing
expired
```

### Prompt Boundary

Prompts should be short and contract-focused.

Use prompts for:

- structured intent extraction
- domain-specific request generation
- result checking summaries

Do not use prompts for:

- enforcing state transitions
- deciding irreversible policy
- maintaining hidden runtime state
- duplicating deterministic validation

### Tool Boundary

Top-level tools should be peers:

```text
finance_data_query
file_read_write
search
```

Each major tool owns its internal protocol.
The outer harness sees them as tools with inputs, outputs, schema, samples, status, and artifacts.

### Finance Tool Boundary

Finance data query owns:

- phase1 subject/dataview planning
- phase2 precise API request generation
- API/context loading
- provider execution
- domain validation
- internal repair loops

The outer harness should only call it as one top-level tool unless the user explicitly asks for cross-tool orchestration.

## Practical Rule

If a behavior can be validated or controlled in code, do not add more prompt text.
If a behavior must rely on the model, make the output shape narrow and statically checkable.

## Codex Exec Event Normalization

When the custom-tool flow runs through `codex exec` with a skill, the final stream should include both model-emitted events and wrapper-captured execution events.

The skill should only define model-visible output events, such as:

```text
codex/agent_update
model/analysis
model/draft_design
model/final
```

The Python wrapper around `codex exec` should additionally normalize observable execution events that are transparent to the skill:

```text
harness/tool_call
harness/tool_result
```

System decoding can map all of these to the user-facing thinking area, while preserving `source` and `type` internally for filtering, tracing, retry, and debugging. The state machine should still only use `model/final` as the authoritative stage result.
