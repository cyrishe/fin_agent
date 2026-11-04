export function formatFinancialValue(
  value: unknown,
  unit = "",
  maximumFractionDigits = 4,
): string {
  if (value == null || value === "") return "—";
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return `${String(value)}${unit}`;
  }

  const formatNumber = (number: number, digits = maximumFractionDigits) =>
    number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
  const absolute = Math.abs(value);
  if (unit === "元" && absolute >= 100_000_000) {
    return `${formatNumber(value / 100_000_000, 2)}亿元`;
  }
  if (unit === "元" && absolute >= 10_000) {
    return `${formatNumber(value / 10_000, 2)}万元`;
  }
  if (unit === "%") return `${formatNumber(value, 2)}%`;
  return `${formatNumber(value)}${unit}`;
}
