import { useEffect, useId, useState } from "react";

export default function MermaidDiagram({ source }: { source: string }) {
  const reactId = useId().replaceAll(":", "");
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void import("mermaid").then(async ({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        themeVariables: {
          primaryColor: "#eef4ff",
          primaryTextColor: "#15213a",
          primaryBorderColor: "#9cb7ee",
          lineColor: "#74829b",
          fontFamily: "Inter, PingFang SC, sans-serif",
        },
      });
      try {
        const result = await mermaid.render(`mermaid-${reactId}`, source);
        if (active) setSvg(result.svg);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "流程图无法渲染");
      }
    });
    return () => { active = false; };
  }, [reactId, source]);

  if (error) return <pre className="diagram-fallback">{source}</pre>;
  if (!svg) return <div className="block-skeleton">正在绘制流程图…</div>;
  return <div className="mermaid-diagram" dangerouslySetInnerHTML={{ __html: svg }} />;
}
