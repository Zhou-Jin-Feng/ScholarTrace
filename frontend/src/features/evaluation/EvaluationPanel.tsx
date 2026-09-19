import { useCallback, useState } from "react";
import { apiRequest, type TokenProvider } from "../../api/client";
import { ErrorNotice, Loading } from "../../components/ui";
import { useResource } from "../../hooks/useResource";

interface EvaluationMatrix {
  overall_status: string;
  phases: Array<{
    phase: string;
    status: string;
    quality_evidence: string;
    evidence_report: string;
  }>;
  blocking_reasons: string[];
  limitations: string[];
}

/** Historical evidence is not a readiness signal for the current task. */
export function EvaluationPanel({ token }: { token: TokenProvider }) {
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) =>
      apiRequest<EvaluationMatrix>("/evaluation/m6", token, { signal }),
    [token],
  );
  const { data, error, loading } = useResource(load, revision);
  return (
    <section className="panel evaluation-panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">HISTORICAL EVIDENCE</span>
          <h1>评估记录与能力边界</h1>
        </div>
        <button className="secondary" onClick={() => setRevision((x) => x + 1)}>
          重新读取
        </button>
      </div>
      <p className="notice">
        历史评估不代表当前任务或真实生产环境已通过验收。ScholarGraph
        默认关闭，不能将兼容性结果解释成研究质量提升。
      </p>
      <ErrorNotice error={error} />
      {loading && <Loading />}
      {data && (
        <>
          <p>
            记录状态：<span className="pill">{data.overall_status}</span>
          </p>
          <div className="evaluation-grid">
            {data.phases.map((phase) => (
              <article key={phase.phase}>
                <h2>{phase.phase}</h2>
                <dl className="metadata">
                  <dt>状态</dt>
                  <dd>{phase.status}</dd>
                  <dt>质量证据</dt>
                  <dd>{phase.quality_evidence}</dd>
                  <dt>证据文件</dt>
                  <dd>{phase.evidence_report}</dd>
                </dl>
              </article>
            ))}
          </div>
          <h3>阻断与限制</h3>
          <ul>
            {[...data.blocking_reasons, ...data.limitations].map(
              (reason, i) => (
                <li key={i}>{reason}</li>
              ),
            )}
          </ul>
        </>
      )}
    </section>
  );
}
