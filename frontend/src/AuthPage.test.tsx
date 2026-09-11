import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { registerPhoneAccount, requestRegistrationCode } from "./api";
import AuthPage from "./AuthPage";

describe("AuthPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses a phone-possession challenge before account registration", () => {
    const html = renderToStaticMarkup(<AuthPage modeOverride="register" />);

    expect(html).toContain("免费注册");
    expect(html).toContain("短信验证码用于确认你持有该号码");
    expect(html).toContain("发送验证码");
    expect(html).toContain("短信验证码");
    expect(html).toContain("请先获取验证码");
    expect(html).toContain("验证并创建账户");
    expect(html).not.toContain("真实姓名");
  });

  it("uses the registered mobile and password for returning users", () => {
    const html = renderToStaticMarkup(<AuthPage modeOverride="login" />);

    expect(html).toContain("手机号登录");
    expect(html).toContain("使用注册手机号和密码");
    expect(html).not.toContain("真实姓名");
    expect(html).not.toContain("发送验证码");
  });

  it("maps the registration-code response into the frontend challenge contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      challenge_id: "challenge-1",
      mobile_masked: "138****8000",
      expires_in_seconds: 300,
      resend_after_seconds: 60,
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(requestRegistrationCode({ mobile: "13800138000" })).resolves.toEqual({
      challengeId: "challenge-1",
      mobileMasked: "138****8000",
      expiresInSeconds: 300,
      resendAfterSeconds: 60,
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/registration-code", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ mobile: "13800138000" }),
    }));
  });

  it("submits the challenge and code without inventing optional real-name data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      authenticated: true,
      user: null,
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await registerPhoneAccount({
      mobile: "13800138000",
      challengeId: "challenge-1",
      verificationCode: "123456",
      password: "password-1",
      confirmPassword: "password-1",
    });

    const options = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/auth/register");
    expect(JSON.parse(String(options.body))).toEqual({
      mobile: "13800138000",
      challenge_id: "challenge-1",
      verification_code: "123456",
      password: "password-1",
      confirm_password: "password-1",
    });
  });
});
