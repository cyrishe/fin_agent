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

  it("uses each milestone status instead of treating every prior step as successful", () => {
    const html = renderToStaticMarkup(<RunPanel run={{
      status: "running",
      summary: "融资数据查询未完成",
      artifacts: [],
      process: [
        {
          block_id: "understanding",
          block_type: "status",
          title: "问题理解",
          data: { role: "process", status: "completed", summary: "已确认口径" },
        },
        {
          block_id: "query",
          block_type: "status",
          title: "数据查询",
          data: { role: "process", status: "error", summary: "数据源查询未完成" },
        },
      ],
    }} />);

    expect(html).toContain("process-item completed");
    expect(html).toContain("process-item error");
    expect(html).not.toContain('class="spinner"');
  });

  it("settles stale historical progress and renders results as navigation buttons", () => {
    const html = renderToStaticMarkup(<RunPanel onArtifactSelect={() => undefined} run={{
      status: "done",
      summary: "本轮处理完成",
      artifacts: [
        { block_id: "design_artifact", block_type: "artifact", title: "设计方案" },
        { block_id: "design_review", block_type: "interaction", title: "确认设计", data: { actions: [{ action_id: "confirm" }] } },
      ],
      process: [
        { block_id: "design_custom_tool_understanding", block_type: "status", title: "工具需求与设计", data: { role: "process", status: "running", summary: "正在形成设计。" } },
        { block_id: "runtime_custom_tool_understanding", block_type: "status", title: "工具需求与设计", data: { role: "process", status: "completed", summary: "设计已经形成。" } },
      ],
    }} />);

    expect(html).toContain("等待你的确认");
    expect(html).toContain("结果与下一步");
    expect(html.match(/工具需求与设计/g)).toHaveLength(1);
    expect(html).not.toContain('class="spinner"');
    expect(html).toContain('<button type="button" aria-label="定位到设计方案"');
    expect(html).toContain("待确认");
  });
});
