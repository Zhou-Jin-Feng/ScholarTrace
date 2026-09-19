import { useCallback, useEffect, useRef, useState } from "react";
import { Check, FileCheck2, Pencil, X } from "lucide-react";
import { ApiError, type TokenProvider } from "../../api/client";
import {
  approvePlan,
  estimatePlan,
  generatePlan,
  getPlan,
  getPlanVersions,
} from "../../api/endpoints";
import type {
  ApprovalPayload,
  ResearchPlanView,
  TaskListItem,
} from "../../api/types";
import { ErrorNotice, Loading, messageOf } from "../../components/ui";
import { useResource } from "../../hooks/useResource";
import { LiveAuthorization } from "./LiveAuthorization";

type PlanPanelProps = {
  token: TokenProvider;
  task: TaskListItem;
  onChange: () => void;
};

export function PlanPanel(props: PlanPanelProps) {
  return <TaskPlanPanel key={props.task.task_id} {...props} />;
}

function TaskPlanPanel({
  token,
  task,
  onChange,
}: PlanPanelProps) {
  const active = useRef(false);
  const inFlight = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    async (signal: AbortSignal) => {
      try {
        return await getPlan(token, task.task_id, signal);
      } catch (error) {
        if (
          error instanceof ApiError &&
          (error.code as string) === "plan_not_found"
        )
          return null;
        throw error;
      }
    },
    [token, task.task_id],
  );
  const { data: plan, error: readError, loading } = useResource(load, revision);
  const [estimate, setEstimate] = useState<Awaited<
    ReturnType<typeof estimatePlan>
  > | null>(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [versions, setVersions] = useState<ResearchPlanView[] | null>(null);
  const waiting = ["created", "waiting_approval"].includes(task.status);
  async function action(work: () => Promise<unknown>, success = "") {
    if (inFlight.current || !active.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      await work();
      if (!active.current) return;
      setNotice(success);
      setEditing(false);
      setRevision((x) => x + 1);
      onChange();
    } catch (reason) {
      if (!active.current) return;
      if (reason instanceof ApiError && reason.isStalePlan) {
        setRevision((x) => x + 1);
        setEditing(false);
        setVersions(null);
        setError("计划已变更，已重新读取。请重新审阅后再确认；未自动批准。");
      } else setError(messageOf(reason));
    } finally {
      inFlight.current = false;
      if (active.current) setBusy(false);
    }
  }
  function decide(
    kind: ApprovalPayload["action"],
    modified?: ApprovalPayload["modified_plan"],
  ) {
    if (!plan) return;
    void action(
      () =>
        approvePlan(token, task.task_id, {
          action: kind,
          plan_version: plan.plan_version,
          plan_digest: plan.plan_digest,
          ...(modified ? { modified_plan: modified } : {}),
        }),
      kind === "modify" ? "新版本已保存，仍待审批，尚未执行。" : "决定已保存。",
    );
  }
  if (loading) return <Loading />;
  return (
    <section className="panel plan-panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">REVIEW BEFORE RUN</span>
          <h2>研究计划</h2>
        </div>
        <FileCheck2 size={22} />
      </div>
      <ErrorNotice error={readError || error} />
      {readError && (
        <button className="secondary" onClick={() => setRevision((x) => x + 1)}>
          重新读取计划
        </button>
      )}
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {!plan && !readError && task.execution_mode === "real" && waiting && (
        <LiveAuthorization token={token} taskId={task.task_id} busy={busy}
          run={work => void action(work)} />
      )}
      {!plan && !readError && task.execution_mode !== "real" && (
        <>
          <h3>第一道确认 · 规划费用</h3>
          <p className="muted">
            先读取生成计划的估算，再决定是否生成。生成计划不等于批准执行。
          </p>
          {!estimate ? (
            <button
              className="secondary"
              disabled={busy || !waiting}
              onClick={() =>
                void action(async () =>
                  setEstimate(await estimatePlan(token, task.task_id)),
                )
              }
            >
              查看规划费用估算
            </button>
          ) : (
            <div className="cost-gate">
              <strong>
                {estimate.estimated_cny === null
                  ? "费用未知，暂不可确认"
                  : `预计 ¥${estimate.estimated_cny.toFixed(2)}`}
              </strong>
              <p>来源：{estimate.estimate_source} · 非实际账单</p>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={ack}
                  onChange={(event) => setAck(event.target.checked)}
                />{" "}
                我已知悉本次规划估算，确认生成计划
              </label>
              <button
                className="primary"
                disabled={
                  busy || !ack || estimate.estimated_cny === null || !waiting
                }
                onClick={() =>
                  void action(() =>
                    generatePlan(token, task.task_id, estimate.estimated_cny!),
                  )
                }
              >
                确认并生成计划
              </button>
            </div>
          )}
        </>
      )}
      {plan && (
        <>
          <div className="plan-meta">
            <span className="pill">版本 {plan.plan_version}</span>
            <span className="pill">
              {plan.generated_by === "fixture"
                ? "Fixture · 演示计划"
                : plan.generated_by}
            </span>
            <span>{plan.approval_state}</span>
          </div>
          <details className="digest">
            <summary>计划指纹与版本记录</summary>
            <code>{plan.plan_digest}</code>
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                void action(async () =>
                  setVersions(
                    (await getPlanVersions(token, task.task_id)).versions,
                  ),
                )
              }
            >
              读取所有版本
            </button>
            {versions?.map((version) => (
              <p key={version.plan_version}>
                v{version.plan_version} · {version.approval_state} ·{" "}
                {version.generated_by}
              </p>
            ))}
          </details>
          {editing ? (
            <PlanEditor
              key={plan.plan_digest}
              plan={plan}
              busy={busy}
              onSave={(value) => decide("modify", value)}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <>
              {plan.details && (
                <div className="plan-details">
                  <h3>{plan.details.title}</h3>
                  <p>{plan.details.objective}</p>
                  <p>检索截止日期：{plan.details.retrieval_cutoff}</p>
                  <h4>纳入条件</h4>
                  <ul>{plan.details.inclusion_criteria.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}</ul>
                </div>
              )}
              <h3>研究子问题</h3>
              <ol className="question-list">
                {plan.sub_questions.map((q, i) => (
                  <li key={i}>{q}{plan.details?.subquestions[i] && (
                    <small> · 优先级：{plan.details.subquestions[i].priority}
                      {" · 证据要求："}{plan.details.subquestions[i].evidence_required}</small>
                  )}</li>
                ))}
              </ol>
              <div className="plan-grid">
                <div>
                  <span>文献范围</span>
                  <strong>
                    {plan.source_scope.min_papers}–
                    {plan.source_scope.max_papers} 篇
                  </strong>
                  <small>{plan.source_scope.providers.join(" / ")}</small>
                </div>
                <div>
                  <span>发表年份</span>
                  <strong>
                    {plan.source_scope.year_from}–{plan.source_scope.year_to}
                  </strong>
                  <small>严格遵守已批准范围</small>
                </div>
                <div>
                  <span>执行预算上限</span>
                  <strong>¥{plan.budget_plan.max_cny.toFixed(2)}</strong>
                  <small>
                    {plan.budget_plan.max_api_calls} 次调用 ·{" "}
                    {plan.budget_plan.max_wall_clock_seconds}s
                  </small>
                </div>
              </div>
              <p className="muted">
                排除项：{plan.exclusions.join("；") || "未设置"}。预算来源：
                {plan.budget_plan.estimate_source}，非实际账单。
              </p>
              {waiting && plan.approval_state === "waiting_approval" && (
                <div className="approval-gate">
                  <h3>第二道确认 · 执行审批</h3>
                  <p>
                    批准仅适用于以上版本及指纹；修改会生成新的待审版本，不会直接运行。
                  </p>
                  <div className="actions">
                    {task.execution_mode === "real" ? (
                      <LiveAuthorization key={plan.plan_digest} token={token}
                        taskId={task.task_id} plan={plan} busy={busy}
                        run={work => void action(work)} />
                    ) : (
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() => decide("approve")}
                    >
                      <Check size={16} /> 批准此版本并执行
                    </button>
                    )}
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => setEditing(true)}
                    >
                      <Pencil size={16} /> 修改计划
                    </button>
                    <button
                      className="danger-button"
                      disabled={busy}
                      onClick={() => decide("reject")}
                    >
                      <X size={16} /> 拒绝执行
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

export function PlanEditor({
  plan,
  busy,
  onSave,
  onCancel,
}: {
  plan: ResearchPlanView;
  busy: boolean;
  onSave: (value: NonNullable<ApprovalPayload["modified_plan"]>) => void;
  onCancel: () => void;
}) {
  const [questions, setQuestions] = useState(plan.sub_questions.join("\n"));
  const [exclusions, setExclusions] = useState(plan.exclusions.join("\n"));
  const [scope, setScope] = useState(plan.source_scope);
  const [budget, setBudget] = useState(plan.budget_plan);
  const [details, setDetails] = useState(plan.details);
  const [criteria, setCriteria] = useState(plan.details?.inclusion_criteria.join("\n") ?? "");
  const split = (value: string) =>
    value
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
  return (
    <form
      className="plan-editor"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          sub_questions: details ? details.subquestions.map((q) => q.question.trim()) : split(questions),
          exclusions: split(exclusions),
          source_scope: scope,
          budget_plan: budget,
          ...(details ? { details: {
            ...details, inclusion_criteria: split(criteria),
            subquestions: details.subquestions.map((q) => ({ ...q, question: q.question.trim() })),
          } } : {}),
        });
      }}
    >
      {details ? <>
        <label htmlFor="plan-title">计划标题</label>
        <input id="plan-title" required value={details.title}
          onChange={(e) => setDetails({ ...details, title: e.target.value })} />
        <label htmlFor="plan-objective">研究目标</label>
        <textarea id="plan-objective" required value={details.objective}
          onChange={(e) => setDetails({ ...details, objective: e.target.value })} />
        <label htmlFor="plan-cutoff">检索截止日期</label>
        <input id="plan-cutoff" type="date" required value={details.retrieval_cutoff}
          min={`${scope.year_to}-01-01`}
          onChange={(e) => setDetails({ ...details, retrieval_cutoff: e.target.value })} />
        <label htmlFor="plan-inclusions">纳入条件（每行一项）</label>
        <textarea id="plan-inclusions" required value={criteria}
          onChange={(e) => setCriteria(e.target.value)} />
        {details.subquestions.map((q, index) => (
          <label key={q.subquestion_id}>
            子问题 {index + 1} · {q.priority} · {q.evidence_required}
            <textarea required value={q.question}
              onChange={(e) => setDetails({ ...details, subquestions:
                details.subquestions.map((item, i) => i === index
                  ? { ...item, question: e.target.value } : item),
              })} />
          </label>
        ))}
      </> : <>
      <label htmlFor="sub-questions">子问题（每行一项）</label>
      <textarea
        id="sub-questions"
        required
        value={questions}
        onChange={(event) => setQuestions(event.target.value)}
        rows={4}
      />
      </>}
      <label htmlFor="exclusions">排除项（每行一项）</label>
      <textarea
        id="exclusions"
        value={exclusions}
        onChange={(event) => setExclusions(event.target.value)}
        rows={2}
      />
      <label htmlFor="providers">来源（逗号分隔）</label>
      <input
        id="providers"
        required
        value={scope.providers.join(",")}
        onChange={(event) =>
          setScope({ ...scope, providers: event.target.value.split(",") })
        }
      />
      <div className="editor-fields">
        {(
          [
            ["year_from", "起始年份", 1900, 2100],
            ["year_to", "截止年份", 1900, 2100],
            ["min_papers", "最少论文", 3, 5],
            ["max_papers", "最多论文", 3, 5],
          ] as const
        ).map(([key, label, min, max]) => (
          <label key={key}>
            {label}
            <input
              type="number"
              required
              min={min}
              max={max}
              step={1}
              value={scope[key]}
              onChange={(event) =>
                setScope({ ...scope, [key]: Number(event.target.value) })
              }
            />
          </label>
        ))}
      </div>
      <div className="editor-fields">
        {(
          [
            ["max_cny", "金额上限 / ¥", 0.01],
            ["max_api_calls", "调用次数上限", 1],
            ["max_wall_clock_seconds", "时长上限 / s", 1],
          ] as const
        ).map(([key, label, step]) => (
          <label key={key}>
            {label}
            <input
              type="number"
              required
              min={0}
              max={plan.budget_plan[key]}
              step={step}
              value={budget[key]}
              onChange={(event) =>
                setBudget({ ...budget, [key]: Number(event.target.value) })
              }
            />
          </label>
        ))}
      </div>
      <p className="muted">
        只允许保持或降低原预算。来源、年份和论文数量将由服务端再次校验。
      </p>
      <div className="actions">
        <button className="primary" disabled={busy || !split(questions).length}>
          保存为新待审版本
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          取消修改
        </button>
      </div>
    </form>
  );
}
