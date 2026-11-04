# Architecture

```text
Browser / main frontend
        |
        | POST /v1/runs/stream
        v
FastAPI boundary
  validation · optional API key · concurrency · disconnect cancellation
        |
        v
AgentHarness
  run IDs · monotonic seq · idle/hard timeout · terminal state
        |
        v
AgentBackend protocol
  +-------------------------+--------------------+
  |                                              |
  v                                              v
ClaudeAgentBackend                         FakeAgentBackend
  |                                              |
  | ClaudeAgentOptions                           | deterministic events
  | skills / prompt / sessions / hooks           |
  v                                              |
Claude SDK subprocess                            |
  |                                              |
  +-- Anthropic / DeepSeek / MaaS gateway        |
  +-- built-in WebSearch                         |
  +-- in-process MCP tools -----------------------+
        |
        v
ClaudeEventNormalizer
        |
        v
agent_stream.v1 SSE
```

## Boundaries

### HTTP boundary

Owns request validation, client authentication hook, bounded concurrency, SSE framing and disconnect cancellation. It does not know Claude message classes.

### Harness boundary

Owns provider-neutral lifecycle and reliability. It accepts an `AgentBackend`, therefore mainline code can inject Codex, Claude, a test double, or another agent runtime without changing the public stream contract.

### Claude adapter

Owns only Claude-specific configuration and normalization:

- `ClaudeAgentOptions`
- skills and project setting source
- built-in tool visibility
- in-process MCP registration
- permissions/hooks
- provider environment
- Claude message/content-block mapping

It does not own finance-domain state transitions.

### Skills and prompts

`prompts/system.md` defines stable product behavior. `workspace/CLAUDE.md` defines workspace context. `.claude/skills/*/SKILL.md` contains progressively disclosed domain workflows. These layers remain distinct because they have different lifetime and loading behavior.

### Tool boundary

Each custom tool has a narrow schema and returns both human-readable `content` and machine-readable `structuredContent`. External data is labeled untrusted. The model never receives search service credentials.

## Capability profiles

This demo implements a read-only research profile. A future coding profile should be separate:

| Profile | Built-ins | Workspace | Permission |
|---|---|---|---|
| research | Skill, optional WebSearch | read-only context | dontAsk + explicit allow |
| tool-authoring | Skill, Read, Write/Edit, Bash tests | per-run ephemeral checkout | container sandbox + path rules + approval |
| production operation | domain MCP tools only | no repo mount | per-tool auth/idempotency/approval |

Changing profiles is a harness policy decision, not a prompt suffix.

模型 provider 与能力 profile 是两个维度。`deepseek-v4-flash` 复用 Claude adapter 的 Anthropic transport，但 capability registry 仍需记录其 image/document/MCP connector 等兼容差异；这些差异不应泄漏到前端 SSE 合同。
