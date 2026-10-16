"""A lossless chapter/figure projection of the agent's one Markdown answer.

Section content remains natural language. IDs only address sections and saved
figures; neither the parser nor the renderer decides financial report taxonomy.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

FIGURE_PATTERN = re.compile(r"!\[([^\]\n]*)\]\(finance-figure:([A-Za-z0-9_-]+)\)")
PNG_PATTERN = re.compile(r"data:image/png;base64,[A-Za-z0-9+/=\s]+\Z")
_ANCHOR = re.compile(r"\s*\{#([A-Za-z][A-Za-z0-9_-]*)\}\s*$")
_DISPLAY_ANCHOR = re.compile(r"\s*\{#[A-Za-z][A-Za-z0-9_-]*\}(?=(?:\*\*|__)?\s*$)")


def collect_figures(result_refs: Sequence[Mapping[str, Any]], *, include_images: bool = True) -> list[dict[str, Any]]:
    figures: dict[str, dict[str, Any]] = {}
    for ref in result_refs:
        evidence = ref.get("provider_evidence") or {}
        for chart in ref.get("charts") or []:
            if not isinstance(chart, Mapping):
                continue
            digest, url = str(chart.get("sha256") or ""), str(chart.get("url") or "")
            if not re.fullmatch(r"[a-f0-9]{64}", digest) or not PNG_PATTERN.fullmatch(url):
                continue
            figure_id = f"fig_{digest[:16]}"
            figure = {
                "id": figure_id,
                "title": str(chart.get("title") or ("近期日K与成交量" if chart.get("name") == "current.png" else "形态局部图")),
                "source": "股票日线行情与同源指标",
                "code": str(evidence.get("code") or ""),
                "as_of": str(evidence.get("data_as_of") or ""),
                "price_basis": "后复权价按截止日真实收盘价缩放" if evidence.get("price_basis") == "hfq scaled to cutoff raw close" else str(evidence.get("price_basis") or ""),
                "display_start": str((chart.get("metadata") or {}).get("display_start") or ""),
                "display_end": str((chart.get("metadata") or {}).get("display_end") or ""),
                "sha256": digest,
            }
            if include_images:
                figure["image_url"] = url
            figures[figure_id] = figure
    return list(figures.values())


def build_report(message: str, result_refs: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, Any]]:
    figures = collect_figures(result_refs)
    known_figures = {figure["id"] for figure in figures}
    sections: list[dict[str, Any]] = []
    introduction: list[str] = []
    clean_lines: list[str] = []
    current = introduction
    title = ""
    seen: set[str] = set()
    fence = ""
    for line in message.splitlines():
        fence_match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match[1]
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            current.append(line)
            clean_lines.append(line)
            continue
        if not fence:
            # Missing/foreign references never become URLs or file reads.
            line = FIGURE_PATTERN.sub(lambda m: m[0] if m[2] in known_figures else m[1], line)
            heading = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
            if heading:
                label = heading[1]
                anchor = _ANCHOR.search(label)
                base = anchor[1] if anchor else f"section-{len(sections) + 1}"
                label = _ANCHOR.sub("", label).strip()
                section_id, suffix = base, 2
                while section_id in seen:
                    section_id = f"{base}-{suffix}"
                    suffix += 1
                seen.add(section_id)
                current = []
                sections.append({"id": section_id, "title": label, "_lines": current})
                clean_lines.append(f"## {label}")
                continue
            # Also tolerate a trailing anchor on a subheading/bold label. It
            # is presentation metadata, not part of the financial prose.
            line = _DISPLAY_ANCHOR.sub("", line)
            if not title and not sections and not any(introduction) and re.match(r"^#\s+", line):
                title = re.sub(r"^#\s+", "", line).strip()
                clean_lines.append(line)
                continue
        current.append(line)
        clean_lines.append(line)
    for section in sections:
        content = "\n".join(section.pop("_lines")).strip()
        section.update(content=content, figure_ids=list(dict.fromkeys(m[2] for m in FIGURE_PATTERN.finditer(content))))
    return "\n".join(clean_lines).strip(), {
        "title": title, "introduction": "\n".join(introduction).strip(),
        "sections": sections, "figures": figures,
    }


def public_report(report: Mapping[str, Any], *, include_images: bool = False) -> dict[str, Any]:
    return {**report, "figures": [
        {key: value for key, value in figure.items() if include_images or key != "image_url"}
        for figure in report.get("figures", [])
    ]}
