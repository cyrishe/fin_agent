import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import MarkdownContent from "./MarkdownContent";

describe("MarkdownContent", () => {
  it("preserves multiple financial ranges in prose and comparison tables", () => {
    const html = renderToStaticMarkup(<MarkdownContent content={
      "营收 +10~17%、归母 +14~25%。\n\n| 指标 | 区间 |\n|---|---|\n| PE | 20~30 |\n| PB | 2~3 |"
    } />);
    expect(html).toContain("营收 +10~17%、归母 +14~25%");
    expect(html).toContain("20~30");
    expect(html).toContain("2~3");
    expect(html).not.toContain("<del>");
    expect(html).toContain('aria-label="分析对比表"');
  });

  it("keeps deliberate double-tilde deletions and inline code", () => {
    const html = renderToStaticMarkup(<MarkdownContent content="~~旧观点~~ 新观点；`a~b`" />);
    expect(html).toContain("<del>旧观点</del>");
    expect(html).toMatch(/<code[^>]*>a~b<\/code>/);
  });
});
