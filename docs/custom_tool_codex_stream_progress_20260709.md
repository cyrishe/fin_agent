# Custom Tool Codex Stream Progress

Date: 2026-07-09

## Current Scope

This track builds a personal custom-tool creation flow driven by natural language.

Main flow:

```text
/custom_tool create <requirement>
-> Codex requirement understanding
-> user confirms or edits design
-> Codex coding
-> local sandbox smoke test
-> draft custom tool
-> /custom_tool commit <tool_name>
```

The implementation is intentionally separate from the standard finance data tool catalog.
Personal tools can call approved SDK helpers, but they are not merged into the subject/dataview/function finance API hierarchy.

## Implemented

- Requirement and coding skill prompt files.
- Codex SDK and CLI harness wrapper with stream events.
- Context bundle generation for Codex runs, including API catalog references and custom tool SDK notes.
- Custom tool store, runtime, draft save, smoke test, call, and commit flow.
- `/custom_tool create/edit/call/commit` command routing.
- Dynamic custom tool lookup from the normal tool registry.
- SSE stream endpoints for custom tool create/edit.
- Frontend agent-run card:
  - short status summary
  - structured artifacts
  - foldable process trace
  - action buttons such as confirm
- Stable frontend render block protocol:
  - `markdown`
  - `table`
  - `bar_chart`
  - `line_chart`
  - `flowchart`
  - `code`
  - `action`
- Strict Codex structured output schema compatible with OpenAI response format rules.
- Codex run timeout split:
  - idle timeout: `STOCK_AGENT_CUSTOM_TOOL_CODEX_TIMEOUT_SECONDS`, default 180 seconds
  - hard timeout: `STOCK_AGENT_CUSTOM_TOOL_CODEX_HARD_TIMEOUT_SECONDS`, default 900 seconds

## Current Limitations

- Custom tool state is still a single active thread-level state.
- A normal free-chat message is routed into `custom_tool_state` when a custom tool flow is active.
- Topic switching and later returning to a suspended custom tool task is not yet implemented.
- Frontend local `customToolActive` is not fully restored from thread context after page refresh.
- Database persistence is not implemented yet; custom tools are file-backed drafts under `data/custom_tools`.
- SDK stream idle timeout depends on the SDK yielding events; the hard timeout is the reliable upper bound.

## Validation

Targeted checks used during this stage:

```bash
python -m py_compile src/services/codex_exec_skill_harness.py src/services/llm_stream_block_service.py src/web/flask_app.py
PYTHONPATH=/Volumes/ext/stock_agent pytest tests/test_custom_tool_service.py -q
```

Latest result:

```text
9 passed
```
