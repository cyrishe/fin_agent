import { CalendarClock, LogOut, MessageSquarePlus, PanelLeftClose, Search, Sparkles } from "lucide-react";
import type { AuthUser, ThreadSummary } from "../types";

interface Props {
  threads: ThreadSummary[];
  activeId: number | null;
  query: string;
  onQuery: (value: string) => void;
  onSelect: (id: number) => void;
  onNew: () => void;
  onOpenSchedules: () => void;
  onClose?: () => void;
  authUser?: AuthUser | null;
  onLogout?: () => void;
}

function titleOf(thread: ThreadSummary): string {
  const source = String(thread.title || thread.latest_user_input || `会话 ${thread.thread_id}`)
    .replace(/^\/custom_tool\s+(create|edit)\s+/i, "")
    .replace(/^\/[a-z0-9_-]+\s+/i, "")
    .replace(/\s+/g, " ")
    .trim();
  const clause = source.split(/[，。！？!?；;\n]/)[0] || "新对话";
  return Array.from(clause).length > 18 ? `${Array.from(clause).slice(0, 18).join("")}…` : clause;
}

function timeOf(value?: string): string {
  if (!value) return "";
  const date = new Date(value.includes("T") ? value : value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  const now = new Date();
  const diff = Math.floor((new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() - new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()) / 86400000);
  if (diff === 0) return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (diff === 1) return "昨天";
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

export default function Sidebar(props: Props) {
  const filtered = props.threads.filter((thread) => !props.query || [thread.title, thread.latest_user_input, thread.latest_assistant_output].some((value) => String(value || "").toLowerCase().includes(props.query.toLowerCase())));
  return (
    <aside className="sidebar-panel">
      <div className="brand-row"><div className="brand-mark"><Sparkles size={19} /></div><div><strong>Fin Agent</strong><span>金融智能工作台</span></div>{props.onClose && <button className="icon-button sidebar-close mobile-only" onClick={props.onClose} aria-label="关闭会话列表"><PanelLeftClose size={18} /></button>}</div>
      <button className="new-chat" type="button" onClick={props.onNew}><MessageSquarePlus size={17} />新建对话</button>
      <button className="schedule-nav-button" type="button" onClick={props.onOpenSchedules}><CalendarClock size={17} />定时任务</button>
      <label className="history-search"><Search size={16} /><input value={props.query} onChange={(event) => props.onQuery(event.target.value)} placeholder="搜索会话" /></label>
      <div className="history-label">最近对话</div>
      <nav className="thread-list" aria-label="会话历史">
        {filtered.length ? filtered.map((thread) => <button type="button" className={`thread-item ${thread.thread_id === props.activeId ? "active" : ""}`} onClick={() => props.onSelect(thread.thread_id)} key={thread.thread_id}><span>{titleOf(thread)}</span><small>{timeOf(thread.last_event_at || thread.updated_at)}</small></button>) : <div className="sidebar-empty">暂无会话</div>}
      </nav>
      <div className="sidebar-footer">
        <span className="online-dot" />
        {props.authUser ? (
          <>
            <span className="sidebar-identity">{props.authUser.mobile_masked || props.authUser.display_name}</span>
            <button type="button" onClick={props.onLogout} aria-label="退出登录" title="退出登录"><LogOut size={14} /></button>
          </>
        ) : (
          <a href="/register">访客模式 · 注册独立账户</a>
        )}
      </div>
    </aside>
  );
}
