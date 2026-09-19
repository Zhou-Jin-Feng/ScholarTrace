import { useCallback, useEffect, useRef, useState } from "react";
import { Ban, RefreshCw, RotateCcw } from "lucide-react";
import type { TokenProvider } from "../../api/client";
import {
  cancelTask,
  getBudget,
  getPlan,
  getTask,
  getTimeline,
  resumeTask,
} from "../../api/endpoints";
import type { BudgetView, TaskListItem } from "../../api/types";
import {
  Empty,
  ErrorNotice,
  Loading,
  messageOf,
  Status,
} from "../../components/ui";
import { useTaskEvents, type TaskEvent } from "../../hooks/useTaskEvents";
import { EvidencePanel } from "../evidence/EvidencePanel";
import { PlanPanel } from "../plans/PlanPanel";
import { ReportPanel } from "../reports/ReportPanel";
import { mergeTimeline, RunOverview } from "./RunOverview";

const tabs = [
  ["overview", "运行概览"],
  ["plan", "研究计划"],
  ["report", "研究报告"],
  ["evidence", "论断与证据"],
] as const;
type Tab = (typeof tabs)[number][0];
export function TaskWorkspace({
  taskId,
  token,
  onChange,
}: {
  taskId: string;
  token: TokenProvider;
  onChange: () => void;
}) {
  const [task, setTask] = useState<TaskListItem | null>(null);
  const [budget, setBudget] = useState<BudgetView | null>(null);
  const [savedEvents, setSavedEvents] = useState<TaskEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("plan");
  const [selectedClaim, setSelectedClaim] = useState<string | null>(null);
  const previousStatus = useRef<string>();
  const [resumeConfirm, setResumeConfirm] = useState(false);
  const initialized = useRef(false);
  const controller = useRef<AbortController>();
  const mounted = useRef(true);
  const stream = useTaskEvents(taskId, token);
  const refresh = useCallback(async () => {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    try {
      const [next, nextBudget, timeline] = await Promise.all([
        getTask(token, taskId, request.signal),
        getBudget(token, taskId, request.signal),
        getTimeline(token, taskId, request.signal),
      ]);
      if (request.signal.aborted || !mounted.current) return;
      setTask(next);
      setBudget(nextBudget);
      setSavedEvents(timeline.events);
      setError(null);
      if (!initialized.current) {
        setTab(
          ["created", "waiting_approval"].includes(next.status)
            ? "plan"
            : "overview",
        );
        initialized.current = true;
      }
    } catch (reason) {
      if (!request.signal.aborted && mounted.current)
        setError(messageOf(reason));
    }
  }, [taskId, token]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, [refresh]);
  useEffect(() => {
    if (stream.lastSequence !== null) void refresh();
  }, [stream.lastSequence, refresh]);
  useEffect(() => {
    if (
      task &&
      previousStatus.current &&
      previousStatus.current !== task.status
    ) {
      onChange();
      if (["created", "waiting_approval"].includes(previousStatus.current))
        setTab("overview");
    }
    previousStatus.current = task?.status;
  }, [task?.status, onChange]);
  useEffect(() => {
    if (!task || !["queued", "running", "cancelling"].includes(task.status))
      return;
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [task?.status, refresh]);
  async function mutate(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
      if (mounted.current) {
        await refresh();
        onChange();
      }
    } catch (reason) {
      if (mounted.current) setError(messageOf(reason));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  const changed = () => {
    void refresh();
    onChange();
  };
  return (
    <div className="workspace">
      <ErrorNotice error={error} />
      {!task ? (
        <>
          {error ? (
            <Empty title="任务无法读取">
              请检查任务是否存在，以及当前页面的访问令牌。
            </Empty>
          ) : (
            <Loading />
          )}
          <button className="secondary" onClick={() => void refresh()}>
            重新读取任务
          </button>
        </>
      ) : (
        <>
          <header className="task-heading">
            <div className="task-heading-meta">
              <span className="eyebrow">RESEARCH WORKSPACE</span>
              <Status value={task.status} />
              <span className="pill">
                {task.execution_mode === "demo"
                  ? "Demo · 非真实模型研究"
                  : "Real"}
              </span>
            </div>
            <h1>{task.title}</h1>
            <p>{task.question}</p>
            <code className="task-id">{task.task_id}</code>
            <div className="task-actions">
              <span className="muted">当前阶段：{task.phase}</span>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void refresh()}
              >
                <RefreshCw size={14} /> 刷新
              </button>
              {["queued", "running", "cancelling"].includes(task.status) && (
                <button
                  className="danger-button"
                  disabled={busy || task.status === "cancelling"}
                  onClick={() => void mutate(() => cancelTask(token, taskId))}
                >
                  <Ban size={14} />
                  {task.status === "cancelling" ? "取消中…" : "取消任务"}
                </button>
              )}
              {task.status === "interrupted" && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => setResumeConfirm((x) => !x)}
                >
                  <RotateCcw size={14} /> 查看恢复条件
                </button>
              )}
            </div>
            {resumeConfirm && (
              <div className="notice">
                <div>
                  <p>
                    仅按原批准计划尝试恢复，最多两次。服务端将核对原授权、截止时间和已记录的操作；
                    未知外部结果须先核对，不会自动重新发送，也不会增加额度或延长授权。
                  </p>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      void mutate(async () => {
                        const plan = await getPlan(token, taskId);
                        await resumeTask(token, taskId, plan);
                        setResumeConfirm(false);
                        stream.retryNow();
                      })
                    }
                  >
                    按原批准计划尝试恢复
                  </button>
                </div>
              </div>
            )}
          </header>
          <nav className="workspace-tabs" aria-label="任务视图">
            {tabs.map(([id, label]) => (
              <button
                key={id}
                aria-current={tab === id ? "page" : undefined}
                className={tab === id ? "active" : ""}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          <div className="tab-content">
            {tab === "plan" && (
              <PlanPanel key={task.task_id} token={token} task={task} onChange={changed} />
            )}
            {tab === "overview" && (
              <RunOverview
                task={task}
                budget={budget}
                stream={stream}
                events={mergeTimeline(savedEvents, stream.events)}
              />
            )}
            {tab === "report" && (
              <ReportPanel
                token={token}
                taskId={taskId}
                onClaim={(id) => {
                  setSelectedClaim(id);
                  setTab("evidence");
                }}
              />
            )}
            {tab === "evidence" && (
              <EvidencePanel
                token={token}
                taskId={taskId}
                initialClaim={selectedClaim}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
