import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { formatFinancialValue } from "../rendering/formatFinancialValue";

type Row = Record<string, unknown> | unknown[];

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
  const totalRows = Number.isFinite(Number(data.row_count))
    ? Math.max(rows.length, Number(data.row_count))
    : rows.length;
  const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" } | null>(null);
  const [expanded, setExpanded] = useState(false);

  const visibleRows = useMemo(() => {
    const sorted = [...rows];
    if (sort) {
      const index = headers.indexOf(sort.key);
      sorted.sort((left, right) => {
        const a = Array.isArray(left) ? left[index] : left[sort.key];
        const b = Array.isArray(right) ? right[index] : right[sort.key];
        const numeric = Number(a) - Number(b);
        const result = Number.isNaN(numeric) ? String(a ?? "").localeCompare(String(b ?? ""), "zh-CN") : numeric;
        return sort.direction === "asc" ? result : -result;
      });
    }
    return expanded ? sorted : sorted.slice(0, 12);
  }, [expanded, headers, rows, sort]);

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
    setSort((current) => current?.key === key
      ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
      : { key, direction: "asc" });
  };

  return (
    <div className="table-shell">
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>{headers.map((header) => (
              <th key={header}>
                <button type="button" onClick={() => changeSort(header)}>
                  {String(columnLabels[header] || header)}
                  {sort?.key !== header ? <ChevronsUpDown size={13} /> : sort.direction === "asc" ? <ArrowUp size={13} /> : <ArrowDown size={13} />}
                </button>
              </th>
            ))}</tr>
          </thead>
          <tbody>
            {visibleRows.map((row, rowIndex) => (
              <tr key={rowIndex}>{headers.map((header, columnIndex) => (
                <td key={`${rowIndex}-${header}`}>{format(valueAt(row, header, columnIndex), header)}</td>
              ))}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-footer">
        <span>{totalRows > rows.length ? `显示 ${rows.length} / 共 ${totalRows} 行` : `${rows.length} 行`} · {headers.length} 列</span>
        {rows.length > 12 && <button type="button" onClick={() => setExpanded((value) => !value)}>{expanded ? "收起" : "查看全部"}</button>}
      </div>
    </div>
  );
}
