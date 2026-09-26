# 管理员与访客权限

- 管理员使用 `aiia_user.user_type = 'admin'`，沿用手机号验证、密码登录和会员会话。普通注册始终创建 `member`，接口不接受用户提交管理员类型。
- `/status`、`/status/data`、`/v1/usage/daily` 仅接受管理员登录会话。统计页自动读取统计，无需另填 API Key；外部金融查询和 MCP 继续使用原 API Key。
- 访客每个服务端访客身份累计最多提交 3 次对话请求，跨线程共享额度。同步对话和两个流式入口统一计数，不按内部模型 turns 计数。请求进入执行后即占用额度，包括后续执行失败或流式连接未打开。
- 额度保存在 `aiia_user.profile_json.guest_questions_used`，通过带上限条件的原子更新预占，进程重启不重置。存储失败时拒绝执行。新上线时既有访客从 0 开始。
- 访客无法直接调用其他写入、任务提交或工具执行接口；注册会员和管理员不受访客额度限制。
- 访客身份依赖浏览器会话，清除 Cookie 后可以获得新身份。该限制是匿名试用额度，不是跨设备实名限额。

管理员授权必须对已注册账号执行数据库更新，先查询 `aiia_user_identity` 中 `identity_type='phone'` 对应的 `user_id`，确认后将该用户的 `user_type` 从 `member` 改为 `admin`。不设置手机号白名单，也不创建跳过手机验证的预置账号。

部署后重启 `fin-agent-web.service` 和金融 API 服务。账户类型在每次会话校验时读取；授权后刷新即可生效。

2026-09-07 部署核对：金融 API 当时由手动进程 PID `657225` 监听 `127.0.0.1:22054`，systemd 服务未运行。此次切换时先核对该 PID 仍是 `.venv/bin/python -m src.finance_api.app`，再结束它并启动正式服务：

```bash
ps -p 657225 -o pid,args
kill -INT 657225
sudo systemctl restart fin-agent-finance-api.service
sudo systemctl restart fin-agent-web.service
```

此 PID 仅对应此次部署，不应在后续部署中复用。切换成功后，后续仅需重启两个 systemd 服务。
