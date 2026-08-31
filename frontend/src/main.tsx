import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Download,
  FileText,
  GitBranch,
  LoaderCircle,
  Play,
  Search,
  ShieldCheck,
} from "lucide-react";
import "./styles.css";

type Task = {
  task_id: string;
  title: string;
  question: string;
  status: string;
  phase: string;
  demo_mode: string;
  event_count: number;
  artifact_count: number;
  metrics: Record<string, unknown>;
  degradations: string[];
};

type EvaluationMatrix = {
  overall_status: string;
  phases: Array<{
    phase: string;
    status: string;
    quality_evidence: string;
    evidence_report: string;
  }>;
  blocking_reasons: string[];
};

const API = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function statusTone(status: string): string {
  if (status === "completed" || status === "validated") return "success";
  if (status === "degraded" || status === "limited" || status === "compatibility_only") return "warn";
  if (status === "failed" || status === "blocked") return "danger";
  return "neutral";
}

function App() {
  const [task, setTask] = useState<Task | null>(null);
  const [matrix, setMatrix] = useState<EvaluationMatrix | null>(null);
  const [question, setQuestion] = useState("How can retrieval evidence improve RAG research reports?");
  const [mode, setMode] = useState("success");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    request<EvaluationMatrix>("/evaluation/m6")
      .then(setMatrix)
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const phaseProgress = useMemo(() => {
    const phases = ["plan", "search", "evidence", "citations", "verification", "synthesis", "done"];
    const index = task ? phases.indexOf(task.phase) : 0;
    return Math.max(0, Math.min(100, Math.round((index / (phases.length - 1)) * 100)));
  }, [task]);

  async function createAndRun(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await request<Task>("/research/tasks", {
        method: "POST",
        headers: { "Idempotency-Key": `workspace-${Date.now()}` },
        body: JSON.stringify({ question, demo_mode: mode }),
      });
      const completed = await request<Task>(`/research/tasks/${created.task_id}/approve`, {
        method: "POST",
        body: JSON.stringify({ action: "approve", reason: "workspace demo approval" }),
      });
      setTask(completed);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unexpected request failure");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-mark"><GitBranch size={20} /><span>ScholarTrace</span></div>
        <div className="workspace-label">RESEARCH WORKSPACE</div>
        <nav>
          <a className="nav-item active" href="#run"><Play size={16} /> New run</a>
          <a className="nav-item" href="#evaluation"><Activity size={16} /> Evaluation</a>
          <a className="nav-item" href="#evidence"><ShieldCheck size={16} /> Evidence policy</a>
        </nav>
        <div className="sidebar-note">
          <span className="live-dot" /> Local delivery mode
          <small>API and report artifacts stay on this machine.</small>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div><span className="eyebrow">M6 DELIVERY</span><h1>Research run control</h1></div>
          <div className="service-status"><span className="status-dot" /> API ready</div>
        </header>

        <section id="run" className="run-grid">
          <form className="run-panel" onSubmit={createAndRun}>
            <div className="section-heading"><div><span className="eyebrow">START A TASK</span><h2>Research question</h2></div><Search size={20} /></div>
            <label htmlFor="question">Question</label>
            <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} rows={4} />
            <div className="form-row">
              <div><label htmlFor="mode">Execution profile</label><select id="mode" value={mode} onChange={(event) => setMode(event.target.value)}><option value="success">Deterministic success demo</option><option value="degraded">Bounded fallback demo</option><option value="production_unavailable">Production gate (api-strong disabled)</option></select></div>
              <button className="primary-button" type="submit" disabled={busy || question.trim().length < 3}>{busy ? <LoaderCircle className="spin" size={17} /> : <Play size={17} />} {busy ? "Running" : "Create and run"}</button>
            </div>
            {error && <div className="error-callout"><AlertTriangle size={17} /> {error}</div>}
          </form>

          <section className="status-panel">
            <div className="section-heading"><div><span className="eyebrow">LATEST RUN</span><h2>{task?.title ?? "No run selected"}</h2></div>{task && (task.status === "completed" ? <CheckCircle2 className="success-icon" /> : <AlertTriangle className="warn-icon" />)}</div>
            {task ? <>
              <div className="status-line"><span className={`status-pill ${statusTone(task.status)}`}>{task.status}</span><span className="task-id">{task.task_id}</span></div>
              <div className="progress-track"><span style={{ width: `${phaseProgress}%` }} /></div>
              <div className="phase-row"><span>{task.phase}</span><span>{phaseProgress}%</span></div>
              <div className="metric-grid"><div><strong>{task.event_count}</strong><span>events</span></div><div><strong>{task.artifact_count}</strong><span>artifacts</span></div><div><strong>{String(task.metrics.evidence_count ?? 0)}</strong><span>evidence</span></div><div><strong>{String(task.metrics.citation_edge_count ?? 0)}</strong><span>citation edges</span></div></div>
            </> : <div className="empty-state"><FileText size={30} /><p>Run a bounded task to inspect its timeline and exportable artifacts.</p></div>}
          </section>
        </section>

        {task && <section id="evidence" className="detail-panel"><div className="section-heading"><div><span className="eyebrow">RUN DETAIL</span><h2>Evidence and delivery state</h2></div><a className="outline-button" href={`${API}/research/tasks/${task.task_id}/report?format=markdown`}><Download size={16} /> Markdown</a></div><div className="detail-grid"><div><h3>Question</h3><p>{task.question}</p><h3>Execution mode</h3><p className="muted">{String(task.metrics.execution_mode ?? task.demo_mode)}</p></div><div><h3>Safety notes</h3>{task.degradations.length ? <ul className="warning-list">{task.degradations.map((item) => <li key={item}><AlertTriangle size={15} />{item}</li>)}</ul> : <p className="safe-line"><ShieldCheck size={16} /> No runtime degradation recorded.</p>}<div className="export-links"><a href={`${API}/research/tasks/${task.task_id}/report?format=html`}><Download size={14} /> HTML</a><a href={`${API}/research/tasks/${task.task_id}/report?format=pdf`}><Download size={14} /> PDF</a><a href={`${API}/research/tasks/${task.task_id}/report?format=json`}><Download size={14} /> JSON</a></div></div></div></section>}

        <section id="evaluation" className="detail-panel evaluation-panel"><div className="section-heading"><div><span className="eyebrow">B0-B4 EVALUATION</span><h2>Delivery evidence matrix</h2></div><Activity size={20} /></div>{matrix ? <><div className="matrix-status"><span className={`status-pill ${statusTone(matrix.overall_status)}`}>{matrix.overall_status.replaceAll("_", " ")}</span><span className="muted">Blind review found no B4 quality gain; ScholarGraph remains opt-in.</span></div><div className="matrix-table"><div className="matrix-head"><span>Phase</span><span>Evidence</span><span>Quality</span><span>Status</span></div>{matrix.phases.map((item) => <div className="matrix-row" key={item.phase}><strong>{item.phase}</strong><span>{item.evidence_report.split("/").pop()}</span><span>{item.quality_evidence}</span><span className={`status-text ${statusTone(item.status)}`}>{item.status.replaceAll("_", " ")}</span></div>)}</div>{matrix.blocking_reasons.length > 0 && <div className="matrix-note"><AlertTriangle size={16} /> {matrix.blocking_reasons[0]}</div>}</> : <div className="loading-bar" />}</section>
      </main>
    </div>
  );
}

export default App;
