import {
  ArrowRight,
  Activity,
  Check,
  Eye,
  EyeOff,
  LockKeyhole,
  ShieldCheck,
  Smartphone,
  UserRound,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  loadAuthConfig,
  loadAuthSession,
  loginPhoneAccount,
  requestRegistrationCode,
  registerPhoneAccount,
} from "./api";
import "./auth.css";

const PENDING_PROMPT_KEY = "fin_agent.pending_prompt";

type AuthMode = "login" | "register";

interface Props {
  modeOverride?: AuthMode;
}

type AuthConfig = Awaited<ReturnType<typeof loadAuthConfig>>;

function initialMode(): AuthMode {
  if (typeof window === "undefined") return "register";
  return window.location.pathname.startsWith("/login") ? "login" : "register";
}

export default function AuthPage({ modeOverride }: Props) {
  const mode = modeOverride || initialMode();
  const isRegister = mode === "register";
  const [mobile, setMobile] = useState("");
  const [realName, setRealName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [codeBusy, setCodeBusy] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);
  const [challengeId, setChallengeId] = useState("");
  const [challengeMobile, setChallengeMobile] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [mobileMasked, setMobileMasked] = useState("");
  const [codeExpiresIn, setCodeExpiresIn] = useState(0);
  const [resendSeconds, setResendSeconds] = useState(0);
  const pendingPrompt = useMemo(() => {
    if (typeof window === "undefined") return "";
    return String(window.sessionStorage.getItem(PENDING_PROMPT_KEY) || "").trim();
  }, []);

  useEffect(() => {
    let active = true;
    void loadAuthSession()
      .catch(() => ({ authenticated: false, user: null }))
      .then((session) => {
        if (!active) return;
        if (session.authenticated && typeof window !== "undefined") {
          window.location.replace("/assistant");
          return;
        }
        setChecking(false);
      });
    void loadAuthConfig()
      .then((config) => {
        if (active) setAuthConfig(config);
      })
      .catch(() => {
        if (active) {
          setAuthConfig({
            available: false,
            provider: "",
            method: "",
            possession_available: false,
            identity_match_required: false,
          });
        }
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (resendSeconds <= 0) return;
    const timer = window.setInterval(() => {
      setResendSeconds((current) => Math.max(0, current - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [resendSeconds]);

  const identityMatchRequired = Boolean(authConfig?.identity_match_required);
  const possessionAvailable = Boolean(authConfig?.possession_available);
  const registerUnavailable = isRegister && authConfig !== null && !authConfig.available;
  const registerConfigurationPending = isRegister && authConfig === null;
  const compactMobile = mobile.trim().replace(/[\s-]/g, "");
  const mobileReady = /^(?:(?:\+|00)?86)?1[3-9]\d{9}$/.test(compactMobile);
  const registrationCodeReady = Boolean(
    challengeId
    && challengeMobile === mobile.trim()
    && /^\d{6}$/.test(verificationCode),
  );

  const updateMobile = (nextMobile: string) => {
    setMobile(nextMobile);
    if (challengeId && nextMobile.trim() !== challengeMobile) {
      setChallengeId("");
      setChallengeMobile("");
      setVerificationCode("");
      setMobileMasked("");
      setCodeExpiresIn(0);
      setResendSeconds(0);
    }
  };

  const sendRegistrationCode = async () => {
    if (codeBusy || resendSeconds > 0) return;
    if (!mobileReady) {
      setError("请输入有效的中国大陆手机号。");
      return;
    }
    setCodeBusy(true);
    setError("");
    setChallengeId("");
    setChallengeMobile("");
    setVerificationCode("");
    setMobileMasked("");
    setCodeExpiresIn(0);
    try {
      const result = await requestRegistrationCode({ mobile });
      if (!result.challengeId) throw new Error("验证码发送成功，但服务未返回验证凭据，请重新发送。");
      setChallengeId(result.challengeId);
      setChallengeMobile(mobile.trim());
      setVerificationCode("");
      setMobileMasked(result.mobileMasked);
      setCodeExpiresIn(result.expiresInSeconds);
      setResendSeconds(result.resendAfterSeconds);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCodeBusy(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (isRegister && !registrationCodeReady) {
      setError(challengeId ? "请输入收到的 6 位短信验证码。" : "请先发送短信验证码。");
      return;
    }
    if (isRegister && !accepted) {
      setError("请先确认手机号使用与验证说明。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (isRegister) {
        await registerPhoneAccount({
          ...(identityMatchRequired ? { realName } : {}),
          mobile,
          challengeId,
          verificationCode,
          password,
          confirmPassword,
        });
      } else {
        await loginPhoneAccount({ mobile, password });
      }
      if (typeof window !== "undefined") window.location.assign("/assistant");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth-page">
      <div className="auth-orbit auth-orbit-one" aria-hidden="true" />
      <div className="auth-orbit auth-orbit-two" aria-hidden="true" />
      <a className="auth-brand" href="/" aria-label="返回 Fin Agent 首页">
        <span><Activity size={18} /></span>
        <strong>Fin Agent</strong><small>Financial Intelligence</small>
      </a>

      <section className="auth-story" aria-labelledby="auth-story-title">
        <div className="auth-kicker">SECURE AGENT WORKSPACE</div>
        <h1 id="auth-story-title">
          进入你的金融智能空间，
          <span>继续未完成的研究。</span>
        </h1>
        <p>
          行情、财务与研报查询，策略构建、历史回测和个人 Skill，
          在同一段连贯对话里逐步完成。
        </p>
        <div className="auth-assurances" aria-label="平台特性">
          <div><ShieldCheck size={18} /><span><strong>数据有时点</strong>明确交易日与数据口径</span></div>
          <div><Check size={18} /><span><strong>结论有依据</strong>保留来源与关键过程</span></div>
          <div><LockKeyhole size={18} /><span><strong>资产有边界</strong>账户隔离个人对话与策略</span></div>
        </div>
      </section>

      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-card-head">
          <div className="auth-step"><span>01</span><i /></div>
          <p>{isRegister ? "创建你的金融工作台" : "欢迎回到金融工作台"}</p>
          <h2 id="auth-title">{isRegister ? "免费注册" : "手机号登录"}</h2>
          <span>
            {isRegister
              ? identityMatchRequired
                ? "手机号是账户的唯一登录标识。短信验证持有权后，将单独核验姓名与手机号。"
                : "手机号是账户的唯一登录标识，短信验证码用于确认你持有该号码。"
              : "使用注册手机号和密码继续你的研究。"}
          </span>
        </div>

        {pendingPrompt && (
          <aside className="auth-pending" aria-label="待继续的问题">
            <small>注册后继续</small>
            <p>{pendingPrompt}</p>
          </aside>
        )}

        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          <div className={isRegister ? "auth-phone-verification" : undefined}>
            <label>
              <span>手机号</span>
              <div className="auth-field">
                <Smartphone size={18} />
                <input
                  value={mobile}
                  onChange={(event) => updateMobile(event.target.value)}
                  type="tel"
                  inputMode="numeric"
                  autoComplete="tel"
                  placeholder="请输入中国大陆手机号"
                  maxLength={18}
                  disabled={codeBusy}
                  required
                />
              </div>
            </label>
            {isRegister && (
              <button
                className="auth-send-code"
                type="button"
                onClick={() => void sendRegistrationCode()}
                disabled={
                  codeBusy
                  || checking
                  || registerUnavailable
                  || registerConfigurationPending
                  || !possessionAvailable
                  || resendSeconds > 0
                  || !mobileReady
                }
                aria-describedby="auth-code-guidance"
              >
                {codeBusy
                  ? "发送中…"
                  : resendSeconds > 0
                    ? `${resendSeconds} 秒后重发`
                    : challengeId
                      ? "重新发送"
                      : "发送验证码"}
              </button>
            )}
          </div>

          {isRegister && (
            <label>
              <span>短信验证码</span>
              <div className="auth-field">
                <ShieldCheck size={18} />
                <input
                  value={verificationCode}
                  onChange={(event) => {
                    setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6));
                    setError("");
                  }}
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder={challengeId ? "请输入 6 位验证码" : "请先获取验证码"}
                  aria-describedby="auth-code-guidance"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  disabled={!challengeId}
                  required
                />
              </div>
              <small
                id="auth-code-guidance"
                className={challengeId ? "auth-code-guidance is-sent" : "auth-code-guidance"}
                aria-live="polite"
              >
                {challengeId
                  ? `验证码已发送至 ${mobileMasked || "该手机号"}，约 ${Math.max(1, Math.ceil(codeExpiresIn / 60))} 分钟内有效。`
                  : "发送验证码后，再完成密码设置。"}
              </small>
            </label>
          )}

          {isRegister && identityMatchRequired && (
            <label>
              <span>真实姓名</span>
              <div className="auth-field">
                <UserRound size={18} />
                <input
                  value={realName}
                  onChange={(event) => setRealName(event.target.value)}
                  autoComplete="name"
                  placeholder="仅用于注册时的运营商实名核验"
                  maxLength={50}
                  required
                />
              </div>
            </label>
          )}

          <label>
            <span>密码</span>
            <div className="auth-field">
              <LockKeyhole size={18} />
              <input
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type={showPassword ? "text" : "password"}
                autoComplete={isRegister ? "new-password" : "current-password"}
                placeholder={isRegister ? "至少 8 个字符" : "请输入密码"}
                minLength={8}
                maxLength={128}
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword((current) => !current)}
                aria-label={showPassword ? "隐藏密码" : "显示密码"}
              >
                {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </div>
          </label>
          {isRegister && (
            <label>
              <span>确认密码</span>
              <div className="auth-field">
                <LockKeyhole size={18} />
                <input
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
                  placeholder="再次输入密码"
                  minLength={8}
                  maxLength={128}
                  required
                />
              </div>
            </label>
          )}

          {isRegister && (
            <label className="auth-consent">
              <input
                type="checkbox"
                checked={accepted}
                onChange={(event) => setAccepted(event.target.checked)}
              />
              <span>
                我确认有权使用该手机号，并同意通过短信验证码确认手机号持有权
                {identityMatchRequired ? "，以及在注册时进行姓名与手机号实名核验。" : "。"}
              </span>
            </label>
          )}

          {registerUnavailable && (
            <div className="auth-notice" role="status">
              {possessionAvailable
                ? "账户注册服务尚未完成配置，暂时无法创建新账户。"
                : "短信验证服务尚未完成配置，暂时无法创建新账户。"}
            </div>
          )}
          {error && <div className="auth-error" role="alert">{error}</div>}

          <button
            className="auth-submit"
            type="submit"
            disabled={
              busy
              || codeBusy
              || checking
              || registerUnavailable
              || registerConfigurationPending
              || (isRegister && !registrationCodeReady)
            }
          >
            <span>{busy ? "正在提交…" : isRegister ? "验证并创建账户" : "登录工作台"}</span>
            <ArrowRight size={18} />
          </button>
        </form>

        <div className="auth-switch">
          {isRegister ? "已经有账户？" : "第一次使用 Fin Agent？"}
          <a href={isRegister ? "/login" : "/register"}>
            {isRegister ? "直接登录" : "免费注册"}
          </a>
        </div>
        <a className="auth-guest" href="/assistant">先以访客身份体验</a>
        <p className="auth-footnote">
          {isRegister
            ? <>
                短信验证码仅用于确认手机号持有权；
                {identityMatchRequired ? "实名信息仅用于注册核验；" : ""}
                Fin Agent 不会在页面公开你的完整手机号。
              </>
            : "Fin Agent 不会在页面公开你的完整手机号。"}
        </p>
      </section>
    </main>
  );
}
