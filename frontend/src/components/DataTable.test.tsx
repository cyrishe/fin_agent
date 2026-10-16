import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import DataTable from "./DataTable";

describe("DataTable", () => {
  it("shows catalog labels while retaining raw column keys and values", () => {
    const html = renderToStaticMarkup(<DataTable data={{
      columns: ["tradedate", "financing_balance"],
      column_labels: {
        tradedate: "交易日期",
        financing_balance: "融资余额",
      },
      column_meta: {
        financing_balance: { unit: "元" },
      },
      rows: [{
        tradedate: "2026-07-27",
        financing_balance: 9123456789,
      }],
      row_count: 2,
    }} />);

    expect(html).toContain("交易日期");
    expect(html).toContain("融资余额");
    expect(html).toContain("2026-07-27");
    expect(html).toContain("91.23亿元");
    expect(html).toContain("预览 1 / 已返回 2 行");
    expect(html).not.toContain(">financing_balance<");
  });

  it("collapses long report text while keeping the full evidence available", () => {
    const longText = "产业链本土化切换带来结构性增长机会。".repeat(12);
    const html = renderToStaticMarkup(<DataTable data={{
      columns: ["investment_highlights"],
      column_labels: { investment_highlights: "投资要点" },
      rows: [{ investment_highlights: longText }],
    }} />);

    expect(html).toContain('class="table-cell-detail"');
    expect(html).toContain("<summary>");
    expect(html).toContain(longText);
  });

  it("uses ten-row pages for complete local result sets", () => {
    const html = renderToStaticMarkup(<DataTable data={{
      columns: ["name"],
      rows: Array.from({ length: 15 }, (_, index) => ({ name: `股票${index + 1}` })),
      row_count: 15,
    }} />);

    expect(html).toContain("股票10");
    expect(html).not.toContain("股票11");
    expect(html).toContain("第 1 / 2 页");
    expect(html).toContain('aria-label="下一页"');
  });

  it("exposes remote pagination controls without pretending the sample is complete", () => {
    const html = renderToStaticMarkup(<DataTable data={{
      columns: ["name"],
      rows: [{ name: "药明康德" }, { name: "海康威视" }, { name: "洛阳钼业" }],
      row_count: 50,
      returned_row_count: 50,
      page_size: 10,
      thread_id: 2640,
      data_ref: "session://example/vars/v1",
    }} />);

    expect(html).toContain("第 1 / 5 页");
    expect(html).toContain("当前 3 行");
    expect(html).toContain("已返回 50 行");
    expect(html).toContain('aria-label="下一页"');
    expect(html).not.toContain("共 50 行");
  });
});
