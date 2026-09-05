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
