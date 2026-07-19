export type SuggestionKind = "command" | "tool" | "skill";

export interface ComposerSuggestion {
  id: string;
  kind: SuggestionKind;
  value: string;
  label: string;
  description: string;
  keywords?: string[];
}

export interface InvocationRange {
  trigger: "/" | "$";
  query: string;
  start: number;
  end: number;
}

export const commandSuggestions: ComposerSuggestion[] = [
  { id: "command:custom_tool_create", kind: "command", value: "/custom_tool create", label: "/custom_tool create", description: "创建一个新的个人金融工具", keywords: ["ct", "new tool", "创建工具"] },
  { id: "command:custom_tool_edit", kind: "command", value: "/custom_tool edit", label: "/custom_tool edit", description: "优化当前个人工具设计", keywords: ["ct", "edit tool", "修改工具"] },
  { id: "command:custom_tool_commit", kind: "command", value: "/custom_tool commit", label: "/custom_tool commit", description: "提交已通过测试的工具", keywords: ["ct", "save tool", "提交工具"] },
  { id: "command:skills", kind: "command", value: "/skills", label: "/skills", description: "查看可用 Skills", keywords: ["skill", "技能"] },
  { id: "command:tools", kind: "command", value: "/tools", label: "/tools", description: "查看工具目录", keywords: ["tool", "工具"] },
  { id: "command:applications", kind: "command", value: "/applications", label: "/applications", description: "查看应用", keywords: ["app", "应用"] },
];

export function findInvocation(value: string, cursor: number): InvocationRange | null {
  const beforeCursor = value.slice(0, cursor);
  const lineStart = beforeCursor.lastIndexOf("\n") + 1;
  const line = beforeCursor.slice(lineStart);
  const slash = line.match(/(^|\s)(\/[^\n]*)$/);
  const dollar = line.match(/(^|\s)(\$[^\s]*)$/);
  const match = dollar && (!slash || (dollar.index || 0) >= (slash.index || 0)) ? dollar : slash;
  if (!match) return null;
  const query = match[2];
  const start = lineStart + (match.index || 0) + match[1].length;
  return { trigger: query[0] as "/" | "$", query, start, end: cursor };
}

export function filterSuggestions(range: InvocationRange | null, assets: ComposerSuggestion[]): ComposerSuggestion[] {
  if (!range) return [];
  let source = range.trigger === "/" ? commandSuggestions : assets.filter((item) => item.kind !== "command");
  let needle = range.query.slice(1).trim().toLowerCase();
  if (range.trigger === "$" && /^(tool|skill):/.test(needle)) {
    const [namespace, ...rest] = needle.split(":");
    source = source.filter((item) => item.kind === namespace);
    needle = rest.join(":");
  }
  if (range.trigger === "/" && /\s$/.test(range.query) && source.some((item) => item.value.toLowerCase() === range.query.trim().toLowerCase())) return [];
  const matches = source.filter((item) => {
    const candidate = item.value.slice(1).toLowerCase();
    const keywords = (item.keywords || []).join(" ").toLowerCase();
    return !needle || needle === "?" || candidate.startsWith(needle) || item.label.toLowerCase().includes(needle) || item.description.toLowerCase().includes(needle) || keywords.includes(needle);
  }).sort((left, right) => {
    const score = (item: ComposerSuggestion) => {
      const candidate = item.value.slice(1).toLowerCase();
      if (!needle || candidate.startsWith(needle)) return 0;
      if (item.label.toLowerCase().includes(needle)) return 1;
      if ((item.keywords || []).join(" ").toLowerCase().includes(needle)) return 2;
      return 3;
    };
    return score(left) - score(right) || left.label.localeCompare(right.label);
  });
  if (range.trigger === "/") return matches.slice(0, 8);
  return [
    ...matches.filter((item) => item.kind === "tool").slice(0, 5),
    ...matches.filter((item) => item.kind === "skill").slice(0, 3),
  ];
}

export function completeSuggestion(value: string, range: InvocationRange, suggestion: ComposerSuggestion): { value: string; cursor: number } {
  const replacement = `${suggestion.value} `;
  return {
    value: `${value.slice(0, range.start)}${replacement}${value.slice(range.end)}`,
    cursor: range.start + replacement.length,
  };
}
