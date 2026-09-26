# Skill 阅读空间发布准备（2026-09-11）

- 功能提交 `b3c453646ef85849f09b1efee36030ffe5a0fd2a`，分支 `agent/financial-tool-design-protocol`，已推送 GitHub、Codeup。
- 正式源码 `/home/che/cyris/fin_agent` 已从 `041ee3e` 快进至该功能提交。
- 仅提交本轮 12 个阅读空间相关文件；本地其他工具开发、BlockRenderer 等未提交改动没有打包发布。
- 服务器 Python 3.10 定向回归 20 passed；前端 18 passed；TypeScript 与 Vite production build 通过。构建使用正常 `/fin_agent/` 前缀，未使用本地根路径预览构建。保留既有大 chunk 告警，不宣称全量回归通过。

## 备份与构建

目录：`/home/che/cyris/fin_agent_deploy/skill-reader-20260911-b3c4536/`。

- `source-before.tar`：原 Git 源码，不含 .env 或运行数据。
- `frontend-before/`：原前端完整备份。
- `frontend-next/`：服务器当前功能提交构建。
- 新哈希 assets 预置到正式 dist，旧 index 保持不变。没有删除旧资源。

## 当前阻塞：待管理员激活

`sudo -n systemctl restart fin-agent-web.service` 返回 `sudo: a password is required`。正式 Web PID 仍为 `3214443`，启动时间 `2026-09-10 10:59:04 CST`。没有杀进程或绕过 systemd 权限。

**源码同步与构建已完成，但新版尚未激活。** 公网 Studio HTTP 200 和 API health 正常只证明旧服务可用，不证明新阅读页已上线。

管理员可作为部署用户执行（交互式 sudo 输入密码，不把密码写入命令或仓库）：

```bash
bash /home/che/cyris/fin_agent/deploy/server/ai-agent-kingdomai/activate-skill-reader-20260911.sh
```

脚本检查代码未偏离当前构建，重启 Web，确认 Studio 已切到 React 路由，再原子替换 index 并校验公网 HTML。若任一步失败即停止。本轮不需重启金融 REST/MCP、不改 .env、不改 nginx、不做数据库迁移。激活后仍需浏览器确认方法列表、参考读取和对话带入。

回退静态入口可恢复 `frontend-before/index.html`；若需回退后端源码，使用正常 Git 回退流程与管理员重启，不覆盖现有配置和运行数据。
