import { Maximize2, Minus, Plus, RotateCcw, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";

export default function MermaidDiagram({ source }: { source: string }) {
  const reactId = useId().replaceAll(":", "");
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [scale, setScale] = useState(1);

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

  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", close);
    document.body.classList.add("diagram-modal-open");
    return () => {
      document.removeEventListener("keydown", close);
      document.body.classList.remove("diagram-modal-open");
    };
  }, [open]);

  if (error) return <pre className="diagram-fallback">{source}</pre>;
  if (!svg) return <div className="block-skeleton">正在绘制流程图…</div>;
  return <>
    <div className="mermaid-shell">
      <button className="diagram-expand" type="button" onClick={() => { setScale(1); setOpen(true); }}><Maximize2 size={14} />查看大图</button>
      <div className="mermaid-diagram" dangerouslySetInnerHTML={{ __html: svg }} />
    </div>
    {open && createPortal(<div className="diagram-modal" role="dialog" aria-modal="true" aria-label="流程图大图">
      <div className="diagram-modal-head"><strong>流程图</strong><div><button type="button" onClick={() => setScale((value) => Math.max(.5, value - .15))} aria-label="缩小"><Minus size={17} /></button><span>{Math.round(scale * 100)}%</span><button type="button" onClick={() => setScale((value) => Math.min(2.5, value + .15))} aria-label="放大"><Plus size={17} /></button><button type="button" onClick={() => setScale(1)} aria-label="重置"><RotateCcw size={16} /></button><button type="button" onClick={() => setOpen(false)} aria-label="关闭"><X size={19} /></button></div></div>
      <div className="diagram-modal-canvas" onDoubleClick={() => setScale((value) => value === 1 ? 1.5 : 1)}><div style={{ transform: `scale(${scale})` }} dangerouslySetInnerHTML={{ __html: svg }} /></div>
    </div>, document.body)}
  </>;
}
