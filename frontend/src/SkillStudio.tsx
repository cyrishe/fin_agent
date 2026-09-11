import { ArrowLeft, ArrowRight, BookOpen, Check, ChevronRight, Copy, ExternalLink, Layers3, LockKeyhole, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { appPath, stripAppBase } from "./appPath";
import { categoryName, filterSkills, getSkillDetail, getSkillLibrary, getSkillReference, methodBody, methodSections, referenceTarget, scopeName, scopeOf, skillUrl, type LibraryDetail, type LibrarySkill } from "./skillLibrary";
import "./skill-studio.css";

type BrowserProps = { onUse: (skill: LibrarySkill) => void; onClose?: () => void; standalone?: boolean };
const messageOf = (error: unknown) => error instanceof Error ? error.message : "读取失败，请稍后重试。";

export function SkillBrowser({ onUse, onClose, standalone = false }: BrowserProps) {
  const [items, setItems] = useState<LibrarySkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("");
  const [category, setCategory] = useState("");
  const [selected, setSelected] = useState<LibrarySkill | null>(null);
  const [missing, setMissing] = useState(false);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const available = items.filter(item => item.skill_type === "business_method");
  const filtered = filterSkills(items, query, scope, category);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    getSkillLibrary(controller.signal).then(payload => {
      if (controller.signal.aborted) return;
      setItems(payload.items);
      if (standalone) {
        const resolveLocation = () => {
          const raw = stripAppBase(window.location.pathname).split("/skills/studio/")[1] || "";
          let name = raw;
          try { name = decodeURIComponent(raw); } catch { /* Display unavailable rather than crash. */ }
          const catalogId = new URLSearchParams(window.location.search).get("catalog_id");
          const target = payload.items.find(item => item.skill_type === "business_method" && item.skill_name === name && (!catalogId || item.catalog_id === catalogId));
          setSelected(target || null); setMissing(Boolean(name && !target));
        };
        resolveLocation();
      }
    }).catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [retry, standalone]);

  useEffect(() => {
    if (!standalone) return;
    document.title = selected ? `${selected.display_name} · Skill Studio` : "Skill Studio · Fin Agent";
  }, [standalone, selected]);

  useEffect(() => {
    if (!standalone) return;
    const back = () => setRetry(value => value + 1);
    window.addEventListener("popstate", back);
    return () => window.removeEventListener("popstate", back);
  }, [standalone]);

  const choose = (item: LibrarySkill | null) => {
    setSelected(item); setMissing(false);
    if (standalone) window.history.pushState({}, "", item ? skillUrl(item) : appPath("/skills/studio"));
    requestAnimationFrame(() => titleRef.current?.focus());
  };

  return <section className="skill-studio" aria-label="Skill 方法库">
    <header className="sl-topbar">
      <a className="sl-brand" href={appPath("/assistant")}><span><Layers3 size={20} /></span><strong>Fin Agent</strong><i>/</i><span>Skill Studio</span></a>
      <div className="sl-top-actions"><span className="sl-readonly"><LockKeyhole size={13} />阅读空间</span>{onClose ? <button className="sl-icon" onClick={onClose} aria-label="关闭 Skill 方法库"><X size={19} /></button> : <a className="sl-text-link" href={appPath("/assistant")}>返回对话 <ArrowRight size={14} /></a>}</div>
    </header>
    <div className="sl-workspace">
      <aside className={`sl-sidebar ${selected ? "sl-sidebar-detail" : ""}`}>
        <div className="sl-sidebar-title">方法库 <span>{available.length.toString().padStart(2, "0")}</span></div>
        <label className="sl-search"><Search size={16} /><input aria-label="搜索 Skill" placeholder="搜索名称或想解决的问题" value={query} onChange={event => setQuery(event.target.value)} /></label>
        <nav className="sl-scopes" aria-label="Skill 来源筛选">{[["", "全部方法"], ["system", "系统 Skill"], ["public", "公开 Skill"], ["private", "私有 Skill"]].map(([value, label]) => <button key={value} aria-pressed={scope === value} onClick={() => setScope(value)}><span>{label}</span><small>{available.filter(item => !value || scopeOf(item) === value).length}</small></button>)}</nav>
        <label className="sl-category">研究方向<select aria-label="研究方向" value={category} onChange={event => setCategory(event.target.value)}><option value="">全部方向</option>{[...new Set(available.map(item => item.category))].map(value => <option key={value} value={value}>{categoryName(value)}</option>)}</select></label>
        {selected && <nav className="sl-compact-list" aria-label="切换 Skill">{filtered.map(item => <button key={item.catalog_id} aria-current={selected.catalog_id === item.catalog_id ? "page" : undefined} onClick={() => choose(item)}>{item.display_name}<ChevronRight size={13} /></button>)}{!filtered.length && <p>没有匹配的方法</p>}</nav>}
        <div className="sl-sidebar-note"><BookOpen size={19} /><strong>先读懂，再使用</strong><p>系统方法由平台维护。你可以查看完整内容，并把它带入自己的分析。</p><a href={appPath("/skills/studio?mode=authoring")} target="_blank" rel="noreferrer">打开已有的个人构建工作区 <ExternalLink size={12} /></a></div>
      </aside>
      <main className="sl-main">
        {loading ? <div className="sl-empty" role="status">正在读取已授权的方法库…</div> : error ? <div className="sl-empty" role="alert"><h2>方法库暂时无法读取</h2><p>{error}</p><button className="sl-button" onClick={() => setRetry(value => value + 1)}>重新加载</button></div> : missing ? <div className="sl-empty"><h2>当前无法查看这份 Skill</h2><p>它可能已下线，或不在你当前可访问的范围内。</p><button className="sl-button" onClick={() => choose(null)}>返回全部方法</button></div> : selected ? <>
          <button className="sl-back" onClick={() => choose(null)}><ArrowLeft size={14} />全部方法</button>
          <SkillReader key={selected.catalog_id} item={selected} onUse={onUse} titleRef={titleRef} />
        </> : <>
          <div className="sl-intro"><span className="sl-eyebrow">THE METHOD LIBRARY</span><h1 ref={titleRef} tabIndex={-1}>读懂方法，再展开研究。</h1><p>从一个问题找到专业方法。看清它如何分析、何时深入，以及结论需要哪些证据。</p><div className="sl-intro-meta"><span><Check size={13} />当前账户可见</span><span><LockKeyhole size={13} />系统内容只读</span><span><Layers3 size={13} />按需展开子方法</span></div></div>
          <div className="sl-results-head"><h2>{query ? "搜索结果" : scope ? `${scope === "system" ? "系统" : scope === "public" ? "公开" : "私有"}方法` : "全部方法"}<span>{filtered.length}</span></h2><span>选一份方法，了解它的工作方式</span></div>
          <div className="sl-cards">{filtered.map(item => <button className="sl-card" key={item.catalog_id} onClick={() => choose(item)}><div className="sl-card-top"><span className="sl-card-icon"><BookOpen size={19} /></span><span className="sl-badge">{scopeName(item)} · 只读</span></div><span className="sl-card-category">{categoryName(item.category)}</span><h3>{item.display_name}</h3><p>{item.short_description || item.description}</p><div className="sl-card-bottom"><code>${item.skill_name}</code><span>查看方法 <ArrowRight size={14} /></span></div></button>)}</div>
          {!filtered.length && <div className="sl-empty"><h3>{available.length ? "没有找到匹配的方法" : "当前还没有可用方法"}</h3><p>{scope === "private" ? "只有你有权访问的私有方法才会显示在这里。" : "试试其他关键词或研究方向。"}</p><button className="sl-button" onClick={() => { setQuery(""); setCategory(""); setScope(""); }}>查看全部</button></div>}
          <footer className="sl-footer">方法指导分析 · 数据工具提供证据 · Agent 根据问题组织回答</footer>
        </>}
      </main>
    </div>
  </section>;
}

export function SkillReader({ item, onUse, titleRef }: { item: LibrarySkill; onUse: (skill: LibrarySkill) => void; titleRef?: React.RefObject<HTMLHeadingElement> }) {
  const [detail, setDetail] = useState<LibraryDetail | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [tab, setTab] = useState("overview");
  const [path, setPath] = useState("");
  const [referenceResult, setReference] = useState<{ path: string; content: string; content_hash: string } | null>(null);
  const reference = referenceResult?.path === path ? referenceResult : null;
  const [referenceError, setReferenceError] = useState("");
  const [referenceRetry, setReferenceRetry] = useState(0);
  const [raw, setRaw] = useState(false);
  const [copyMessage, setCopyMessage] = useState("");
  const documentRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    setError(""); setDetail(null);
    getSkillDetail(item, controller.signal).then(payload => { if (!controller.signal.aborted) setDetail(payload.skill); })
      .catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [item.catalog_id, retry]);
  useEffect(() => {
    setReference(null); setReferenceError("");
    if (!path || !detail) return;
    const controller = new AbortController();
    getSkillReference(detail, path, controller.signal).then(result => { if (!controller.signal.aborted) setReference({ ...result, path }); })
      .catch(reason => { if (!controller.signal.aborted) setReferenceError(messageOf(reason)); });
    return () => controller.abort();
  }, [path, detail, referenceRetry]);
  const openReference = (value: string) => { setPath(value); setTab("method"); setRaw(false); };
  const sections = methodSections(detail?.skill_markdown || "");
  const content = path ? reference?.content || "" : detail?.skill_markdown || "";
  const references = detail?.references || [];
  const referenceInfo = references.find(ref => ref.path === path);
  const jumpTo = (index: number) => {
    setTab("method"); setPath(""); setRaw(false);
    requestAnimationFrame(() => {
      const heading = documentRef.current?.querySelectorAll("h2")[index];
      heading?.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  };
  return <div className="sl-detail">
    <header className="sl-detail-hero"><div><div className="sl-eyebrow">{categoryName(item.category)} <span>/</span> {scopeName(item)} Skill</div><h1 ref={titleRef} tabIndex={-1}>{item.display_name}</h1><p>{item.description}</p></div><div className="sl-use"><button className="sl-button sl-primary" disabled={!detail || !item.invocation_enabled} onClick={() => detail && onUse(detail)}>用于对话 <ArrowRight size={16} /></button><small>仅带入选择，不会自动发送</small></div></header>
    <div className="sl-permission"><LockKeyhole size={14} /><span>{item.owner === "system" ? "系统维护 · 所有人可阅读，普通用户不可修改" : scopeOf(item) === "private" ? "私有方法 · 当前展示你有权查看的启用版本" : "用户公开分享 · 当前展示已发布的只读版本"}</span><code>${item.skill_name}</code></div>
    <nav className="sl-tabs" aria-label="内容视图">{[["overview", "概览"], ["method", "专业方法"], ["source", "来源与版本"]].map(([value, label]) => <button key={value} aria-pressed={tab === value} onClick={() => setTab(value)}>{label}</button>)}</nav>
    {error ? <div className="sl-empty" role="alert"><p>{error}</p><button className="sl-button" onClick={() => setRetry(value => value + 1)}>重新读取方法</button></div> : !detail ? <div className="sl-empty" role="status">正在读取方法内容…</div> : tab === "overview" ? <>
      <div className="sl-overview-grid"><section className="sl-outline"><div className="sl-section-label">01 / 主方法</div><h2>这份方法如何组织</h2><p>以下章节来自当前 SKILL.md。点击直接阅读，不另行生成一份摘要。</p><div>{sections.map((title, index) => <button key={`${index}-${title}`} onClick={() => jumpTo(index)}><span>{String(index + 1).padStart(2, "0")}</span><strong>{title}</strong><ArrowRight size={14} /></button>)}</div>{!sections.length && <button className="sl-button" onClick={() => openReference("")}>阅读完整方法</button>}</section>
      <section className="sl-branch-overview"><div className="sl-section-label">02 / 按需深入</div><h2>不是每次都走全部分支</h2><p>Agent 根据问题与已取得的证据，选择需要阅读的子方法和参考。这里展示的是方法结构，不是本轮执行记录。</p><div className="sl-branch-root"><BookOpen size={17} /><span>{item.display_name}</span><small>主方法</small></div><div className="sl-branch-list">{references.map(ref => <button key={ref.path} onClick={() => openReference(ref.path)}><span>{ref.title}</span><ChevronRight size={14} /></button>)}{!references.length && <p>当前方法没有单独的参考文件，完整指导在主方法中。</p>}</div></section></div>
      <section className="sl-invocation"><div><div className="sl-section-label">开始使用</div><h2>把方法带进你的问题</h2><p>{detail.default_prompt || `在输入框选择 $${item.skill_name}，再写下分析对象与问题。`}</p></div><button className="sl-button" onClick={async () => { try { await navigator.clipboard.writeText(`$${item.skill_name}`); setCopyMessage("已复制调用标识"); } catch { setCopyMessage("复制未成功，请手动复制右侧调用标识，或使用“用于对话”。"); } }}><Copy size={14} />复制调用标识</button><span role="status">{copyMessage}</span></section>
    </> : tab === "method" ? <div className="sl-method-layout"><nav className="sl-file-nav" aria-label="方法文件"><span>阅读目录</span><button aria-current={!path ? "page" : undefined} onClick={() => openReference("")}><BookOpen size={14} /><span>主方法<small>SKILL.md</small></span></button><span>子方法与参考 · {references.length}</span>{references.map(ref => <button aria-current={path === ref.path ? "page" : undefined} key={ref.path} onClick={() => openReference(ref.path)}><span>{ref.title}<small>{ref.path}</small></span></button>)}</nav><section className="sl-document"><div className="sl-document-bar"><span>{referenceInfo?.title || "主方法"}</span><button className="sl-text-link" aria-pressed={raw} onClick={() => setRaw(value => !value)}>{raw ? "阅读视图" : "查看原文"}</button></div><div className="sl-file-path">{path || "SKILL.md"} <span>只读</span></div>
      {path && referenceError ? <div className="sl-empty" role="alert"><p>{referenceError}</p><p>若发布版本已变化，请刷新整份方法后再读参考。</p><button className="sl-button" onClick={() => setReferenceRetry(value => value + 1)}>重试参考</button><button className="sl-button" onClick={() => { setPath(""); setRetry(value => value + 1); }}>刷新方法</button></div> : path && !reference ? <div className="sl-empty" role="status">正在按需读取参考…</div> : raw ? <pre className="sl-raw" tabIndex={0}>{content}</pre> : <div className="sl-markdown" ref={documentRef}><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        table: ({ children }) => <div className="sl-table-scroll" tabIndex={0}><table>{children}</table></div>,
        a: ({ href = "", children }) => {
          const target = referenceTarget(href, path, references);
          if (target) return <button className="sl-inline-link" onClick={() => openReference(target.path)}>{children}<ChevronRight size={12} /></button>;
          return /^https?:\/\//i.test(href) ? <a href={href} target="_blank" rel="noreferrer">{children}</a> : <span title="此资源未包含在当前可读方法中">{children}</span>;
        },
      }}>{methodBody(content)}</ReactMarkdown></div>}
    </section></div> : <section className="sl-source"><div className="sl-section-label">可追溯的内容</div><h2>你正在阅读哪个版本</h2><p>展示内容直接来自当前账户的授权 Skill 快照。这里不展示未绑定该版本的测试成绩，也不代表本轮已经执行过此方法。</p><dl><dt>Skill ID</dt><dd>{detail.skill_id}</dd><dt>归属</dt><dd>{scopeName(item)}{item.owner === "system" ? " · 平台维护" : " · 用户创建"}</dd><dt>方法内容哈希</dt><dd><code>{detail.content_hash}</code></dd><dt>目录快照</dt><dd><code>{detail.revision}</code></dd><dt>参考文件</dt><dd>{references.length} 份 · 点击阅读时按同一快照校验</dd><dt>附加工具申请</dt><dd>{detail.controls?.supplemental_tools?.join("、") || "未申请额外工具；数据访问沿用 Agent 的授权能力"}</dd><dt>编辑权限</dt><dd>当前阅读空间只读。系统方法不提供普通用户写入入口。</dd><dt>评测证据</dt><dd>此接口未提供版本绑定的评测记录，暂不显示通过率。</dd></dl></section>}
  </div>;
}

export function SkillLibraryDialog({ onClose, onUse }: BrowserProps) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = ref.current;
    dialog?.showModal();
    return () => { dialog?.close(); previous?.focus(); };
  }, []);
  return <dialog ref={ref} className="sl-dialog" aria-label="Skill 方法库" onCancel={event => { event.preventDefault(); onClose?.(); }}><SkillBrowser onClose={onClose} onUse={onUse} /></dialog>;
}

export default function SkillStudio() {
  return <SkillBrowser standalone onUse={skill => {
    window.sessionStorage.setItem("fin_agent.pending_prompt", `$${skill.skill_name} `);
    window.location.assign(appPath("/assistant"));
  }} />;
}
