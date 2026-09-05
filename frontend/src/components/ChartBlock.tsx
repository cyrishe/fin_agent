import { Chart } from "@highcharts/react";

interface ChartData {
  x_axis?: unknown[];
  labels?: unknown[];
  categories?: unknown[];
  series?: Array<{ name?: string; data?: unknown[]; type?: string }>;
  items?: Array<{ label?: string; name?: string; value?: number }>;
}

export default function ChartBlock({ type, data }: { type: string; data: ChartData }) {
  const categories = (data.x_axis || data.labels || data.categories || []).map(String);
  const isPie = type === "pie_chart";
  const series = isPie
    ? [{
      type: "pie",
      name: "占比",
      data: (data.items || []).map((item) => ({ name: item.label || item.name || "-", y: Number(item.value || 0) })),
    }]
    : (data.series || []).map((item) => ({
      type: type === "bar_chart" ? "column" : (item.type || "spline"),
      name: item.name || "数据",
      data: (item.data || []).map((value) => Number(value)),
    }));

  if (!series.length) return <div className="empty-block">暂无图表数据</div>;

  return (
    <div className="chart-shell">
      <Chart options={{
        chart: { backgroundColor: "transparent", height: 310, spacing: [12, 10, 10, 8] },
        title: { text: undefined },
        credits: { enabled: false },
        legend: { align: "left", verticalAlign: "top", itemStyle: { color: "#4d5b73", fontWeight: "500" } },
        xAxis: { categories, lineColor: "#dce2eb", tickColor: "#dce2eb", labels: { style: { color: "#758096" } } },
        yAxis: { title: { text: undefined }, gridLineColor: "#edf0f4", labels: { style: { color: "#758096" } } },
        tooltip: { shared: !isPie, borderRadius: 10, shadow: false, borderColor: "#d8deea" },
        plotOptions: {
          series: { animation: { duration: 300 }, marker: { enabled: false } },
          column: { borderRadius: 4, borderWidth: 0 },
          pie: { innerSize: "52%", borderWidth: 2, borderColor: "#fff" },
        },
        colors: ["#316adf", "#16a37d", "#e89437", "#7d61d1", "#d85365"],
        series,
      }} />
    </div>
  );
}
