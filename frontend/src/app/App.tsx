import { useCallback, useEffect, useState } from "react";
import {
  BookOpen,
  GitBranch,
  KeyRound,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { ServiceStatus } from "../components/ServiceStatus";
import { CreateTask } from "../features/tasks/CreateTask";
import { TaskHistory } from "../features/tasks/TaskHistory";
import { TaskWorkspace } from "../features/tasks/TaskWorkspace";
import { EvaluationPanel } from "../features/evaluation/EvaluationPanel";

function selectedFromLocation() {
  const value = new URLSearchParams(window.location.hash.slice(1)).get("task");
  return value && /^[a-zA-Z0-9:_-]{1,200}$/.test(value) ? value : null;
}
export default function App() {
  const [selected, setSelected] = useState<string | null>(selectedFromLocation);
  const [revision, setRevision] = useState(0);
  const [auth, setAuth] = useState("");
  const [authRevision, setAuthRevision] = useState(0);
  const [draft, setDraft] = useState("");
  const [showAuth, setShowAuth] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(true);
  const [showEvaluation, setShowEvaluation] = useState(false);
  const token = useCallback(() => auth || null, [auth]);
  const refresh = useCallback(() => setRevision((x) => x + 1), []);
  useEffect(() => {
    const changed = () => setSelected(selectedFromLocation());
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  function select(id: string | null) {
    setShowEvaluation(false);
    window.location.hash = id
      ? new URLSearchParams({ task: id }).toString()
      : "";
    setSelected(id);
  }
  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        跳转到主内容
      </a>
      <header className="topbar">
        <a href="#" className="brand" onClick={() => select(null)}>
          <span className="brand-icon">
            <GitBranch size={22} />
          </span>
          <span>
            Scholar<span className="brand-light">Trace</span>
            <small>循证研究工作台</small>
          </span>
        </a>
        <div className="topbar-actions">
          <button
            className="text-button evaluation-trigger"
            aria-pressed={showEvaluation}
            onClick={() => setShowEvaluation((x) => !x)}
          >
            评估记录
          </button>
          <ServiceStatus />
          <button
            className="icon-button"
            aria-label="访问令牌设置"
            aria-expanded={showAuth}
            onClick={() => setShowAuth((x) => !x)}
          >
            <KeyRound size={18} />
          </button>
        </div>
      </header>
      {showAuth && (
        <form
          className="auth-panel"
          onSubmit={(event) => {
            event.preventDefault();
            setAuth(draft.trim());
            setAuthRevision((x) => x + 1);
            setRevision((x) => x + 1);
            setShowAuth(false);
          }}
        >
          <label htmlFor="access-token">
            访问令牌 · 仅保存在当前页面内存中
          </label>
          <div className="actions">
            <input
              id="access-token"
              type="password"
              autoComplete="off"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            <button className="primary">应用</button>
            <button
              className="secondary"
              type="button"
              onClick={() => {
                setAuth("");
                setDraft("");
                setAuthRevision((x) => x + 1);
                setShowAuth(false);
              }}
            >
              清除
            </button>
          </div>
        </form>
      )}
      <div className="workspace-bar">
        <span>
          <BookOpen size={15} /> 我的研究空间
        </span>
        <button
          className="text-button"
          aria-expanded={historyOpen}
          onClick={() => setHistoryOpen((x) => !x)}
        >
          {historyOpen ? (
            <PanelLeftClose size={16} />
          ) : (
            <PanelLeftOpen size={16} />
          )}
          {historyOpen ? "收起任务列表" : "展开任务列表"}
        </button>
      </div>
      <div className={`body-grid ${historyOpen ? "" : "history-hidden"}`}>
        {historyOpen && (
          <aside className="history-sidebar">
            <TaskHistory
              token={token}
              selected={selected}
              revision={revision}
              onSelect={select}
              onCreate={() => select(null)}
            />
            <div className="sidebar-footer">
              <span className="status-dot" /> 证据优先 · 决策可追溯
              <small>ScholarGraph 默认关闭</small>
            </div>
          </aside>
        )}
        <main id="main" tabIndex={-1}>
          {showEvaluation ? (
            <EvaluationPanel token={token} />
          ) : selected ? (
            <TaskWorkspace
              key={`${selected}:${authRevision}`}
              taskId={selected}
              token={token}
              onChange={refresh}
            />
          ) : (
            <CreateTask
              key={authRevision}
              token={token}
              onCreated={(id) => {
                select(id);
                refresh();
              }}
            />
          )}
        </main>
      </div>
      <footer className="page-footer">
        <span>ScholarTrace</span>
        <span>研究不止于答案，更在于证据。</span>
        <span>1.0.0 · Local</span>
      </footer>
    </div>
  );
}
