export type SuggestionKind = "command" | "tool" | "skill";

export interface ComposerSuggestion {
  id: string;
  kind: SuggestionKind;
  value: string;
  label: string;
  description: string;
  keywords?: string[];
  customTool?: boolean;
  editable?: boolean;
  assetName?: string;
}

export interface InvocationRange {
  trigger: "/" | "$";
  query: string;
  start: number;
  end: number;
  context?: "command" | "asset" | "edit_tool";
}

export const commandSuggestions: ComposerSuggestion[] = [
  { id: "command:custom_tool_create", kind: "command", value: "/custom_tool create", label: "/custom_tool create", description: "创建一个新的个人金融工具", keywords: ["ct", "new tool", "创建工具"] },
  { id: "command:custom_tool_edit", kind: "command", value: "/custom_tool edit", label: "/custom_tool edit", description: "选择并修改已有的个人工具", keywords: ["ct", "edit tool", "修改工具"] },
  { id: "command:custom_tool_commit", kind: "command", value: "/custom_tool commit", label: "/custom_tool commit", description: "提交已通过测试的工具", keywords: ["ct", "save tool", "提交工具"] },
  { id: "command:skills", kind: "command", value: "/skills", label: "/skills", description: "查看可用 Skills", keywords: ["skill", "技能"] },
  { id: "command:tools", kind: "command", value: "/tools", label: "/tools", description: "查看工具目录", keywords: ["tool", "工具"] },
  { id: "command:applications", kind: "command", value: "/applications", label: "/applications", description: "查看应用", keywords: ["app", "应用"] },
];

export function findInvocation(value: string, cursor: number): InvocationRange | null {
  const boundedCursor = Math.max(0, Math.min(cursor, value.length));
  const beforeCursor = value.slice(0, boundedCursor);
  const lineStart = beforeCursor.lastIndexOf("\n") + 1;
  const line = beforeCursor.slice(lineStart);
  const editTarget = line.match(/^(\s*\/custom_tool\s+edit\s+)([^\s]*)$/i);
  if (editTarget) {
    const query = editTarget[2] || "";
    return {
      trigger: "/",
      query,
      start: lineStart + editTarget[1].length,
      end: boundedCursor,
      context: "edit_tool",
    };
  }
  const slash = line.match(/(^|\s)(\/[^\n]*)$/);
  const dollar = line.match(/(^|\s)(\$[^\s]*)$/);
  const match = dollar && (!slash || (dollar.index || 0) >= (slash.index || 0)) ? dollar : slash;
  if (!match) return null;
  const query = match[2];
  if (/^\$\d/.test(query)) return null;
  const start = lineStart + (match.index || 0) + match[1].length;
  return {
    trigger: query[0] as "/" | "$",
    query,
    start,
    end: boundedCursor,
    context: query[0] === "$" ? "asset" : "command",
  };
}

function normalized(value: string): string {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/^[/$]/, "")
    .replace(/[\s_.:/\\-]+/g, "");
}

function searchParts(value: string): string[] {
  const text = value.normalize("NFKC").toLocaleLowerCase().replace(/^[/$]/, "");
  return [
    text,
    ...text.split(/[\s_.:/\\-]+/),
  ].map(normalized).filter(Boolean);
}

function boundedEditDistance(left: string, right: string, maximum: number): number {
  if (Math.abs(left.length - right.length) > maximum) return maximum + 1;
  let previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    const current = [leftIndex];
    let rowMinimum = leftIndex;
    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      const substitution = previous[rightIndex - 1] + (left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1);
      const distance = Math.min(
        previous[rightIndex] + 1,
        current[rightIndex - 1] + 1,
        substitution,
      );
      current.push(distance);
      rowMinimum = Math.min(rowMinimum, distance);
    }
    if (rowMinimum > maximum) return maximum + 1;
    previous = current;
  }
  return previous[right.length];
}

function typoDistance(needle: string, values: string[]): number | null {
  if (needle.length < 3) return null;
  const maximum = needle.length <= 5 ? 1 : 2;
  let best = maximum + 1;
  values.forEach((rawValue) => {
    searchParts(rawValue).forEach((candidate) => {
      if (!candidate) return;
      if (Math.abs(candidate.length - needle.length) <= maximum) {
        best = Math.min(best, boundedEditDistance(needle, candidate, maximum));
      }
      if (candidate.length > needle.length + maximum) {
        for (let start = 0; start < candidate.length; start += 1) {
          for (let delta = -maximum; delta <= maximum; delta += 1) {
            const length = needle.length + delta;
            if (length < 1 || start + length > candidate.length) continue;
            best = Math.min(
              best,
              boundedEditDistance(needle, candidate.slice(start, start + length), maximum),
            );
          }
        }
      }
    });
  });
  return best <= maximum ? best : null;
}

function isUsefulSubsequence(needle: string, candidate: string): boolean {
  if (needle.length < 3 || needle.length / Math.max(candidate.length, 1) < 0.42) return false;
  let position = 0;
  for (const character of candidate) {
    if (character === needle[position]) position += 1;
    if (position === needle.length) return true;
  }
  return false;
}

function suggestionScore(item: ComposerSuggestion, needle: string): number | null {
  if (!needle) return 100;
  const canonical = normalized(item.value);
  const display = normalized(item.label);
  const description = normalized(item.description);
  const keywords = (item.keywords || []).map(normalized).filter(Boolean);

  if (canonical === needle) return 0;
  if (display === needle) return 1;
  if (keywords.some((keyword) => keyword === needle)) return 2;
  if (canonical.startsWith(needle)) return 3;
  if (display.startsWith(needle)) return 4;
  if (keywords.some((keyword) => keyword.startsWith(needle))) return 5;
  if (canonical.includes(needle)) return 6;
  if (display.includes(needle)) return 7;
  if (keywords.some((keyword) => keyword.includes(needle))) return 8;
  if (description.includes(needle)) return 9;

  const distance = typoDistance(needle, [item.value, item.label, ...(item.keywords || [])]);
  if (distance !== null) return 10 + distance;
  if ([canonical, display, ...keywords].some((candidate) => isUsefulSubsequence(needle, candidate))) return 13;
  return null;
}

export function filterSuggestions(range: InvocationRange | null, assets: ComposerSuggestion[]): ComposerSuggestion[] {
  if (!range) return [];
  if (range.context === "edit_tool") {
    const needle = normalized(range.query);
    return assets
      .filter((item) => item.kind === "tool" && item.customTool && item.editable !== false)
      .map((item, index) => ({ item, index, score: suggestionScore(item, needle) }))
      .filter((match): match is { item: ComposerSuggestion; index: number; score: number } => match.score !== null)
      .sort((left, right) =>
        left.score - right.score ||
        left.item.label.localeCompare(right.item.label, "zh-CN") ||
        left.index - right.index
      )
      .slice(0, 8)
      .map((match) => match.item);
  }
  let source = range.trigger === "/" ? commandSuggestions : assets.filter((item) => item.kind !== "command");
  let rawNeedle = range.query.slice(1).trim().toLocaleLowerCase();
  let namespace = "";
  if (range.trigger === "$" && /^(tool|skill):/.test(rawNeedle)) {
    const [resolvedNamespace, ...rest] = rawNeedle.split(":");
    namespace = resolvedNamespace;
    source = source.filter((item) => item.kind === namespace);
    rawNeedle = rest.join(":");
  }
  if (
    range.trigger === "/" &&
    /\s$/.test(range.query) &&
    source.some((item) => item.value.toLocaleLowerCase() === range.query.trim().toLocaleLowerCase())
  ) return [];

  const needle = rawNeedle === "?" ? "" : normalized(rawNeedle);
  const unique = source.filter((item, index, values) =>
    values.findIndex((candidate) => candidate.id === item.id) === index
  );
  const matches = unique.map((item, index) => ({
    item,
    index,
    score: suggestionScore(item, needle),
  })).filter((match): match is { item: ComposerSuggestion; index: number; score: number } =>
    match.score !== null
  ).sort((left, right) =>
    left.score - right.score ||
    Number(Boolean(right.item.customTool)) - Number(Boolean(left.item.customTool)) ||
    left.item.label.localeCompare(right.item.label, "zh-CN") ||
    left.index - right.index
  ).map((match) => match.item);

  if (range.trigger === "/" || namespace) return matches.slice(0, 8);
  return [
    ...matches.filter((item) => item.kind === "tool").slice(0, 5),
    ...matches.filter((item) => item.kind === "skill").slice(0, 3),
  ];
}

export function completeEditTool(
  value: string,
  range: InvocationRange,
  suggestion: ComposerSuggestion,
): { value: string; cursor: number } {
  const toolName = suggestion.assetName || suggestion.value.replace(/^\$/, "");
  const replacement = `${toolName} `;
  return {
    value: `${value.slice(0, range.start)}${replacement}${value.slice(range.end)}`,
    cursor: range.start + replacement.length,
  };
}

export function completeSuggestion(value: string, range: InvocationRange, suggestion: ComposerSuggestion): { value: string; cursor: number } {
  const replacement = `${suggestion.value} `;
  return {
    value: `${value.slice(0, range.start)}${replacement}${value.slice(range.end)}`,
    cursor: range.start + replacement.length,
  };
}

export function removeInvocation(value: string, range: InvocationRange): { value: string; cursor: number } {
  let before = value.slice(0, range.start);
  let after = value.slice(range.end);
  const invocationCue = before.trim();
  if (["请用", "请使用", "请调用", "用", "使用", "调用"].includes(invocationCue)) {
    before = "";
  }
  if (before && /\s$/.test(before) && /^\s/.test(after)) after = after.replace(/^\s+/, "");
  if (!after && /\s+$/.test(before)) before = before.replace(/\s+$/, "");
  if (!before) after = after.replace(/^\s+/, "");
  return {
    value: `${before}${after}`,
    cursor: before.length,
  };
}
