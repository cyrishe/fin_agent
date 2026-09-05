import { Download, FileText } from "lucide-react";
import type { UnknownRecord } from "../types";

const asRecord = (value: unknown): UnknownRecord => value && typeof value === "object" && !Array.isArray(value)
  ? value as UnknownRecord
  : {};

export function reportPdfExportEnabled(payload: unknown): boolean {
  return asRecord(asRecord(payload).report_export).pdf === true;
}

export function reportPdfUrl(threadId: number, turnId: number): string {
  const query = new URLSearchParams({
    thread_id: String(threadId),
    turn_id: String(turnId),
  });
  return `/api/assistant/results/report.pdf?${query.toString()}`;
}

export default function ReportExportButton({ payload, threadId, turnId }: {
  payload?: UnknownRecord;
  threadId?: number;
  turnId?: number;
}) {
  if (!reportPdfExportEnabled(payload) || !threadId || !turnId) return null;
  const exportConfig = asRecord(asRecord(payload).report_export);
  const label = String(exportConfig.label || "下载 PDF").trim() || "下载 PDF";
  return (
    <div className="report-export-row">
      <div className="report-export-copy">
        <FileText size={15} />
        <span><strong>研究报告</strong><small>保留当前结论、证据口径与数据时点</small></span>
      </div>
      <a href={reportPdfUrl(threadId, turnId)} download>
        <Download size={14} />{label}
      </a>
    </div>
  );
}
