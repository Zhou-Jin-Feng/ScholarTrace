import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, Quote } from "lucide-react";
import { ApiError, type TokenProvider } from "../../api/client";
import { getClaims, getEvidence } from "../../api/endpoints";
import { VerificationBadge } from "../../components/VerificationBadge";
import { Empty, ErrorNotice, Loading } from "../../components/ui";
import { useResource } from "../../hooks/useResource";

export function EvidencePanel({
  taskId,
  token,
  initialClaim,
}: {
  taskId: string;
  token: TokenProvider;
  initialClaim?: string | null;
}) {
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const load = useCallback(
    async (signal: AbortSignal) => {
      try {
        return await getClaims(token, taskId, signal);
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) return null;
        throw error;
      }
    },
    [token, taskId],
  );
  const { data, error, loading } = useResource(load, revision);
  useEffect(() => {
    if (initialClaim && data)
      setSelected(
        data.claims.find((claim) => claim.claim_id === initialClaim)
          ?.evidence_ids[0] ?? null,
      );
  }, [initialClaim, data]);
  return (
    <section aria-label="论断与证据">
      <div className="section-heading">
        <div>
          <span className="eyebrow">TRACE EVERY CLAIM</span>
          <h2>论断与证据</h2>
        </div>
        <button className="secondary" onClick={() => setRevision((x) => x + 1)}>
          重新读取
        </button>
      </div>
      <ErrorNotice error={error} />
      {loading ? (
        <Loading />
      ) : !data ? (
        <Empty title="尚无结构化研究结果">
          普通 Demo、未运行、失败与业务空结果不同；请查看运行概览中的实际状态。
        </Empty>
      ) : (
        <>
          <p className="muted">
            {data.claims.length} 条论断 · {data.excluded_count}{" "}
            条不支持论断已排除。选择证据查看原文，推断与原文分开展示。
          </p>
          <div className="evidence-layout">
            <div className="claims-list">
              {data.claims.map((claim, index) => (
                <article
                  className={`panel claim-card ${!claim.included_in_report ? "excluded" : ""} ${claim.claim_id === initialClaim ? "from-report" : ""}`}
                  key={claim.claim_id}
                >
                  <div className="claim-top">
                    <span className="eyebrow">
                      CLAIM {String(index + 1).padStart(2, "0")}
                    </span>
                    <span className="pill">
                      {claim.included_in_report ? "收入报告" : "已排除"}
                    </span>
                  </div>
                  <h3>{claim.text}</h3>
                  <VerificationBadge
                    status={claim.verification.status}
                    verifierKind={claim.verification.verifier_kind}
                    marker={claim.verification.marker}
                  />
                  <p className="muted">
                    来源：{claim.verification.verifier_kind} · 原始判定：
                    {claim.verification.status}
                  </p>
                  <p className="muted">
                    子问题：{claim.sub_question ?? "未提供明确归属"}
                  </p>
                  <div className="evidence-links">
                    {claim.evidence_ids.map((id, i) => (
                      <button
                        key={id}
                        className={`evidence-link ${id === selected ? "active" : ""}`}
                        aria-pressed={id === selected}
                        onClick={() => setSelected(id)}
                      >
                        <Quote size={13} /> 证据 {i + 1}
                        <ArrowUpRight size={13} />
                      </button>
                    ))}
                  </div>
                </article>
              ))}
            </div>
            <aside className="panel source-panel" aria-label="证据原文">
              {selected ? (
                <EvidenceDetail
                  key={selected}
                  token={token}
                  taskId={taskId}
                  evidenceId={selected}
                />
              ) : (
                <Empty title="从论断，追溯到原文">
                  点击左侧任意证据。缺失的页码、版本和归属不会被补造。
                </Empty>
              )}
            </aside>
          </div>
        </>
      )}
    </section>
  );
}

export function EvidenceDetail({
  taskId,
  evidenceId,
  token,
}: {
  taskId: string;
  evidenceId: string;
  token: TokenProvider;
}) {
  const load = useCallback(
    (signal: AbortSignal) => getEvidence(token, taskId, evidenceId, signal),
    [token, taskId, evidenceId],
  );
  const { data, error, loading } = useResource(load);
  if (loading) return <Loading />;
  if (!data) return <ErrorNotice error={error} />;
  return (
    <>
      <span className="eyebrow">SOURCE EVIDENCE</span>
      <h3>{data.paper.title}</h3>
      <p className="muted">
        {data.paper.source} · {data.evidence_level} · 版本{" "}
        {data.paper.version ?? "未提供"}
      </p>
      <h4>
        {data.excerpt_is_verbatim ? "原文摘录" : "摘录（未声明为逐字原文）"}
      </h4>
      <blockquote>{data.excerpt}</blockquote>
      <h4>推断说明</h4>
      <p>{data.inference_note ?? "未提供；以上摘录不等于模型推断。"}</p>
      <dl className="metadata">
        <dt>页码</dt>
        <dd>{data.chunk.page_number ?? "未提供"}</dd>
        <dt>字符范围</dt>
        <dd>
          {data.chunk.char_start === null || data.chunk.char_end === null
            ? "未提供"
            : `${data.chunk.char_start}–${data.chunk.char_end}`}
        </dd>
        <dt>Chunk ID</dt>
        <dd>{data.chunk.chunk_id ?? "未提供"}</dd>
        <dt>内容 SHA256</dt>
        <dd>
          <code>{data.chunk.content_sha256 ?? "未提供"}</code>
        </dd>
        <dt>文档归属</dt>
        <dd>{data.binding?.document_key ?? "未绑定"}</dd>
        <dt>DocuMind</dt>
        <dd>{data.binding?.documind_version ?? "未提供"}</dd>
        <dt>索引</dt>
        <dd>{data.binding?.index_id ?? "未提供"}</dd>
        <dt>源 SHA256</dt>
        <dd>
          <code>{data.binding?.source_sha256 ?? "未提供"}</code>
        </dd>
      </dl>
    </>
  );
}
