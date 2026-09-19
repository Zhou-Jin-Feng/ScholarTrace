import { useCallback, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import type { TokenProvider } from "../../api/client";
import { fetchReport, getClaims, retryExports } from "../../api/endpoints";
import { Empty, ErrorNotice, Loading, messageOf } from "../../components/ui";
import { useResource } from "../../hooks/useResource";

/** A deliberately small, non-HTML Markdown reader. Content cannot execute scripts or fetch images. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      {text.split(/\r?\n/).map((line, i) => {
        if (line.startsWith("### ")) return <h4 key={i}>{line.slice(4)}</h4>;
        if (line.startsWith("## ")) return <h3 key={i}>{line.slice(3)}</h3>;
        if (line.startsWith("# ")) return <h2 key={i}>{line.slice(2)}</h2>;
        if (line.startsWith("- "))
          return (
            <p className="markdown-item" key={i}>
              • {line.slice(2)}
            </p>
          );
        return line ? <p key={i}>{line}</p> : null;
      })}
    </div>
  );
}
export function ReportPanel({
  taskId,
  token,
  onClaim,
}: {
  taskId: string;
  token: TokenProvider;
  onClaim?: (id: string) => void;
}) {
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(
    async (signal: AbortSignal) =>
      (await fetchReport(token, taskId, "markdown", signal)).text(),
    [token, taskId],
  );
  const report = useResource(load, revision);
  const loadClaims = useCallback(
    (signal: AbortSignal) => getClaims(token, taskId, signal),
    [token, taskId],
  );
  const claims = useResource(loadClaims, revision);
  async function download(format: string) {
    setBusy(true);
    setError(null);
    try {
      const blob = await fetchReport(token, taskId, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `scholartrace-${taskId.replace(/[^a-zA-Z0-9-]/g, "_")}.${format === "markdown" ? "md" : format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }
  async function retryReport() {
    setBusy(true);
    setError(null);
    try {
      await retryExports(token, taskId);
      setRevision((x) => x + 1);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel report-panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">RESEARCH OUTPUT</span>
          <h2>研究报告</h2>
        </div>
        <button
          className="text-button"
          onClick={() => setRevision((x) => x + 1)}
        >
          重新读取
        </button>
      </div>
      <div className="export-actions">
        {["pdf", "markdown", "html", "json"].map((format) => (
          <button
            key={format}
            className={format === "pdf" ? "primary" : "secondary"}
            disabled={busy || !report.data}
            onClick={() => void download(format)}
          >
            <Download size={15} />
            {format.toUpperCase()}
          </button>
        ))}
      </div>
      <ErrorNotice error={error} />
      {report.loading ? (
        <Loading />
      ) : report.error ? (
        <Empty title={report.error}>
          任务仍在运行时，请等待导出完成后重新读取。
          <button className="secondary" disabled={busy} onClick={() => void retryReport()}>
            <RefreshCw size={15} /> 重试报告导出
          </button>
        </Empty>
      ) : report.data ? (
        <Markdown text={report.data} />
      ) : (
        <Empty title="暂无报告" />
      )}
      {onClaim && claims.data && (
        <section className="report-audit">
          <h3>核查报告中的论断</h3>
          <p className="muted">
            点击直接打开对应论断及其证据原文；未收入报告的论断仍可在证据视图检查。
          </p>
          {claims.data.claims
            .filter((claim) => claim.included_in_report)
            .map((claim) => (
              <button
                className="audit-link"
                key={claim.claim_id}
                onClick={() => onClaim(claim.claim_id)}
              >
                {claim.text}
                <span>查看证据 →</span>
              </button>
            ))}
        </section>
      )}
    </section>
  );
}
