import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SkillBrowser, SkillReader } from "./SkillStudio";
import { filterSkills, getSkillDetail, getSkillLibrary, getSkillReference, methodBody, methodSections, referenceTarget, scopeOf, skillUrl, type LibraryDetail, type LibrarySkill } from "./skillLibrary";
import SkillActivity from "./components/SkillActivity";

const system: LibrarySkill = { catalog_id: "skill:business_method:stock-research", skill_name: "stock-research", display_name: "个股研究", description: "分析基本面和机构观点", category: "equity-research", skill_type: "business_method", owner: "system", auth: "public", invocation_enabled: true };
const privateSkill = { ...system, catalog_id: "personal", skill_name: "personal", owner: "alice", scope: "private", display_name: "私有方法", description: "个人分析框架" };
describe("Skill reading workspace", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("distinguishes system from user public and private without reintroducing legacy", () => {
    const publicSkill = { ...privateSkill, scope: "public" };
    const items = [system, privateSkill, publicSkill, { ...system, skill_type: "legacy_compiled" }];
    expect(scopeOf(system)).toBe("system");
    expect(filterSkills(items, "", "system", "")).toEqual([system]);
    expect(filterSkills(items, "", "private", "")).toEqual([privateSkill]);
    expect(filterSkills(items, "", "public", "")).toEqual([publicSkill]);
    expect(filterSkills(items, "个股 机构", "", "equity-research")).toEqual([system]);
    expect(filterSkills(items, "不存在", "", "")).toEqual([]);
  });
  it("derives navigation only from real Markdown headings outside code fences", () => {
    const source = "---\nname: method\n---\n# 研究\n## 默认方法\n```md\n## Not a section\n```\n## 证据与输出\n";
    expect(methodBody(source)).toBe("# 研究\n## 默认方法\n```md\n## Not a section\n```\n## 证据与输出\n");
    expect(methodSections(source)).toEqual(["默认方法", "证据与输出"]);
    expect(methodSections("~~~\n## Code\n~~~\n## Real")).toEqual(["Real"]);
  });
  it("resolves only published reference paths, including relative links inside references", () => {
    const ref = { path: "references/valuation.md", title: "估值", content_hash: "h" };
    expect(referenceTarget("references/valuation.md#test", "", [ref])).toEqual(ref);
    expect(referenceTarget("./valuation.md", "references/main.md", [ref])).toEqual(ref);
    for (const href of ["javascript:alert(1)", "../../etc/passwd", "/references/valuation.md", "references/missing.md", "%broken"]) expect(referenceTarget(href, "", [ref])).toBeUndefined();
  });
  it("makes read-only requests with identity, exact catalog id and frozen reference revision", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ok: true, items: [] }))));
    vi.stubGlobal("fetch", fetchMock);
    const signal = new AbortController().signal;
    await getSkillLibrary(signal);
    await getSkillDetail(system, signal);
    await getSkillReference({ ...system, skill_id: "stock-research", revision: "frozen" } as LibraryDetail, "references/a b.md", signal);
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      "/api/skill-hub", "/api/skill-hub/stock-research?catalog_id=skill%3Abusiness_method%3Astock-research",
      "/api/skill-hub/stock-research/references/references/a%20b.md?revision=frozen",
    ]);
    for (const [, options] of fetchMock.mock.calls) expect(options).toEqual({ credentials: "include", signal });
    expect(skillUrl(system)).toContain("catalog_id=skill%3Abusiness_method%3Astock-research");
  });
  it("surfaces permission, stale revision and network failures instead of manufacturing content", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: false, error: "版本已变化" }), { status: 409 })));
    await expect(getSkillDetail(system)).rejects.toThrow("版本已变化");
  });
  it("renders an accessible read-only entry without edit, delete or publish controls", () => {
    const html = renderToStaticMarkup(<SkillBrowser onUse={() => {}} />);
    expect(html).toContain("搜索 Skill"); expect(html).toContain("Skill 来源筛选"); expect(html).toContain("正在读取已授权的方法库");
    const reader = renderToStaticMarkup(<SkillReader item={system} onUse={() => {}} />);
    expect(reader).toContain("普通用户不可修改"); expect(reader).toContain("仅带入选择，不会自动发送");
    expect(reader).not.toContain("textarea"); expect(reader).not.toContain("启用当前候选");
    expect(reader).toContain("disabled");
  });
  it("links loaded methods to current readable content without replacing the conversation", () => {
    const html = renderToStaticMarkup(<SkillActivity run={{ status: "done", summary: "", process: [], artifacts: [] }} payload={{ financial_qa: { skill_entries: [{ skill_id: "stock-research", display_name: "个股研究" }] } }} />);
    expect(html).toContain('href="/skills/studio/stock-research"');
    expect(html).toContain('target="_blank"'); expect(html).toContain("当前方法内容");
  });
});

describe("Skill editing and use-only sharing", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("never presents shared source as publicly readable", () => {
    const shared = { ...privateSkill, scope: "public", viewable: false, editable: false };
    const html = renderToStaticMarkup(<SkillReader item={shared} onUse={() => {}} />);
    expect(html).toContain("正文与内部配置仅作者可查看");
    expect(html).not.toContain("编辑方法与参考");
  });
  it("sends version checks and content, never caller-supplied role", async () => {
    const { saveSkillDefinition, activateSkillDefinition } = await import("./skillLibrary");
    const base = { skill_id: "personal", display_name: "个人", skill_markdown: "body", references: {}, control_manifest: {}, candidate_revision_no: 2, active_revision_no: 1, content_hash: "h2" };
    const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, candidate: base })));
    vi.stubGlobal("fetch", mock);
    await saveSkillDefinition(base, { display_name: "新名称", skill_markdown: "body 3", references: {}, control_manifest: {} });
    mock.mockResolvedValue(new Response(JSON.stringify({ ok: true, candidate: base })));
    await activateSkillDefinition(base);
    expect(mock.mock.calls[0][0]).toBe("/api/skill-hub/personal/definition");
    expect(JSON.parse(mock.mock.calls[0][1].body)).toMatchObject({ expected_candidate_revision: 2, expected_content_hash: "h2" });
    expect(mock.mock.calls[1][0]).toBe("/api/skill-hub/personal/definition/activate");
    expect(JSON.parse(mock.mock.calls[1][1].body)).toEqual({ expected_candidate_revision: 2, expected_active_revision: 1 });
    expect(mock.mock.calls[0][1].credentials).toBe("include");
    expect(mock.mock.calls[0][1].body).not.toContain("is_admin");
  });
});
