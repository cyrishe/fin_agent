import { appPath } from "./appPath";

export interface LibrarySkill {
  catalog_id: string;
  skill_name: string;
  display_name: string;
  description: string;
  short_description?: string;
  default_prompt?: string;
  category: string;
  skill_type: string;
  owner: string;
  scope?: string;
  auth?: string;
  invocation_enabled: boolean;
}
export interface SkillReference { path: string; title: string; content_hash: string }
export interface LibraryDetail extends LibrarySkill {
  skill_id: string;
  skill_markdown: string;
  references: SkillReference[];
  revision: string;
  content_hash: string;
  active_revision_no?: number;
  controls?: { execution_budget?: string; supplemental_tools?: string[]; web_search_enabled?: boolean };
}

export const categories: Record<string, string> = {
  "equity-research": "个股研究", "market-and-sector": "市场与板块",
  "screening-and-factor": "筛选与因子", "fund-research": "基金研究", "fixed-income": "债券研究",
};
export const categoryName = (category: string) => categories[category] || category || "其他方法";
export const scopeOf = (skill: LibrarySkill) => skill.owner === "system" ? "system" : skill.scope || skill.auth || "private";
export const scopeName = (skill: LibrarySkill) => ({ system: "系统", public: "公开", private: "私有" }[scopeOf(skill)] || "已授权");
export const skillUrl = (skill: Pick<LibrarySkill, "skill_name" | "catalog_id">) => appPath(`/skills/studio/${encodeURIComponent(skill.skill_name)}?catalog_id=${encodeURIComponent(skill.catalog_id)}`);

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(appPath(path), { credentials: "include", signal });
  const payload = await response.json();
  if (!response.ok || payload.ok === false || payload.error) throw new Error(payload.error || `读取失败（${response.status}）`);
  return payload as T;
}
export const getSkillLibrary = (signal?: AbortSignal) => get<{ items: LibrarySkill[] }>("/api/skill-hub", signal);
export const getSkillDetail = (skill: LibrarySkill, signal?: AbortSignal) => get<{ skill: LibraryDetail }>(`/api/skill-hub/${encodeURIComponent(skill.skill_name)}?catalog_id=${encodeURIComponent(skill.catalog_id)}`, signal);
export const getSkillReference = (detail: LibraryDetail, path: string, signal?: AbortSignal) => get<{ content: string; content_hash: string }>(`/api/skill-hub/${encodeURIComponent(detail.skill_id)}/references/${path.split("/").map(encodeURIComponent).join("/")}?revision=${encodeURIComponent(detail.revision)}`, signal);

export function filterSkills(items: LibrarySkill[], query: string, scope: string, category: string) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return items.filter(item => item.skill_type === "business_method"
    && (!scope || scopeOf(item) === scope) && (!category || item.category === category)
    && words.every(word => [item.display_name, item.skill_name, item.description, categoryName(item.category)].join(" ").toLocaleLowerCase().includes(word)));
}
export const methodBody = (markdown: string) => markdown.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, "");
export function methodSections(markdown: string) {
  let fence = "";
  return methodBody(markdown).split(/\r?\n/).flatMap(line => {
    const marker = line.match(/^\s*(`{3,}|~{3,})/);
    if (marker) { fence = fence ? (marker[1][0] === fence[0] && marker[1].length >= fence.length ? "" : fence) : marker[1]; return []; }
    return !fence && /^##\s+/.test(line) ? [line.replace(/^##\s+/, "").replace(/\s+#+\s*$/, "").trim()] : [];
  });
}
export function referenceTarget(href: string, current: string, references: SkillReference[]) {
  if (/^(?:[a-z][a-z\d+.-]*:|\/|#)/i.test(href)) return undefined;
  const path = href.split("#")[0];
  let decoded: string;
  try { decoded = decodeURIComponent(path); } catch { return undefined; }
  const parts = [...(current ? current.split("/").slice(0, -1) : []), ...decoded.split("/")];
  const normalized: string[] = [];
  for (const part of parts) {
    if (part === "..") { if (!normalized.length) return undefined; normalized.pop(); }
    else if (part && part !== ".") normalized.push(part);
  }
  return references.find(ref => ref.path === normalized.join("/"));
}
