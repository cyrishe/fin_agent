# Codex Astra runtime（2026-09-06）

默认模型 `gpt-6-astra`，推理强度 `low`，继续使用既有 CRS API key。

生产业务只调用 Python SDK（`CodexSdkSkillHarness`），不通过 shell 执行 `codex exec`。
但 SDK 本身仍会启动一个 app-server runtime 二进制；`CodexConfig.codex_bin` 只是选择这个
runtime，不代表业务切换成 CLI 框架。

Python SDK 固定 `openai-codex==0.147.0`。它捆绑的 runtime 也是 0.147.0，能够经 CRS
显式调用 Astra，但内置模型目录没有 Astra，只能使用 fallback metadata。当前生产环境因此用
`STOCK_AGENT_CODEX_BIN` 为 SDK 选择 0.153.4 runtime；该版本的内置目录原生包含 Astra。

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

## 判断与升级边界

- 服务器隔离 `CODEX_HOME` 不配置 `model_catalog_json`。静态目录是完整覆盖，不用于补丁。
- 自定义工具阶段始终显式传入 `CODEX_CRS_MODEL`，因此不依赖交互式模型选择器。
- Proxy 的 `/models` 返回与实际 Responses 调用分开判断；最终以 SDK 的结构化 turn 为准。
- 只有新版 `openai-codex` 所捆绑 runtime 的内置目录已包含 `gpt-6-astra`，并通过真实 Skill
  冒烟后，才移除 `STOCK_AGENT_CODEX_BIN`。在此之前保留 override，避免 metadata 回退。
- SDK Skill 只通过一个 `TextInput` 传入完整 Skill、上下文和用户请求。不要再叠加原生
  `SkillInput`；两套 Skill 激活会使 runtime 进入普通 Agent 执行并绕过阶段结构化输出。

## 生产验收

验收必须从服务使用的 `.venv` 和 `.env` 启动 `CodexSdkSkillHarness`，至少确认：

1. SDK import 成功，配置的 runtime 可执行；
2. `model=gpt-6-astra`、`auth_mode=crs_api_key`；
3. 直接结构化 turn 返回预设 marker；
4. 实际 `financial-tool-requirement` Skill 返回非空 `requirement_brief`，明确需求下
   `questions=0`，且 `ok=true`；
5. 服务完整重启或 Gunicorn HUP 后，新 worker 正常启动，公网入口可访问。

2026-09-07 服务器实测：直接 turn 约 22.4 秒；修复后的 Requirement Skill 约 58.0 秒，
返回 396 字需求资产、3 条默认口径、0 个追问，schema 解析成功。

官方资料：
- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://learn.chatgpt.com/docs/changelog
