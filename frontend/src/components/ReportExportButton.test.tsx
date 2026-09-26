import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ReportExportButton, { reportPdfExportEnabled, reportPdfUrl } from "./ReportExportButton";

describe("ReportExportButton", () => {
  it("only enables PDF export for an explicitly exportable report", () => {
    expect(reportPdfExportEnabled({ report_export: { pdf: true } })).toBe(true);
    expect(reportPdfExportEnabled({ report_export: { pdf: false } })).toBe(false);
    expect(reportPdfExportEnabled({})).toBe(false);
  });

  it("builds an owned-turn report URL and renders a download link", () => {
    expect(reportPdfUrl(23, 41)).toBe("/api/assistant/results/report.pdf?thread_id=23&turn_id=41");
    const html = renderToStaticMarkup(
      <ReportExportButton
        payload={{ report_export: { pdf: true, label: "下载 PDF" } }}
        threadId={23}
        turnId={41}
      />,
    );
    expect(html).toContain("研究报告");
    expect(html).toContain("下载 PDF");
    expect(html).toContain("thread_id=23&amp;turn_id=41");
    expect(html).toContain("download=\"\"");
  });

  it("does not render before the persisted turn identity is available", () => {
    const html = renderToStaticMarkup(
      <ReportExportButton payload={{ report_export: { pdf: true } }} />,
    );
    expect(html).toBe("");
  });
});
