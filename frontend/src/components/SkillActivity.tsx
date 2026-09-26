import { BookOpen } from "lucide-react";
import type { AgentRun, UnknownRecord } from "../types";
import { appPath } from "../appPath";

export function loadedSkills(run: AgentRun, payload?: UnknownRecord) {
  const result = new Map<string, string>();
  const financial = payload?.financial_qa as UnknownRecord | undefined;
  const entries = financial?.skill_entries;
  if (Array.isArray(entries)) for (const entry of entries) {
    if (entry && typeof entry.skill_id === "string") result.set(entry.skill_id, String(entry.display_name || entry.skill_id));
  }
  for (const block of run.process) {
    const data = block.data;
    if (data?.status === "completed" && typeof data.skill_id === "string") {
      result.set(data.skill_id, String(data.display_name || data.skill_id));
    }
  }
  return [...result].map(([id, name]) => ({ id, name }));
}

export default function SkillActivity({ run, payload }: { run: AgentRun; payload?: UnknownRecord }) {
  const skills = loadedSkills(run, payload);
  if (!skills.length) return null;
  return <section className="skill-activity" aria-label="本轮使用的专业方法">
    <div className="skill-activity-heading"><BookOpen size={17} aria-hidden="true" /><strong>专业方法</strong><small>SKILL</small>
      <span>{run.status === "running" ? "分析进行中" : "本轮已加载"}</span></div>
    <div className="skill-activity-methods">{skills.map(skill => <a key={skill.id} href={appPath(`/skills/studio/${encodeURIComponent(skill.id)}`)} target="_blank" rel="noreferrer" title={`查看 ${skill.name} 的当前方法内容`}>{skill.name}</a>)}</div>
    <p>方法指导分析，数据工具提供证据。执行记录可在本轮过程查看。</p>
  </section>;
}
