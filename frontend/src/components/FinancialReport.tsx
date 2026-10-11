import { useId, useState } from "react";
import type { UnknownRecord } from "../types";
import MarkdownContent from "./MarkdownContent";
import "./financial-report.css";

const rows = (value: unknown): UnknownRecord[] => Array.isArray(value) ? value.filter(item => item && typeof item === "object") : [];
const text = (value: unknown): string => typeof value === "string" ? value : "";
const png = /^data:image\/png;base64,[A-Za-z0-9+/=\s]+$/;

export default function FinancialReport({ report }: { report: UnknownRecord }) {
  const scope = useId();
  const [expandedFigure, setExpandedFigure] = useState<string | null>(null);
  const sections = rows(report.sections);
  const figures = rows(report.figures).filter(figure => png.test(text(figure.image_url)));
  const byId = new Map(figures.map(figure => [text(figure.id), figure]));
  const referenced = new Set<string>();
  const renderFigure = (figure: UnknownRecord, caption?: string) => {
    const id = text(figure.id);
    const url = text(figure.image_url);
    if (!png.test(url)) return null;
    const expanded = expandedFigure === id;
    return <figure className="financial-report-figure" key={id}>
      <div className={`financial-report-image${expanded ? " expanded" : ""}`} id={`${scope}-${id}`} tabIndex={expanded ? 0 : undefined} role={expanded ? "region" : undefined} aria-label={expanded ? "放大行情图，可横向滚动" : undefined}>
        <img src={url} alt={caption || text(figure.title)} loading="lazy" />
      </div>
      <figcaption><strong>{caption || text(figure.title)}</strong><span>{[figure.code, figure.as_of, figure.price_basis].filter(Boolean).join(" · ")}</span><span>{text(figure.source)}</span></figcaption>
      <button type="button" className="financial-report-zoom" aria-expanded={expanded} aria-controls={`${scope}-${id}`} onClick={() => setExpandedFigure(expanded ? null : id)}>{expanded ? "适应宽度" : "放大图片"}</button>
    </figure>;
  };
  const renderContent = (content: string) => {
    const parts = [];
    let offset = 0;
    for (const match of content.matchAll(/!\[([^\]\n]*)\]\(finance-figure:([A-Za-z0-9_-]+)\)/g)) {
      parts.push(<MarkdownContent key={`text-${offset}`} content={content.slice(offset, match.index)} renderImages={false} />);
      const figure = byId.get(match[2]);
      if (figure) {
        referenced.add(match[2]);
        parts.push(renderFigure(figure, match[1]));
      }
      offset = (match.index || 0) + match[0].length;
    }
    parts.push(<MarkdownContent key={`text-${offset}`} content={content.slice(offset)} renderImages={false} />);
    return parts;
  };
  const introduction = renderContent(text(report.introduction));
  const chapters = sections.map(section => <section className="financial-report-section" key={text(section.id)} id={`${scope}-${text(section.id)}`}>
    <h2>{text(section.title)}</h2>{renderContent(text(section.content))}
  </section>);
  // Older/plain answers still show their generated chart, even without placement markers.
  const remaining = figures.filter(figure => !referenced.has(text(figure.id)));
  return <article className="financial-report">
    {text(report.title) && <h1>{text(report.title)}</h1>}
    {introduction}
    {sections.length >= 3 && <nav aria-label="报告章节">{sections.map(section => <a key={text(section.id)} href={`#${scope}-${text(section.id)}`}>{text(section.title)}</a>)}</nav>}
    {chapters}
    {remaining.length > 0 && <details className="financial-report-charts" open={figures.length === 1}>
      <summary>本轮行情图（{remaining.length}）</summary>{remaining.map(figure => renderFigure(figure))}
    </details>}
  </article>;
}
