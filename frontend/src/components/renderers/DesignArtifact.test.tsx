import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import DesignArtifact from "./DesignArtifact";

describe("DesignArtifact", () => {
  it("renders the natural-language design as markdown without repeating the requirement", () => {
    const html = renderToStaticMarkup(<DesignArtifact
      content=""
      data={{
        details: {
          understanding: { requirement_brief: "这是一段很长的需求原文，不应作为设计标题再次展示。" },
          document: "## 工具概述\n判断股票是否满足条件。\n\n| 输入 | 输出 |\n| --- | --- |\n| 股票代码 | 判断结果 |",
        },
      }}
    />);

    expect(html).toContain("<h2>工具概述</h2>");
    expect(html).toContain("<table>");
    expect(html).not.toContain("## 工具概述");
    expect(html).not.toContain("这是一段很长的需求原文");
    expect(html).not.toContain("查看完整设计细节");
  });
});
