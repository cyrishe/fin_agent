import { StockChart } from "@highcharts/react/Stock";
import { useMemo, useState } from "react";
import type { RenderObject } from "../../rendering/model";
import { asRecord, normalizeCandles, normalizeIndicators } from "../../rendering/normalize";
import RenderMeta from "./RenderMeta";

interface IntradayPoint { time: string; price: number; average?: number; volume?: number }

function normalizeIntraday(object: RenderObject): IntradayPoint[] {
  const payload = object.payload;
  const candles = normalizeCandles(payload);
  if (candles.length) return candles.map((candle) => ({ time: candle.time, price: candle.close, volume: candle.volume }));
  const points = Array.isArray(payload.points) ? payload.points : Array.isArray(payload.rows) ? payload.rows : [];
  if (points.length) return points.map((item): IntradayPoint | null => {
    if (Array.isArray(item)) {
      const price = Number(item[1]);
      return Number.isFinite(price) ? { time: String(item[0] || ""), price, average: Number(item[2]) || undefined, volume: Number(item[3]) || undefined } : null;
    }
    const row = asRecord(item);
    const price = Number(row.price ?? row.close ?? row.value);
    return Number.isFinite(price) ? { time: String(row.time || row.datetime || ""), price, average: Number(row.average ?? row.avg_price) || undefined, volume: Number(row.volume) || undefined } : null;
  }).filter((point): point is IntradayPoint => point !== null);

  const series = Array.isArray(payload.series) ? payload.series.map(asRecord) : [];
  const labels = Array.isArray(payload.x_axis) ? payload.x_axis.map(String) : [];
  const priceSeries = series.find((item) => /价格|price|close/i.test(String(item.name || ""))) || series[0];
  const averageSeries = series.find((item) => /均价|average|avg/i.test(String(item.name || "")));
  const volumeSeries = series.find((item) => /成交量|volume|vol/i.test(String(item.name || "")));
  return (Array.isArray(priceSeries?.data) ? priceSeries.data : []).map((value, index) => ({
    time: labels[index] || String(index + 1),
    price: Number(value),
    average: Array.isArray(averageSeries?.data) ? Number(averageSeries.data[index]) : undefined,
    volume: Array.isArray(volumeSeries?.data) ? Number(volumeSeries.data[index]) : undefined,
  })).filter((point) => Number.isFinite(point.price));
}

export default function FinanceChart({ object, mode }: { object: RenderObject; mode: "kline" | "intraday" }) {
  const [showVolume, setShowVolume] = useState(true);
  const candles = useMemo(() => normalizeCandles(object.payload), [object.payload]);
  const indicators = useMemo(() => normalizeIndicators(object.payload.indicators), [object.payload]);
  const intraday = useMemo(() => normalizeIntraday(object), [object]);
  const labels = mode === "kline" ? candles.map((item) => item.time) : intraday.map((item) => item.time);

  const series = mode === "kline" ? [
    {
      type: "candlestick" as const,
      id: "price",
      name: "价格",
      data: candles.map((item, index) => [index, item.open, item.high, item.low, item.close]),
      color: "#20a37a",
      lineColor: "#168766",
      upColor: "#e45861",
      upLineColor: "#c84955",
      tooltip: { valueDecimals: 2 },
    },
    ...indicators.map((indicator, indicatorIndex) => ({
      type: "line" as const,
      name: indicator.name,
      data: indicator.points.map((point) => [Math.max(0, labels.indexOf(point.time)), point.value]),
      color: indicator.color || ["#e4a037", "#6f62cf", "#3e87c8"][indicatorIndex % 3],
      lineWidth: 1.2,
      marker: { enabled: false },
      tooltip: { valueDecimals: 2 },
    })),
    ...(showVolume && candles.some((item) => item.volume != null) ? [{
      type: "column" as const,
      name: "成交量",
      yAxis: 1,
      data: candles.map((item, index) => ({ y: item.volume || 0, color: item.close >= item.open ? "rgba(228,88,97,.55)" : "rgba(32,163,122,.55)", x: index })),
      borderWidth: 0,
      tooltip: { valueDecimals: 0 },
    }] : []),
  ] : [
    {
      type: "line" as const,
      name: "价格",
      data: intraday.map((item, index) => [index, item.price]),
      color: "#2f64d6",
      lineWidth: 1.8,
      marker: { enabled: false },
      tooltip: { valueDecimals: 2 },
    },
    ...(intraday.some((item) => item.average != null) ? [{
      type: "line" as const,
      name: "均价",
      data: intraday.map((item, index) => [index, item.average]),
      color: "#e2a33c",
      lineWidth: 1.2,
      marker: { enabled: false },
      tooltip: { valueDecimals: 2 },
    }] : []),
    ...(showVolume && intraday.some((item) => item.volume != null) ? [{
      type: "column" as const,
      name: "成交量",
      yAxis: 1,
      data: intraday.map((item, index) => ({ y: item.volume || 0, x: index, color: index && item.price < intraday[index - 1].price ? "rgba(32,163,122,.48)" : "rgba(228,88,97,.48)" })),
      borderWidth: 0,
    }] : []),
  ];

  const hasData = mode === "kline" ? candles.length > 0 : intraday.length > 0;
  if (!hasData) return <div className="empty-block">暂无可用的{mode === "kline" ? "K 线" : "分时"}数据</div>;

  return <div className="finance-chart">
    <div className="finance-chart-toolbar">
      <RenderMeta object={object} />
      <label><input type="checkbox" checked={showVolume} onChange={(event) => setShowVolume(event.target.checked)} />成交量</label>
    </div>
    <StockChart options={{
      chart: { backgroundColor: "transparent", height: 390, spacing: [10, 8, 8, 4] },
      title: { text: undefined },
      credits: { enabled: false },
      navigator: { enabled: labels.length > 80 },
      scrollbar: { enabled: labels.length > 80 },
      rangeSelector: { enabled: false },
      legend: { enabled: true, align: "left", verticalAlign: "top", itemStyle: { color: "#526078", fontSize: "10px" } },
      xAxis: { type: "linear", tickLength: 0, lineColor: "#dde3eb", labels: { formatter() { return labels[Number(this.value)] || ""; }, style: { color: "#7c8798", fontSize: "9px" } } },
      yAxis: [
        { height: showVolume ? "72%" : "100%", lineWidth: 0, gridLineColor: "#edf0f4", labels: { align: "right", x: -4, style: { color: "#7c8798", fontSize: "9px" } }, resize: { enabled: true } },
        { top: "76%", height: "24%", offset: 0, lineWidth: 0, gridLineWidth: 0, labels: { align: "right", x: -4, style: { color: "#8b95a4", fontSize: "8px" } } },
      ],
      tooltip: { split: true, borderColor: "#dce2eb", borderRadius: 8, shadow: false },
      plotOptions: { series: { animation: false, dataGrouping: { enabled: false }, states: { inactive: { opacity: 1 } } } },
      series,
    }} />
  </div>;
}
