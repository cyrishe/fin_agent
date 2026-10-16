import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { hydrateMessages } from "./App";
import RunPanel from "./components/RunPanel";

describe("historical conversation", () => {
  it("keeps a completed answer in the conversation and only status in the run panel", () => {
    const answer = "## 估值\n营收 +10~17%、归母 +14~25%。";
    const messages = hydrateMessages([{ status: "completed", user_input_text: "分析", assistant_output_text: answer }], 42);
    const assistant = messages[1];
    expect(assistant.content).toBe(answer);
    expect(assistant.threadId).toBe(42);
    const panel = renderToStaticMarkup(<RunPanel run={assistant.run} />);
    expect(panel).toContain("本轮处理完成");
    expect(panel).not.toContain("营收");
    expect(panel).not.toContain("##");
  });

  it("preserves the actual failure in historical error feedback", () => {
    const [assistant] = hydrateMessages([{ status: "failed", assistant_output_text: "数据源连接失败，请稍后重试。" }], 42);
    expect(assistant.run?.status).toBe("error");
    expect(assistant.content).toBe("数据源连接失败，请稍后重试。");
    expect(assistant.run?.summary).toBe("数据源连接失败，请稍后重试。");
  });
});
