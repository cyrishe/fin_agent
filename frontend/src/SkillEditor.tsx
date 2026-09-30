import { useEffect, useState } from "react";
import { activateSkillDefinition, getSkillDefinition, saveSkillDefinition, type SkillDefinition } from "./skillLibrary";

export default function SkillEditor({ skillId, onClose, onActivated }: { skillId: string; onClose: () => void; onActivated: () => void }) {
  const [base, setBase] = useState<SkillDefinition | null>(null);
  const [name, setName] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [references, setReferences] = useState<Record<string, string>>({});
  const [control, setControl] = useState("{}");
  const [newPath, setNewPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    getSkillDefinition(skillId, controller.signal).then(({ candidate }) => {
      if (controller.signal.aborted) return;
      setBase(candidate); setName(candidate.display_name); setMarkdown(candidate.skill_markdown);
      setReferences(candidate.references || {}); setControl(JSON.stringify(candidate.control_manifest || {}, null, 2));
      setDirty(false);
    }).catch(reason => { if (!controller.signal.aborted) setError(String(reason.message || reason)); });
    return () => controller.abort();
  }, [skillId, retry]);
  const run = async (activate: boolean) => {
    if (!base || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = activate ? await activateSkillDefinition(base) : await saveSkillDefinition(base, {
        display_name: name, skill_markdown: markdown, references, control_manifest: JSON.parse(control),
      });
      setBase(result.candidate); setDirty(false);
      setMessage(activate ? "已启用，后续分析使用此版本。" : "新版本已保存，启用后生效。");
      if (activate) onActivated();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); }
    finally { setBusy(false); }
  };
  return <section className="sl-editor" aria-label="编辑 Skill">
    <header><div><h2>编辑方法</h2><p>修改业务定义、处理方法和参考；保存不会覆盖当前启用版本。</p></div><button className="sl-button" disabled={busy} onClick={onClose}>返回阅读</button></header>
    {error && <div role="alert"><p>{error}</p><button className="sl-button" disabled={busy} onClick={() => setRetry(v => v + 1)}>重新读取版本（放弃未保存修改）</button></div>}
    {!base ? <p role="status">正在读取编辑版本…</p> : <>
      <p>当前启用：{base.active_revision_no ? `v${base.active_revision_no}` : "系统初始版本"} · 编辑版本：{base.candidate_revision_no ? `v${base.candidate_revision_no}` : "系统初始版本"}</p>
      <fieldset disabled={busy}>
        <label>显示名称<input value={name} onChange={e => { setName(e.target.value); setDirty(true); }} /></label>
        <label>业务定义与处理方法 · SKILL.md<textarea rows={20} value={markdown} onChange={e => { setMarkdown(e.target.value); setDirty(true); }} /></label>
        <p>name 是稳定的调用标识，请保留；description 描述用途。关联方法、数据口径和分析步骤可直接写在正文中。</p>
        <h3>参考文件</h3>
        {Object.entries(references).map(([path, content]) => <details key={path}><summary>{path}</summary><label>{path}<textarea rows={12} value={content} onChange={e => { setReferences(old => ({ ...old, [path]: e.target.value })); setDirty(true); }} /></label><button className="sl-button" onClick={() => { setReferences(old => Object.fromEntries(Object.entries(old).filter(([key]) => key !== path))); setDirty(true); }}>移除此参考</button></details>)}
        <div className="sl-editor-add"><label>新增参考路径<input placeholder="references/method.md" value={newPath} onChange={e => setNewPath(e.target.value)} /></label><button className="sl-button" onClick={() => { const path = newPath.trim(); if (!/^references\/.+/.test(path) || path.split("/").includes("..") || path in references) { setError("请填写未使用的 references/… 路径。"); return; } setReferences(old => ({ ...old, [path]: "" })); setNewPath(""); setDirty(true); }}>添加参考</button></div>
        <details><summary>高级：已保存的关联方法与工具配置</summary><p>普通方法可只编辑正文。这里保留已有结构化关联，不额外授予工具权限。</p><label>关联配置 JSON<textarea rows={12} value={control} onChange={e => { setControl(e.target.value); setDirty(true); }} /></label></details>
      </fieldset>
      <div className="sl-editor-actions"><button className="sl-button sl-primary" disabled={busy || !dirty} onClick={() => run(false)}>{busy ? "处理中…" : "保存新版本"}</button><button className="sl-button" disabled={busy || dirty || !base.candidate_revision_no || base.candidate_revision_no === base.active_revision_no} onClick={() => run(true)}>启用已保存版本</button><span role="status">{dirty ? "有未保存修改" : message}</span></div>
    </>}
  </section>;
}
