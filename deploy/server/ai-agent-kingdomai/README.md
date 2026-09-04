# ai-agent.kingdomai.com 部署说明

部署目录为 `/home/che/cyris/fin_agent`，进程只监听 `127.0.0.1:22054`，
Nginx 对外发布到：

- 数据体系页面：`https://ai-agent.kingdomai.com/finance/data-map`
- OpenAPI：`https://ai-agent.kingdomai.com/finance/docs`
- REST 查询：`https://ai-agent.kingdomai.com/finance/v1/finance/query`
- MCP Streamable HTTP：`https://ai-agent.kingdomai.com/finance/mcp`

## 1. 更新代码与 Python 依赖

服务器的 `/usr/sbin/python3` 不是 CPython，必须明确使用 `/usr/bin/python3`：

```bash
cd /home/che/cyris/fin_agent
git pull --ff-only origin agent/financial-tool-design-protocol
/usr/bin/python3 -m venv .venv
.venv/bin/python -m pip install \
  -i https://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com \
  -r requirements-finance-api.txt
```

## 2. 安装 DeepSeek Harness 运行时

当前通过源码入口运行 DSH。服务器需要 Node.js `22.19+`，以及本次验证使用的
Harness commit `cd5ef8148158c3a752a658978873241fdf8e2bbc`。

```bash
cd /home/che/cyris
git clone git@github.com:deepseek-ai/deepseek-harness.git
cd deepseek-harness
git checkout cd5ef8148158c3a752a658978873241fdf8e2bbc
corepack enable
pnpm install --frozen-lockfile
```

若系统 Node 版本低于要求，把 Node 22 安装到用户目录，并在 `.env` 中用
`FINANCE_DSH_NODE_BIN` 指向它，不必替换其他项目使用的系统 Node。

## 3. 填写服务环境变量

```bash
cd /home/che/cyris/fin_agent
cp deploy/finance-api/.env.example .env
chmod 600 .env
```

编辑 `.env`。本机固定项：

```dotenv
FINANCE_API_HOST=127.0.0.1
FINANCE_API_PORT=22054
FINANCE_API_ROOT_PATH=/finance
FINANCE_API_ALLOWED_HOSTS=ai-agent.kingdomai.com,ai-agent.kingdomai.com:443,127.0.0.1:*
FINANCE_DSH_SOURCE_ROOT=/home/che/cyris/deepseek-harness
FINANCE_DSH_SDK_SOURCE=/home/che/cyris/deepseek-harness/python/sdk/src
FINANCE_DSH_NODE_BIN=/home/che/cyris/runtime/node-v22.19.0-linux-x64/bin/node
```

必须替换的凭据：

- `FINANCE_API_KEYS_JSON`：服务访问 key，可用 `openssl rand -hex 32` 生成。
- `KINGDOMAI_DB_URL`：金融数据库连接串。
- `FINANCE_DSH_API_KEY` / `FINANCE_DSH_BASE_URL` / `FINANCE_DSH_MODEL`：
  DeepSeek 官方或百炼兼容入口。
- `CODEX_CRS_API_KEY`：服务器 Codex runtime 的 CRS/KingdomAI key。
- `DASHSCOPE_API_KEY`：百炼 OpenAI 兼容入口。
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_DEFAULT_MODEL`：基础模型入口。

`STOCK_AGENT_CODEX_AUTH_MODE=crs_api_key` 会强制 Codex 使用显式 API key；缺 key
会直接报错，不会回退到个人 ChatGPT 订阅。

## 4. 安装 Nginx 配置

`/home/che/cyris/fin_agent_deploy/nginx/` 中保存线上配置备份与加入 `/finance/`
后的候选文件。先核对：

```bash
diff -u \
  /home/che/cyris/fin_agent_deploy/nginx/ngx.conf.backup \
  /home/che/cyris/fin_agent_deploy/nginx/ngx.conf.candidate
```

确认后由有 sudo 权限的用户执行：

```bash
sudo cp /home/che/cyris/fin_agent_deploy/nginx/ngx.conf.candidate \
  /etc/nginx/conf.d/ngx.conf
sudo nginx -t
sudo systemctl reload nginx
```

回退命令：

```bash
sudo cp /home/che/cyris/fin_agent_deploy/nginx/ngx.conf.backup \
  /etc/nginx/conf.d/ngx.conf
sudo nginx -t && sudo systemctl reload nginx
```

## 5. 启动服务

先前台检查：

```bash
cd /home/che/cyris/fin_agent
.venv/bin/python -m pytest tests/test_finance_api_app.py -q
set -a; source .env; set +a
.venv/bin/python -m src.finance_api.app
```

另开终端验证 `curl http://127.0.0.1:22054/health`，然后 `Ctrl-C` 停止前台进程，
安装 systemd 单元：

```bash
sudo cp \
  /home/che/cyris/fin_agent/deploy/server/ai-agent-kingdomai/fin-agent-finance-api.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fin-agent-finance-api
sudo systemctl status fin-agent-finance-api
```

日志与最终验证：

```bash
sudo journalctl -u fin-agent-finance-api -f
curl https://ai-agent.kingdomai.com/finance/health
curl https://ai-agent.kingdomai.com/finance/v1/tools \
  -H 'Authorization: Bearer YOUR_FINANCE_API_KEY'
```
