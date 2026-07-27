import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import RunPanel from "./RunPanel";

describe("RunPanel", () => {
  it("renders structured process output as formatted JSON instead of paragraph text", () => {
    const html = renderToStaticMarkup(<RunPanel run={{
      status: "running",
      summary: "正在实现工具",
      artifacts: [],
      process: [{
        block_id: "coding_structured_tool_output",
        block_type: "code",
        title: "结构化执行结果",
        data: {
          role: "process",
          summary: "已返回结构化执行结果。",
          format: "json",
          value: { ok: true, count: 2 },
        },
      }],
    }} />);

    expect(html).toContain("结构化结果");
    expect(html).toContain('class="json-block compact"');
    expect(html).toContain("&quot;ok&quot;: true");
    expect(html).toContain("&quot;count&quot;: 2");
    expect(html).not.toContain('>{&quot;ok&quot;:true');
  });
});
