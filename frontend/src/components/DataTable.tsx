import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown, LoaderCircle } from "lucide-react";
import { loadResultPage } from "../api";
import { formatFinancialValue } from "../rendering/formatFinancialValue";

type Row = Record<string, unknown> | unknown[];
type LoadState = "idle" | "loading" | "ready" | "error";

export default function DataTable({ data }: { data: Record<string, unknown> }) {
  const columnLabels = useMemo(
    () => data.column_labels && typeof data.column_labels === "object" && !Array.isArray(data.column_labels)
      ? data.column_labels as Record<string, unknown>
      : {},
    [data.column_labels],
  );
  const columnMeta = useMemo(
    () => data.column_meta && typeof data.column_meta === "object" && !Array.isArray(data.column_meta)
      ? data.column_meta as Record<string, Record<string, unknown>>
      : {},
    [data.column_meta],
  );
  const headers = useMemo(() => {
    const configured = Array.isArray(data.headers) ? data.headers : data.columns;
    if (Array.isArray(configured)) return configured.map(String);
    const first = Array.isArray(data.rows) ? data.rows[0] : null;
    return first && typeof first === "object" && !Array.isArray(first) ? Object.keys(first) : [];
  }, [data]);
  const rows = useMemo(() => Array.isArray(data.rows) ? data.rows as Row[] : [], [data.rows]);
  const returnedRows = Number.isFinite(Number(data.returned_row_count ?? data.row_count))
    ? Math.max(rows.length, Number(data.returned_row_count ?? data.row_count))
    : rows.length;
  const configuredPageSize = Number(data.page_size);
  const pageSize = Number.isFinite(configuredPageSize)
    ? Math.max(1, Math.min(100, Math.floor(configuredPageSize)))
    : 10;
  const threadId = Number(data.thread_id);
  const dataRef = String(data.data_ref || "").trim();
  const canLoadRemote = threadId > 0 && dataRef.startsWith("session://") && returnedRows > rows.length;
  const pageCount = Math.max(1, Math.ceil(returnedRows / pageSize));

  const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" } | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [remoteRows, setRemoteRows] = useState<Row[] | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");

  const fetchRemotePage = useCallback(async (nextPage: number) => {
    if (!canLoadRemote) return;
    setLoadState("loading");
    try {
      const result = await loadResultPage({
        threadId,
        dataRef,
        offset: nextPage * pageSize,
        limit: pageSize,
      });
      setRemoteRows(result.rows);
      setPageIndex(nextPage);
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, [canLoadRemote, dataRef, pageSize, threadId]);

  useEffect(() => {
    setPageIndex(0);
    setRemoteRows(null);
    setLoadState("idle");
    if (canLoadRemote) void fetchRemotePage(0);
  }, [canLoadRemote, dataRef, fetchRemotePage]);

  const sortedRows = useMemo(() => {
    const sorted = [...rows];
    if (sort && !canLoadRemote) {
      const index = headers.indexOf(sort.key);
      sorted.sort((left, right) => {
        const a = Array.isArray(left) ? left[index] : left[sort.key];
        const b = Array.isArray(right) ? right[index] : right[sort.key];
        const numeric = Number(a) - Number(b);
        const result = Number.isNaN(numeric) ? String(a ?? "").localeCompare(String(b ?? ""), "zh-CN") : numeric;
        return sort.direction === "asc" ? result : -result;
      });
    }
    return sorted;
  }, [canLoadRemote, headers, rows, sort]);

  const visibleRows = useMemo(() => {
    if (canLoadRemote) return remoteRows ?? rows;
    const start = pageIndex * pageSize;
    return sortedRows.slice(start, start + pageSize);
  }, [canLoadRemote, pageIndex, pageSize, remoteRows, rows, sortedRows]);

  if (!headers.length || !rows.length) return <div className="empty-block">暂无表格数据</div>;

  const valueAt = (row: Row, key: string, index: number) => Array.isArray(row) ? row[index] : row[key];
  const format = (value: unknown, key: string) => {
    if (value == null) return "—";
    if (typeof value === "boolean") return value ? "是" : "否";
    if (typeof value === "number") {
      return formatFinancialValue(value, String(columnMeta[key]?.unit || ""), 6);
    }
    const text = typeof value === "object" ? JSON.stringify(value) : String(value);
    if (text.length > 120) {
      return <details className="table-cell-detail">
        <summary>{text.slice(0, 54)}…</summary>
        <div>{text}</div>
      </details>;
    }
    return text;
  };

  const changeSort = (key: string) => {
    if (canLoadRemote) return;
    setPageIndex(0);
    setSort((current) => current?.key === key
      ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
      : { key, direction: "asc" });
  };

  const changePage = (nextPage: number) => {
    const bounded = Math.max(0, Math.min(pageCount - 1, nextPage));
    if (canLoadRemote) void fetchRemotePage(bounded);
    else setPageIndex(bounded);
  };

  const footerSummary = canLoadRemote
    ? `第 ${pageIndex + 1} / ${pageCount} 页 · 当前 ${visibleRows.length} 行 · 已返回 ${returnedRows} 行`
    : returnedRows > rows.length
      ? `预览 ${rows.length} / 已返回 ${returnedRows} 行`
      : pageCount > 1
        ? `第 ${pageIndex + 1} / ${pageCount} 页 · ${returnedRows} 行`
        : `${returnedRows} 行`;

  return (
    <div className="table-shell">
      <div className="table-scroll" tabIndex={0} aria-label="表格内容，可横向和纵向滚动">
        <table className="data-table">
          <thead>
            <tr>{headers.map((header) => (
              <th key={header}>
                <button
                  type="button"
                  onClick={() => changeSort(header)}
                  disabled={canLoadRemote}
                  title={canLoadRemote ? "分页结果保持查询时的全局排序" : `按${String(columnLabels[header] || header)}排序`}
                >
                  {String(columnLabels[header] || header)}
                  {!canLoadRemote && (sort?.key !== header ? <ChevronsUpDown size={13} /> : sort.direction === "asc" ? <ArrowUp size={13} /> : <ArrowDown size={13} />)}
                </button>
              </th>
            ))}</tr>
          </thead>
          <tbody>
            {visibleRows.map((row, rowIndex) => (
              <tr key={`${pageIndex}-${rowIndex}`}>{headers.map((header, columnIndex) => (
                <td key={`${rowIndex}-${header}`}>{format(valueAt(row, header, columnIndex), header)}</td>
              ))}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-footer">
        <span>
          {footerSummary} · {headers.length} 列
          {loadState === "loading" && <em><LoaderCircle className="spin" size={12} />正在读取</em>}
          {loadState === "error" && <em className="error">分页读取失败，可重试</em>}
        </span>
        {pageCount > 1 && (canLoadRemote || returnedRows === rows.length) && <nav aria-label="表格分页">
          <button
            type="button"
            aria-label="上一页"
            disabled={pageIndex <= 0 || loadState === "loading"}
            onClick={() => changePage(pageIndex - 1)}
          ><ChevronLeft size={14} /></button>
          <span>{pageIndex + 1} / {pageCount}</span>
          <button
            type="button"
            aria-label={loadState === "error" ? "重试当前页" : "下一页"}
            disabled={(pageIndex >= pageCount - 1 && loadState !== "error") || loadState === "loading"}
            onClick={() => changePage(loadState === "error" ? pageIndex : pageIndex + 1)}
          ><ChevronRight size={14} /></button>
        </nav>}
      </div>
    </div>
  );
}
