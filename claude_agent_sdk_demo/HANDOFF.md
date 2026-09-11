# 主线接入简要说明

这套 demo 是独立验证实现，不依赖也不修改主项目 `src/`。建议把设计和 adapter 分阶段合入主线，不要直接把整个 FastAPI demo 当成生产服务。

## 可以复用的模块

- `app/backend.py`：Claude Agent SDK provider adapter，集中配置 model、Skills、tools、hooks、sessions 和 provider env。
- `app/normalizer.py`：把 Claude SDK message/content blocks 转成 provider-neutral events。
- `app/harness.py`：统一 run 生命周期、idle/hard timeout 和 terminal result。
- `app/event_stream.py`：稳定的 `agent_stream.v1` SSE envelope 示例。
- `app/config.py`：Anthropic、DeepSeek 官方端点和通用 MaaS gateway 的配置边界。
- `app/sdk_tools.py`：in-process MCP tools，以及搜索能力与模型 provider 解耦的示例。

## 推荐合入顺序

1. 在主线新增 `AgentBackend` protocol 与 Claude adapter，保留现有 Codex adapter并行运行。
2. 让两个 provider 都输出统一的 `assistant.delta`、`tool.started`、`tool.completed` 和 final result，再接入现有前端 block/surface builder。
3. 将主线已有金融工具逐个包装为窄 schema、只读优先的 MCP tools；身份、租户、幂等和审计继续放在工具执行端。
4. 将业务 stage 的可信 structured-output schema 交给 wrapper 选择，不允许前端上传任意 schema。
5. 最后才评估 coding profile；它必须使用独立 checkout/container，不能在 research profile 中简单开放 Bash/Write/Edit。

更详细的文件落点和兼容测试见 `MERGE_GUIDE.md`。

## Provider 选择

- `CLAUDE_PROVIDER=anthropic`：Anthropic 官方 API。
- `CLAUDE_PROVIDER=deepseek`：只允许 DeepSeek 官方 Anthropic endpoint，并使用专用 `DEEPSEEK_API_KEY`。
- `CLAUDE_PROVIDER=dashscope`：阿里云百炼按量付费 Anthropic endpoint；可安全映射主项目的 `LLM_KEY` 与 `LLM_DEFAULT_MODEL`。
- `CLAUDE_PROVIDER=gateway`：自有 MaaS 或第三方托管模型；必须实现 Anthropic Messages streaming/tool-use 合同。

不要按 model name 在业务代码中散落分支。主线应维护 provider capability，例如 text、stream、tools、web search、vision、structured output，再由 adapter 处理差异。

## 本地验证

```bash
cd claude_agent_sdk_demo
.venv/bin/pytest -q
CLAUDE_DEMO_BACKEND=fake .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8008
```

DeepSeek V4 Flash 真实兼容测试：

```bash
cp .env.deepseek.example .env.deepseek
# 在未跟踪的 .env.deepseek 中填写 DEEPSEEK_API_KEY
set -a
source .env.deepseek
set +a
.venv/bin/python scripts/check_deepseek.py
.venv/bin/python scripts/check_deepseek.py --with-web-search
```

## 上生产前仍需补齐

- API gateway 身份认证、tenant ownership、限流和审计持久化；
- provider session 与业务用户的 ownership 映射；
- 每次 run 的隔离 workspace/container；
- 外部工具 timeout、retry、idempotency 与 credential proxy；
- Codex/Claude/DeepSeek 同一组 contract/eval cases；
- prompt injection、权限拒绝、断流、重连和模型不支持 capability 的回归测试。
