import { useRef, useState } from "react";
import { ArrowRight, FlaskConical, GitBranch, ShieldCheck } from "lucide-react";
import type { TokenProvider } from "../../api/client";
import { createTask } from "../../api/endpoints";
import type { TaskListItem } from "../../api/types";
import { ErrorNotice, messageOf } from "../../components/ui";

export function CreateTask({
  token,
  onCreated,
}: {
  token: TokenProvider;
  onCreated: (id: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<TaskListItem["demo_mode"] | "real">("success");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const attempt = useRef<{ body: string; key: string }>();
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    const body = JSON.stringify([question.trim(), mode]);
    if (attempt.current?.body !== body)
      attempt.current = { body, key: crypto.randomUUID() };
    try {
      const task = await createTask(
        token,
        question.trim(),
        mode,
        attempt.current.key,
      );
      onCreated(task.task_id);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="start-page">
      <div className="hero">
        <span className="eyebrow">FROM QUESTION TO EVIDENCE</span>
        <h1>
          让每一个结论，
          <br />
          <em>都有据可循。</em>
        </h1>
        <p>
          从一个好问题出发，追踪文献、审阅证据，
          <br className="desktop-only" />
          在可控的研究流程中形成清晰的结论。
        </p>
        <div className="hero-orbit" aria-hidden="true">
          <GitBranch size={96} strokeWidth={1} />
        </div>
      </div>
      <form className="panel create-panel" onSubmit={submit}>
        <div className="section-heading">
          <h2>开启一项研究</h2>
          <span className="pill">01 / 提出问题</span>
        </div>
        <label htmlFor="question">你想研究什么？</label>
        <textarea
          id="question"
          rows={4}
          maxLength={2000}
          minLength={3}
          required
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="例如：检索增强生成如何提高学术报告的证据可靠性？"
        />
        <div className="input-hint">
          <span>范围越明确，越容易审阅与追溯。</span>
          <span>{question.length} / 2000</span>
        </div>
        <label htmlFor="execution-profile">执行模式</label>
        <select
          id="execution-profile"
          value={mode}
          onChange={(event) =>
            setMode(event.target.value as TaskListItem["demo_mode"] | "real")
          }
        >
          <option value="success">Demo · 确定性演示</option>
          <option value="degraded">Demo · 降级场景</option>
          <option value="production_unavailable">Demo · 生产门禁场景</option>
          <option value="real">真实研究 · 需要配置与单独授权</option>
        </select>
        <div className="notice">
          <FlaskConical size={18} />
          <span>
            {mode === "real"
              ? "创建任务不会发起研究调用。配置就绪后，需分别确认规划费用、审阅计划并授权执行。"
              : "当前为确定性演示，不调用真实模型。可切换真实研究，逐阶段审阅服务、数据范围和额度。"}
          </span>
        </div>
        <ErrorNotice error={error} />
        <div className="form-footer">
          <span>
            <ShieldCheck size={15} /> 创建后需单独审阅计划
          </span>
          <button
            className="primary"
            disabled={busy || question.trim().length < 3}
          >
            {busy ? "创建中…" : "创建并查看计划"}
            <ArrowRight size={16} />
          </button>
        </div>
      </form>
      <div className="process-guide">
        {[
          ["01", "明确问题", "界定研究范围"],
          ["02", "审阅计划", "确认来源与预算"],
          ["03", "追溯证据", "查看原文与核验边界"],
        ].map(([n, title, detail]) => (
          <div key={n}>
            <span>{n}</span>
            <strong>{title}</strong>
            <p>{detail}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
