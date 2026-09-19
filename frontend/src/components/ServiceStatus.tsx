import { useEffect, useState } from "react";

import type { DependencyEntry, DependencyReport, DependencyState } from "../api/types";

/**
 * Replaces the hardcoded "API ready" indicator (T16).
 *
 * The old badge was a literal string, so a workspace with no DocuMind, no local
 * model and a closed paid gate still displayed as ready. This reads
 * `GET /health/dependencies` and reports the workspace verdict, not just the
 * fact that the HTTP process answered.
 */

const LABELS: Record<DependencyState, string> = {
  ready: "就绪",
  draining: "停止接单中",
  degraded: "降级",
  disabled: "已禁用",
  unavailable: "不可用",
  unknown: "未探测",
  error: "异常",
};

const DEPENDENCY_LABELS: Record<string, string> = {
  documind: "DocuMind 检索",
  local_model: "本地分析模型",
  search_provider: "学术检索源",
  api_strong: "付费强模型",
};

/** Triple-encoded (symbol + text + colour class) so colour is never the only cue. */
function symbolFor(state: DependencyState): string {
  if (state === "ready") return "✓";
  if (state === "disabled") return "⊘";
  if (state === "unknown") return "?";
  return "⚠";
}

export interface ServiceStatusProps {
  /** Injectable so tests and stories do not need a live backend. */
  fetchReport?: (signal: AbortSignal) => Promise<DependencyReport>;
  pollMs?: number;
}

async function defaultFetch(signal: AbortSignal): Promise<DependencyReport> {
  const response = await fetch("/api/v1/health/dependencies", { signal });
  if (!response.ok) throw new Error(`readiness check failed (${response.status})`);
  return (await response.json()) as DependencyReport;
}

export function ServiceStatus({ fetchReport, pollMs = 30000 }: ServiceStatusProps) {
  const [report, setReport] = useState<DependencyReport | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const load = fetchReport ?? defaultFetch;
    let cancelled = false;
    const controller = new AbortController();

    const run = async () => {
      try {
        const next = await load(controller.signal);
        if (!cancelled) {
          setReport(next);
          setFailed(false);
        }
      } catch {
        // A failed readiness check is itself unknown state, not "ready".
        if (!cancelled) setFailed(true);
      }
    };

    void run();
    const timer = window.setInterval(run, pollMs);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [fetchReport, pollMs]);

  if (failed || report === null) {
    const state: DependencyState = failed ? "unknown" : "unknown";
    return (
      <div className={`service-status tone-${state}`} data-state={state}>
        <span className="status-dot" aria-hidden="true" />
        <span>
          {symbolFor(state)} 状态{failed ? "读取失败" : "读取中"}
        </span>
      </div>
    );
  }

  const entries: Array<[string, DependencyEntry]> = Object.entries(report.dependencies);
  const blocking = report.real_mode.blocking_dependencies.length;

  return (
    <div className={`service-status tone-${report.overall}`} data-state={report.overall}>
      <button
        type="button"
        className="service-status-trigger"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="status-dot" aria-hidden="true" />
        <span>
          {symbolFor(report.overall)} 工作区{LABELS[report.overall]}
        </span>
        {blocking > 0 && <span className="status-count">{blocking} 项未就绪</span>}
      </button>

      {open && (
        <div className="service-status-detail" role="region" aria-label="依赖就绪明细">
          <ul>
            {entries.map(([name, entry]) => (
              <li key={name} data-state={entry.state}>
                <span className="dep-name">{DEPENDENCY_LABELS[name] ?? name}</span>
                <span className={`dep-state tone-${entry.state}`}>
                  {symbolFor(entry.state)} {LABELS[entry.state]}
                </span>
                {entry.reason && <p className="dep-reason">{entry.reason}</p>}
                {entry.remediation && <p className="dep-remediation">{entry.remediation}</p>}
              </li>
            ))}
          </ul>
          <p className="dep-note">
            演示模式{LABELS[report.demo_mode.state]}；真实模式
            {LABELS[report.real_mode.state]}
            {!report.real_mode.pipeline_stages_wired && "（流水线阶段尚未接通）"}
          </p>
          <p className="dep-note dep-policy">{report.probe_policy}</p>
        </div>
      )}
    </div>
  );
}
