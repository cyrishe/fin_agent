import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import LandingPage, {
  continueToRegistration,
  PENDING_PROMPT_STORAGE_KEY,
} from "./LandingPage";

describe("LandingPage", () => {
  it("renders a usable prompt form and accessible product demo", () => {
    const html = renderToStaticMarkup(<LandingPage />);

    expect(html).toContain("<textarea");
    expect(html).toContain('id="landing-prompt"');
    expect(html).toContain('aria-label="开始使用 Fin Agent"');
    expect(html).toContain('role="tablist"');
    expect(html.match(/role="tab"/g)).toHaveLength(4);
    expect(html).toContain('aria-selected="true"');
    expect(html).toContain('role="tabpanel"');
    expect(html).toContain("金融查询与问答");
    expect(html).toContain("自然交互的策略平台");
    expect(html).toContain("回测与策略管理");
    expect(html).toContain("强大的 Skill 平台");
    expect(html).toContain("页面示例不构成投资建议");
  });

  it("persists a normalized prompt before navigating to registration", () => {
    const setItem = vi.fn();
    const navigate = vi.fn();

    const submitted = continueToRegistration(
      "  回测这组股票过去三年的表现  ",
      { setItem },
      navigate,
    );

    expect(submitted).toBe(true);
    expect(setItem).toHaveBeenCalledWith(
      PENDING_PROMPT_STORAGE_KEY,
      "回测这组股票过去三年的表现",
    );
    expect(navigate).toHaveBeenCalledWith("/register");
    expect(setItem.mock.invocationCallOrder[0]).toBeLessThan(navigate.mock.invocationCallOrder[0]);
  });

  it("does not persist or navigate when the prompt is empty", () => {
    const setItem = vi.fn();
    const navigate = vi.fn();

    const submitted = continueToRegistration("   ", { setItem }, navigate);

    expect(submitted).toBe(false);
    expect(setItem).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });
});
