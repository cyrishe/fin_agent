import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

type Row = Record<string, unknown> | unknown[];

export default function DataTable({ data }: { data: Record<string, unknown> }) {
  const headers = useMemo(() => {
    const configured = Array.isArray(data.headers) ? data.headers : data.columns;
    if (Array.isArray(configured)) return configured.map(String);
    const first = Array.isArray(data.rows) ? data.rows[0] : null;
    return first && typeof first === "object" && !Array.isArray(first) ? Object.keys(first) : [];
  }, [data]);
  const rows = useMemo(() => Array.isArray(data.rows) ? data.rows as Row[] : [], [data.rows]);
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
  const format = (value: unknown) => {
    if (value == null) return "—";
    if (typeof value === "boolean") return value ? "是" : "否";
    if (typeof value === "number") return value.toLocaleString("zh-CN", { maximumFractionDigits: 6 });
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
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
                  {header}
                  {sort?.key !== header ? <ChevronsUpDown size={13} /> : sort.direction === "asc" ? <ArrowUp size={13} /> : <ArrowDown size={13} />}
                </button>
              </th>
            ))}</tr>
          </thead>
          <tbody>
            {visibleRows.map((row, rowIndex) => (
              <tr key={rowIndex}>{headers.map((header, columnIndex) => (
                <td key={`${rowIndex}-${header}`}>{format(valueAt(row, header, columnIndex))}</td>
              ))}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-footer">
        <span>{rows.length} 行 · {headers.length} 列</span>
        {rows.length > 12 && <button type="button" onClick={() => setExpanded((value) => !value)}>{expanded ? "收起" : "查看全部"}</button>}
      </div>
    </div>
  );
}
