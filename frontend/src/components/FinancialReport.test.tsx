import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import BlockRenderer from "./BlockRenderer";

describe("shared financial report", () => {
  it("renders chapters and a saved figure alongside its interpretation with table scrolling", () => {
    const html = renderToStaticMarkup(<BlockRenderer block={{ block_id: "answer", block_type: "narrative", semantic: "finance.answer", payload: {
      text: "fallback", report: { title: "个股研究", introduction: "核心结论", sections: [
        { id: "valuation", title: "估值", content: "| 项目 | 数值 |\n|---|---|\n| PE | 10 |" },
        { id: "technical", title: "技术面", content: "均线下方整理。\n\n![近期走势](finance-figure:fig_a)" },
        { id: "risks", title: "风险", content: "观察量能。" },
      ], figures: [{ id: "fig_a", title: "日K", code: "000001.SZ", as_of: "2026-09-29", image_url: "data:image/png;base64,aGVsbG8=" }] },
    } }} />);
    expect(html).toContain('aria-label="报告章节"');
    expect(html).toContain('class="markdown-table-scroll"');
    expect(html).toContain('alt="近期走势"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("放大图片");
    expect(html).toContain("000001.SZ · 2026-09-29");
    expect(html.indexOf('alt="近期走势"')).toBeLessThan(html.indexOf(">风险</h2>"));
    expect(html).not.toContain("finance-figure:");
    expect(html).not.toContain("fallback");
    expect(html.match(/<img /g)).toHaveLength(1);
  });
  it("keeps brief answers simple and never renders an arbitrary report image URL", () => {
    const html = renderToStaticMarkup(<BlockRenderer block={{ block_id: "answer", block_type: "narrative", semantic: "finance.answer", payload: {
      report: { introduction: "简短回答\n\n![未登记图片](https://untrusted.test/image.png)", sections: [], figures: [{ id: "bad", image_url: "javascript:alert(1)" }] },
    } }} />);
    expect(html).toContain("简短回答");
    expect(html).not.toContain("<nav");
    expect(html).not.toContain("<img");
  });
});
