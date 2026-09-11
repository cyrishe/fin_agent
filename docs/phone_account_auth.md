# 公开主页与手机号账户

Fin Agent 的公开入口保持一条连续主线：

`主页自然语言意图（SOFT） → 手机号唯一账户与登录会话（HARD） → 工作台继续原问题（SOFT）`

主页输入的问题会暂存在浏览器当前会话的
`fin_agent.pending_prompt`。注册或登录后进入 `/assistant`，原问题会回填到输入框。
手机号不会作为内部 `user_id`：系统继续使用不含个人信息的随机 `user_*`
标识，手机号只作为 `aiia_user_identity` 中唯一的外部主身份。

## 认证边界

手机号唯一注册必须先证明用户此刻持有该号码。注册主线为：

1. 用户输入手机号，服务端创建一次性 challenge。
2. 通过阿里云号码认证服务 `SendSmsVerifyCode` 生成并发送六位验证码。
3. 用户提交 challenge、手机号、验证码和密码。
4. 服务端通过 `CheckSmsVerifyCode` 校验验证码；创建手机号身份、密码凭据、初始登录 session，并消费
   challenge。四项写入在同一个数据库事务中完成。
5. 后续登录只使用手机号和密码，不重复发送短信。

`../personal_news_agent` 中已有的阿里云能力是 `Mobile2MetaVerify`：它只判断
姓名与手机号的运营商实名记录是否一致，不能证明请求者持有手机。因此它只保留为
可选的第二层核验，绝不替代短信验证码。设置
`FIN_AGENT_PHONE_IDENTITY_MATCH_REQUIRED=1` 后，注册页才会要求真实姓名。

## Challenge 的隐私和滥用防护

`aiia_phone_verification_challenge` 不保存手机号或验证码明文：

- 手机号、请求 IP 和验证码均使用独立 challenge secret 做 HMAC-SHA256。
- 验证码有过期时间、重发冷却和最大尝试次数。
- MySQL advisory lock 对手机号和 IP 做跨 worker single-flight。
- 发送频率由数据库记录统一限制，不依赖单进程内存。
- 全局每小时/每日短信预算限制分布式手机号和 IP 轮换造成的费用风险。
- `verified_at` 只表示验证码正确；`consumed_at` 必须与账户创建在同一事务写入。

会员 session Cookie 为随机 HttpOnly/SameSite=Lax Cookie，数据库只保存 token 的
SHA-256 摘要。登录保护使用同一张数据库事件表表达两种口径：

- 手机号/IP 失败预算只统计 `succeeded_at IS NULL`，成功后不惩罚用户。
- 更粗的 IP 全请求预算和每类认证操作的全局熔断统计窗口内所有事件；即使密码
  正确，也不能无限触发 PBKDF2 或创建新 session。

三个维度在写入事件前由排序后的 MySQL advisory lock 原子保护，随后立即释放连接
和锁；密码计算、session 写入和外部 provider 调用都不占用这段数据库资源。session
存储不可用时请求返回 503，不会静默降级为访客身份。

## 数据库

在 `SYSTEM_DB_URL` 指向的系统库按顺序执行：

- `docs/sql/create_aiia_user.sql`
- `docs/sql/create_aiia_user_identity.sql`
- `docs/sql/create_aiia_user_credential.sql`
- `docs/sql/create_aiia_user_session.sql`
- `docs/sql/create_aiia_phone_verification_challenge.sql`
- `docs/sql/create_aiia_auth_attempt.sql`

真正的手机号唯一性由
`aiia_user_identity.uk_identity_type_value(identity_type, identity_value)`
保证。应用层预检查只负责避免无意义的短信或第三方调用。

当前实现只提供迁移 SQL，不会在应用启动时自动修改生产数据库。

## 环境变量

最小生产配置：

```dotenv
FIN_AGENT_PHONE_CHALLENGE_PROVIDER=aliyun_pnvs
FIN_AGENT_PHONE_CHALLENGE_SECRET=<至少 32 字节的随机 secret>
FIN_AGENT_PHONE_CHALLENGE_GLOBAL_HOURLY_LIMIT=500
FIN_AGENT_PHONE_CHALLENGE_GLOBAL_DAILY_LIMIT=2000
FIN_AGENT_SMS_ALIYUN_ACCESS_KEY_ID=
FIN_AGENT_SMS_ALIYUN_ACCESS_KEY_SECRET=
FIN_AGENT_PNVS_SIGN_NAME=<号码认证控制台显示的系统签名>
FIN_AGENT_PNVS_TEMPLATE_CODE=100001
FIN_AGENT_PNVS_SCHEME_NAME=fin-agent-register
FIN_AGENT_AUTH_RATE_SECRET=<至少 32 字节的随机 secret>
FIN_AGENT_AUTH_REQUEST_IP_LIMIT=20
FIN_AGENT_AUTH_REQUEST_GLOBAL_LIMIT=120
FIN_AGENT_AUTH_REQUEST_WINDOW_SECONDS=60
FIN_AGENT_COOKIE_SECURE=1
```

号码认证服务使用系统登录/注册模板 `100001`，验证码由阿里云生成并由阿里云
校验，应用不读取或保存验证码明文。启用前需在号码认证控制台开通“短信认证”和
“融合认证”，并授予运行身份 `dypns:SendSmsVerifyCode` 与
`dypns:CheckSmsVerifyCode`。普通 `aliyun` provider 仍作为已有自定义签名/模板
场景的兼容选项。
AK 兼容阿里云标准环境变量，以及 `personal_news_agent` 使用的
`AccessKeyID` / `AccessKeySecret` 名称；应用不会自动跨项目读取另一个 `.env`，
应由部署环境显式注入。

本地 mock 必须同时显式设置：

```dotenv
FIN_AGENT_PHONE_CHALLENGE_PROVIDER=mock
FIN_AGENT_PHONE_CHALLENGE_MOCK_ENABLED=1
FIN_AGENT_PHONE_CHALLENGE_SECRET=<本地随机 secret>
FIN_AGENT_AUTH_RATE_SECRET=<本地随机 secret>
```

可选实名增强：

```dotenv
FIN_AGENT_PHONE_IDENTITY_MATCH_REQUIRED=1
FIN_AGENT_PHONE_VERIFY_PROVIDER=aliyun
FIN_AGENT_PHONE_VERIFY_ALIYUN_ACCESS_KEY_ID=
FIN_AGENT_PHONE_VERIFY_ALIYUN_ACCESS_KEY_SECRET=
```

## API

- `GET /api/auth/config`：公开的注册能力状态，不返回密钥。
- `GET /api/auth/session`：当前会员登录态，不创建访客身份。
- `POST /api/auth/registration-code`：为手机号发送注册验证码。
- `POST /api/auth/register`：challenge、验证码、手机号和密码注册。
- `POST /api/auth/login`：手机号和密码登录。
- `POST /api/auth/logout`：服务端撤销 session 后再清除 Cookie。

所有认证响应使用 `Cache-Control: no-store, private`；认证 JSON 有 32 KiB 上限，
第三方原始异常、完整手机号、真实姓名和 session token 均不会返回浏览器。

公网开放前还应在网关或注册入口接入 CAPTCHA/设备风险令牌；数据库全局预算是费用
熔断线，不替代机器人识别。运维应按隐私保留期定时删除过期 challenge 和旧登录
尝试，例如只保留 30 天，并对全局预算触发配置告警。
