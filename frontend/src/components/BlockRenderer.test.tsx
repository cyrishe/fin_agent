import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import BlockRenderer from "./BlockRenderer";

describe("resource list renderer", () => {
  it("opens internal Skill Studio resources as links", () => {
    const html = renderToStaticMarkup(<BlockRenderer block={{
      block_id: "skills",
      block_type: "resource",
      data: {
        resources: [{
          resource_id: "skill:business_method:stock-research",
          title: "个股深度研究",
          relation: "Finance CC 专业方法",
          uri: "/skills/studio/stock-research",
        }],
      },
    }} />);

    expect(html).toContain('href="/skills/studio/stock-research"');
    expect(html).toContain("查看");
  });

  it("does not turn unsafe resource schemes into links", () => {
    const html = renderToStaticMarkup(<BlockRenderer block={{
      block_id: "unsafe-resource",
      block_type: "resource",
      data: {
        resources: [{
          resource_id: "unsafe",
          title: "Unsafe",
          uri: "javascript:alert(1)",
        }, {
          resource_id: "protocol-relative",
          title: "Protocol relative",
          uri: "//example.com/phishing",
        }],
      },
    }} />);

    expect(html).not.toContain("href=");
    expect(html).toContain("javascript:alert(1)");
    expect(html).toContain("//example.com/phishing");
  });
});

describe("validation evidence renderer", () => {
  it("shows the expectation basis, expected value, actual value, and key metrics", () => {
    const html = renderToStaticMarkup(<BlockRenderer block={{
      block_id: "custom_tool_test_result",
      block_type: "assessment",
      data: {
        overall: "pass",
        summary: "3 / 3 项代表性样例技术运行成功",
        details: {
          tests: [{
            name: "边界样例",
            status: "passed",
            summary: "核对阈值等于 1.5 时的判断",
            input: { volume: 150, average_volume: 100 },
            expected: { matched: true },
            expected_basis: "按已保存规则手算：150 / 100 = 1.5",
            actual: {
              matched: true,
              key_process_info: { volume_ratio: 1.5, threshold: 1.5 },
            },
          }],
        },
      },
    }} />);

    expect(html).toContain("预期依据");
    expect(html).toContain("按已保存规则手算");
    expect(html).toContain("独立预期");
    expect(html).toContain("实际结果");
    expect(html).toContain("核心过程信息");
    expect(html).toContain("volume ratio");
    expect(html).toContain("threshold");
  });
});
