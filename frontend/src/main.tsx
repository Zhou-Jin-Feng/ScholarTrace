import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  AlertTriangle,
  Ban,
  CheckCircle2,
  Download,
  FileText,
  GitBranch,
  LoaderCircle,
  Play,
  Search,
  ShieldCheck,
  Wifi,
  WifiOff,
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

type WorkflowEvent = {
  event_id: string;
  kind: string;
  node: string;
  created_at: string;
};

type StreamStatus = "idle" | "connecting" | "live" | "reconnecting" | "complete" | "error";

type Readiness = {
  deployment_mode: string;
  auth_required: boolean;
};

const API = "/api/v1";
let activeAuthToken = "";
try {
  activeAuthToken = window.sessionStorage.getItem("scholartrace-auth-token")?.trim() ?? "";
} catch {
  activeAuthToken = "";
}

function withAuthQuery(path: string): string {
  if (!activeAuthToken) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}access_token=${encodeURIComponent(activeAuthToken)}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(activeAuthToken ? { Authorization: `Bearer ${activeAuthToken}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function statusTone(status: string): string {
  if (["completed", "validated", "live", "complete"].includes(status)) return "success";
  if (
    [
      "degraded",
      "limited",
      "compatibility_only",
      "reconnecting",
      "queued",
      "cancelling",
    ].includes(status)
  ) return "warn";
  if (["failed", "blocked", "error"].includes(status)) return "danger";
  return "neutral";
}

function App() {
  const [task, setTask] = useState<Task | null>(null);
  const [matrix, setMatrix] = useState<EvaluationMatrix | null>(null);
  const [question, setQuestion] = useState("How can retrieval evidence improve RAG research reports?");
  const [mode, setMode] = useState("success");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("idle");
  const [streamEvents, setStreamEvents] = useState<WorkflowEvent[]>([]);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [authInput, setAuthInput] = useState(activeAuthToken);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    request<EvaluationMatrix>("/evaluation/m6")
      .then(setMatrix)
      .catch((reason: Error) => setError(reason.message));
    request<Readiness>("/health/ready")
      .then(setReadiness)
      .catch((reason: Error) => setError(reason.message));
  }, []);

  function updateAuthToken(value: string) {
    const token = value.trim();
    activeAuthToken = token;
    setAuthInput(value);
    try {
      if (token) window.sessionStorage.setItem("scholartrace-auth-token", token);
      else window.sessionStorage.removeItem("scholartrace-auth-token");
    } catch {
      // Private browsing may disable sessionStorage; the in-memory token still works.
    }
  }

  useEffect(() => () => eventSourceRef.current?.close(), []);

  const phaseProgress = useMemo(() => {
    const phases = ["plan", "search", "evidence", "citations", "verification", "synthesis", "done"];
    const index = task ? phases.indexOf(task.phase) : 0;
    return Math.max(0, Math.min(100, Math.round((index / (phases.length - 1)) * 100)));
  }, [task]);

  function connectEventStream(taskId: string) {
    eventSourceRef.current?.close();
    setStreamEvents([]);
    setStreamStatus("connecting");
    const source = new EventSource(
      withAuthQuery(
        `${API}/research/tasks/${encodeURIComponent(taskId)}/events?follow=true`,
      ),
    );
    eventSourceRef.current = source;

    let refreshing = false;
    let refreshPending = false;
    const refreshTask = async () => {
      if (refreshing) {
        refreshPending = true;
        return;
      }
      refreshing = true;
      try {
        do {
          refreshPending = false;
          setTask(await request<Task>(`/research/tasks/${taskId}`));
        } while (refreshPending);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Task refresh failed");
      } finally {
        refreshing = false;
      }
    };

    source.onopen = () => setStreamStatus("live");
    source.onerror = () => {
      setStreamStatus(source.readyState === EventSource.CONNECTING ? "reconnecting" : "error");
    };

    const handleWorkflowEvent = (event: Event) => {
      const message = event as MessageEvent<string>;
      try {
        const item = JSON.parse(message.data) as WorkflowEvent;
        if (!item.event_id || !item.kind || !item.node) return;
        setStreamEvents((current) => {
          if (current.some((existing) => existing.event_id === item.event_id)) return current;
          return [...current, item].slice(-8);
        });
        void refreshTask();
      } catch {
        setStreamStatus("error");
      }
    };
    const eventKinds = [
      "task_created",
      "plan_approved",
      "plan_modified",
      "plan_rejected",
      "search_completed",
      "evidence_completed",
      "citations_completed",
      "verification_completed",
      "synthesis_completed",
      "fallback_to_b3",
      "workflow_finished",
      "workflow_degraded",
      "queue_rejected",
      "task_cancel_requested",
      "task_cancelled",
      "task_failed",
      "exports_ready",
    ];
    eventKinds.forEach((kind) => source.addEventListener(kind, handleWorkflowEvent));
    source.addEventListener("stream_end", () => {
      source.close();
      if (eventSourceRef.current === source) eventSourceRef.current = null;
      setStreamStatus("complete");
      void refreshTask();
    });
  }

  async function cancelTask() {
    if (!task || !["queued", "running"].includes(task.status)) return;
    setError(null);
    try {
      setTask(await request<Task>(`/research/tasks/${task.task_id}/cancel`, { method: "POST" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Cancellation failed");
    }
  }

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
      setTask(created);
      connectEventStream(created.task_id);
      const completed = await request<Task>(`/research/tasks/${created.task_id}/approve`, {
        method: "POST",
        body: JSON.stringify({ action: "approve", reason: "workspace demo approval" }),
      });
      setTask(completed);
    } catch (reason) {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      setStreamStatus("error");
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
          <div><span className="eyebrow">M10 RELIABILITY</span><h1>Research run control</h1></div>
          <div className="service-status"><span className="status-dot" /> API ready</div>
        </header>

        <section id="run" className="run-grid">
          <form className="run-panel" onSubmit={createAndRun}>
            <div className="section-heading"><div><span className="eyebrow">START A TASK</span><h2>Research question</h2></div><Search size={20} /></div>
            <label htmlFor="question">Question</label>
            <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} rows={4} />
            {readiness?.auth_required && (
              <div className="auth-field">
                <label htmlFor="auth-token">Private access token</label>
                <input
                  id="auth-token"
                  type="password"
                  autoComplete="current-password"
                  value={authInput}
                  onChange={(event) => updateAuthToken(event.target.value)}
                  placeholder="Bearer token"
                />
              </div>
            )}
            <div className="form-row">
              <div><label htmlFor="mode">Execution profile</label><select id="mode" value={mode} onChange={(event) => setMode(event.target.value)}><option value="success">Deterministic success demo</option><option value="degraded">Bounded fallback demo</option><option value="production_unavailable">Production gate (api-strong disabled)</option></select></div>
              <button className="primary-button" type="submit" disabled={busy || question.trim().length < 3 || (readiness?.auth_required === true && activeAuthToken.length < 16)}>{busy ? <LoaderCircle className="spin" size={17} /> : <Play size={17} />} {busy ? "Running" : "Create and run"}</button>
            </div>
            {error && <div className="error-callout"><AlertTriangle size={17} /> {error}</div>}
          </form>

          <section className="status-panel">
            <div className="section-heading"><div><span className="eyebrow">LATEST RUN</span><h2>{task?.title ?? "No run selected"}</h2></div>{task && (task.status === "completed" ? <CheckCircle2 className="success-icon" /> : <AlertTriangle className="warn-icon" />)}</div>
            {task ? <>
              <div className="status-line"><span className={`status-pill ${statusTone(task.status)}`}>{task.status}</span><span className="task-id">{task.task_id}</span></div>
              {["queued", "running", "cancelling"].includes(task.status) && (
                <button
                  className="outline-button cancel-button"
                  type="button"
                  onClick={() => void cancelTask()}
                  disabled={task.status === "cancelling"}
                >
                  <Ban size={15} /> {task.status === "cancelling" ? "Cancelling" : "Cancel task"}
                </button>
              )}
              <div className="progress-track"><span style={{ width: `${phaseProgress}%` }} /></div>
              <div className="phase-row"><span>{task.phase}</span><span>{phaseProgress}%</span></div>
              <div className="metric-grid"><div><strong>{task.event_count}</strong><span>events</span></div><div><strong>{task.artifact_count}</strong><span>artifacts</span></div><div><strong>{String(task.metrics.evidence_count ?? 0)}</strong><span>evidence</span></div><div><strong>{String(task.metrics.citation_edge_count ?? 0)}</strong><span>citation edges</span></div></div>
              <div className="stream-state-row"><span>Event stream</span><span className={`stream-state ${statusTone(streamStatus)}`}>{streamStatus === "error" ? <WifiOff size={13} /> : <Wifi size={13} />}{streamStatus}</span></div>
              {streamEvents.length > 0 && <ol className="event-timeline">{streamEvents.map((item) => <li key={item.event_id}><span>{item.kind.replaceAll("_", " ")}</span><small>{item.node}</small></li>)}</ol>}
            </> : <div className="empty-state"><FileText size={30} /><p>Run a bounded task to inspect its timeline and exportable artifacts.</p></div>}
          </section>
        </section>

        {task && <section id="evidence" className="detail-panel"><div className="section-heading"><div><span className="eyebrow">RUN DETAIL</span><h2>Evidence and delivery state</h2></div><a className="outline-button" href={withAuthQuery(`${API}/research/tasks/${task.task_id}/report?format=markdown`)}><Download size={16} /> Markdown</a></div><div className="detail-grid"><div><h3>Question</h3><p>{task.question}</p><h3>Execution mode</h3><p className="muted">{String(task.metrics.execution_mode ?? task.demo_mode)}</p></div><div><h3>Safety notes</h3>{task.degradations.length ? <ul className="warning-list">{task.degradations.map((item) => <li key={item}><AlertTriangle size={15} />{item}</li>)}</ul> : <p className="safe-line"><ShieldCheck size={16} /> No runtime degradation recorded.</p>}<div className="export-links"><a href={withAuthQuery(`${API}/research/tasks/${task.task_id}/report?format=html`)}><Download size={14} /> HTML</a><a href={withAuthQuery(`${API}/research/tasks/${task.task_id}/report?format=pdf`)}><Download size={14} /> PDF</a><a href={withAuthQuery(`${API}/research/tasks/${task.task_id}/report?format=json`)}><Download size={14} /> JSON</a></div></div></div></section>}

        <section id="evaluation" className="detail-panel evaluation-panel"><div className="section-heading"><div><span className="eyebrow">B0-B4 EVALUATION</span><h2>Delivery evidence matrix</h2></div><Activity size={20} /></div>{matrix ? <><div className="matrix-status"><span className={`status-pill ${statusTone(matrix.overall_status)}`}>{matrix.overall_status.replaceAll("_", " ")}</span><span className="muted">Blind review found no B4 quality gain; ScholarGraph remains opt-in.</span></div><div className="matrix-table"><div className="matrix-head"><span>Phase</span><span>Evidence</span><span>Quality</span><span>Status</span></div>{matrix.phases.map((item) => <div className="matrix-row" key={item.phase}><strong>{item.phase}</strong><span>{item.evidence_report.split("/").pop()}</span><span>{item.quality_evidence}</span><span className={`status-text ${statusTone(item.status)}`}>{item.status.replaceAll("_", " ")}</span></div>)}</div>{matrix.blocking_reasons.length > 0 && <div className="matrix-note"><AlertTriangle size={16} /> {matrix.blocking_reasons[0]}</div>}</> : <div className="loading-bar" />}</section>
      </main>
    </div>
  );
}

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("ScholarTrace root element is missing");
createRoot(rootElement).render(<App />);

export default App;
