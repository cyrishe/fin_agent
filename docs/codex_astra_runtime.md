# Codex Astra runtime（2026-09-06）

默认模型 `gpt-6-astra`，推理强度 `low`，继续使用既有 CRS API key。

Python SDK 固定 `openai-codex==0.147.0`。该 SDK 配套二进制为 0.147.0，
早于 Astra 支持；应用通过 `STOCK_AGENT_CODEX_BIN` 显式选择 CLI 0.153.4。

```bash
npm install --prefix /home/che/cyris/runtime/codex-astra @openai/codex@0.153.4
.venv/bin/python -m pip install openai-codex==0.147.0
```

```dotenv
STOCK_AGENT_CODEX_BIN=/home/che/cyris/runtime/codex-astra/node_modules/.bin/codex
CODEX_CRS_MODEL=gpt-6-astra
CODEX_CRS_REASONING_EFFORT=low
STOCK_AGENT_CUSTOM_TOOL_CODEX_MODEL=gpt-6-astra
STOCK_AGENT_CUSTOM_TOOL_CODEX_REASONING=low
```

修改后重启使用 Codex 的服务，使已加载的环境变量生效。数据查询的 DSH 模型保持原配置。

官方资料：
- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://learn.chatgpt.com/docs/changelog
